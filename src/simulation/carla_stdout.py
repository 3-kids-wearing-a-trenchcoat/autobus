"""Capturing what the traffic manager prints, and saying where it happened.

CARLA's traffic manager runs inside the **client** process — the string
``"We couldn't find the RoadOption you were looking for"`` lives in the pip
wheel's ``libcarla`` shared object, not on the server — and it is written
with C++ ``std::cout``, which reaches file descriptor 1 without passing
through ``sys.stdout``. So ``contextlib.redirect_stdout`` cannot see it at
all: the capture has to happen at the descriptor.

Two things make the raw output useless, and this module fixes both.

**It is unattributable.** The message names no vehicle, no leg and no
position, and a campaign emits hundreds of them. Every line matching a known
message is re-emitted with a caller-supplied tag in front of it, so it can be
tied to the town, the leg and the piece of road the bus was on.

**It is block-buffered.** glibc gives ``stdout`` a 4 KB buffer whenever fd 1
is not a terminal, which under ``conda run`` and Jupyter it never is. Without
intervention the messages therefore surface in clumps of roughly forty, long
after the tick that caused them and in the wrong order relative to anything
Python printed — which is why the campaign's saved output cannot say which
town a message came from. ``setvbuf`` puts the stream in unbuffered mode so
each line arrives as it is written.

Nothing is dropped: lines matching no known message are passed through
untouched, so this stays a tap rather than a filter.

**This module hijacks a process-wide resource.** ``install`` is idempotent
and ``restore`` is registered with ``atexit``, because a trial that dies
part-way must not leave the process without a stdout.
"""

import atexit
import ctypes
import os
import select
from collections.abc import Callable

# Substrings identifying the messages worth attributing, keyed by the
# counter name each is reported under. Matched as substrings because CARLA's
# exact wording is not a stable interface -- only the distinctive middle of
# the sentence is worth depending on.
MESSAGE_PATTERNS: dict[str, str] = {
    "route_drops": "couldn't find the RoadOption",
}

# How much to take from the pipe in a single read.
_READ_CHUNK = 65536

# _IONBF from stdio.h: no buffering, so every write reaches the pipe.
_UNBUFFERED = 2

_libc = ctypes.CDLL("libc.so.6")
_libc.setvbuf.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_size_t]

# fd 1 as it was before the hijack, and the read end of the pipe now
# standing in for it. Both None while not installed.
_saved_fd: int | None = None
_read_fd: int | None = None

# A read can land mid-line; the tail waits here for its newline.
_residual = b""


def install() -> None:
    """
    Route fd 1 through a pipe this module can read, and stop it buffering.

    Idempotent, because a campaign builds one backend per trial while fd 1
    is a process-wide resource that must be taken over exactly once.
    :return: None.
    """
    global _saved_fd, _read_fd
    if _saved_fd is not None:
        return

    _libc.fflush(None)
    _saved_fd = os.dup(1)
    _read_fd, write_fd = os.pipe()
    os.set_blocking(_read_fd, False)
    os.dup2(write_fd, 1)
    os.close(write_fd)

    # Must come after the dup2. glibc picks a stream's buffering mode from
    # what fd 1 was when the stream was first used, and does not revisit it
    # when the descriptor is replaced underneath.
    _libc.setvbuf(ctypes.c_void_p.in_dll(_libc, "stdout"), None,
                  _UNBUFFERED, 0)
    atexit.register(restore)


def restore() -> None:
    """
    Put fd 1 back, emitting anything still in flight.

    Safe to call when not installed, and safe to call twice.
    :return: None.
    """
    global _saved_fd, _read_fd, _residual
    if _saved_fd is None:
        return

    _libc.fflush(None)
    drain(lambda: "closing")
    if _residual:
        _emit(_residual.decode("utf-8", errors="replace"))
        _residual = b""

    os.dup2(_saved_fd, 1)
    os.close(_saved_fd)
    os.close(_read_fd)
    _saved_fd = _read_fd = None


def drain(tag_for: Callable[[], str]) -> dict[str, int]:
    """
    Pass on whatever has been printed, labelling the messages we know.

    :param tag_for: Called at most once per drain, and only when a known
        message actually arrived, to describe where the bus was. Deferred
        rather than passed as a string because building the description
        costs a map query and this runs on every tick.
    :return: How many of each known message arrived, by counter name. Empty
        when nothing did, which is the overwhelmingly common case.
    """
    global _residual
    if _read_fd is None:
        return {}

    _residual += _read_available()
    lines = _residual.split(b"\n")
    _residual = lines.pop()

    hits: dict[str, int] = {}
    tag: str | None = None
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        name = _matched(line)
        if name is None:
            _emit(line)
            continue
        hits[name] = hits.get(name, 0) + 1
        if tag is None:
            tag = _describe(tag_for)
        _emit(f"[{tag}] {line}")
    return hits


def _read_available() -> bytes:
    """
    Take everything the pipe holds without ever waiting on it.

    :return: Bytes read, empty when the pipe is quiet.
    """
    chunks: list[bytes] = []
    while select.select([_read_fd], [], [], 0)[0]:
        chunk = os.read(_read_fd, _READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _matched(line: str) -> str | None:
    """
    Identify a line as one of the messages worth attributing.

    :param line: One line of captured output.
    :return: The counter name it belongs to, or None if it is not ours.
    """
    for name, pattern in MESSAGE_PATTERNS.items():
        if pattern in line:
            return name
    return None


def _describe(tag_for: Callable[[], str]) -> str:
    """
    Ask the caller where the bus is, without ever failing the run for it.

    A diagnostic that can abort a trial is worse than no diagnostic, and the
    callers here touch live CARLA actors, which can vanish mid-tick.
    :param tag_for: The caller's description callback.
    :return: The description, or a note of why there is not one.
    """
    try:
        return tag_for()
    except Exception as error:              # noqa: BLE001
        return f"context unavailable: {error!r}"


def _emit(text: str) -> None:
    """
    Write one line to the real stdout, behind the capture.

    :param text: Line to write, without its newline.
    :return: None.
    """
    os.write(_saved_fd, (text + "\n").encode("utf-8", errors="replace"))
