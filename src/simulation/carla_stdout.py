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

**Reading the messages from code.** ``drain`` reports how many arrived but
not what they said, because its caller counts them and the text is only ever
meant for a human. A diagnostic that wants the text — a notebook picking
apart one bad run — registers a callback with ``add_listener``. Listeners see
attributed messages only; pass-through output is not this module's to
describe.

**This module hijacks a process-wide resource.** ``install`` is idempotent
and ``restore`` is registered with ``atexit``, because a trial that dies
part-way must not leave the process without a stdout.

**It is glibc-only, and says so rather than failing.** Every technique here
-- the ``libc.so.6`` handle, the ``setvbuf`` on glibc's exported ``stdout``,
the non-blocking ``select`` over a pipe -- is POSIX and specifically glibc.
None of it ports to Windows, where ``select`` takes sockets alone. So
``available`` reports whether the tap can work and ``install`` quietly does
nothing when it cannot, because this is a **diagnostic**: losing it costs
attribution on a message CARLA prints anyway, and that is never worth
refusing to drive a bus over. Callers must not read a zero from an
un-installed tap as a measurement -- see ``available``.
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

def _load_libc() -> ctypes.CDLL | None:
    """
    Open the C library this module drives fd 1 through, if there is one.

    Everything here is glibc-specific, and deliberately so: the buffering
    problem it solves is glibc's. Windows has no ``libc.so.6``, and renaming
    it would not help -- the UCRT keeps ``stdout`` behind
    ``__acrt_iob_func()`` rather than exporting it as data, so ``in_dll``
    has nothing to find. The symbol is therefore probed here rather than at
    the point of use, so "can this platform be tapped" is one question
    answered once, at import, instead of an exception from inside a trial.
    :return: The library, or None where the capture cannot work.
    """
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.setvbuf.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                 ctypes.c_int, ctypes.c_size_t]
        ctypes.c_void_p.in_dll(libc, "stdout")
    except (OSError, ValueError):
        return None
    return libc


_libc = _load_libc()

# fd 1 as it was before the hijack, and the read end of the pipe now
# standing in for it. Both None while not installed.
_saved_fd: int | None = None
_read_fd: int | None = None

# A read can land mid-line; the tail waits here for its newline.
_residual = b""

# Called with every message this module attributes. Module-level because the
# capture it observes is process-wide: a consumer registers once and sees
# every trial the process goes on to run.
Listener = Callable[[str, str], None]
_listeners: list[Listener] = []


def available() -> bool:
    """
    Say whether this platform can be tapped at all.

    Callers need this to tell "measured, and there were none" apart from
    "never measured": a counter that reads zero on a platform where nothing
    was ever counted is a false statement about the run, not a missing
    convenience.
    :return: True where ``install`` will actually capture fd 1.
    """
    return _libc is not None


def install() -> None:
    """
    Route fd 1 through a pipe this module can read, and stop it buffering.

    Idempotent, because a campaign builds one backend per trial while fd 1
    is a process-wide resource that must be taken over exactly once. Where
    the platform cannot be tapped this does nothing at all, leaving the
    module in its uninstalled state, which ``drain`` and ``restore`` already
    handle -- the messages then reach stdout untagged, which is what would
    have happened anyway had this module never existed.
    :return: None.
    """
    global _saved_fd, _read_fd
    if _libc is None or _saved_fd is not None:
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


def add_listener(listener: Listener) -> None:
    """
    Ask to be shown every message this module attributes.

    The listener receives the counter name the line was recognised as and
    the tagged line exactly as it reached stdout, so it sees what a reader
    sees. Only known messages are passed on — pass-through output carries no
    context and is not this module's to describe.

    Registering the same listener twice does nothing, so a notebook cell
    that registers one stays safe to re-run.
    :param listener: Called as ``listener(counter_name, tagged_line)``.
    :return: None.
    """
    if listener not in _listeners:
        _listeners.append(listener)


def remove_listener(listener: Listener) -> None:
    """
    Stop showing attributed messages to a listener.

    :param listener: A previously registered listener. One that was never
        registered is ignored rather than raising, so teardown never has to
        know whether setup got that far.
    :return: None.
    """
    if listener in _listeners:
        _listeners.remove(listener)


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
        tagged = f"[{tag}] {line}"
        _emit(tagged)
        _notify(name, tagged)
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


def _notify(name: str, tagged: str) -> None:
    """
    Show one attributed message to every registered listener.

    Failures are swallowed for the reason ``_describe`` swallows them: a
    diagnostic that can abort a trial is worse than no diagnostic. The
    complaint goes straight to the descriptor rather than through ``_emit``,
    which keeps a broken listener from being handed its own error message.
    Called only from ``drain``, so listeners never see the residual line
    ``restore`` flushes on the way out.
    :param name: Counter name the message was recognised as.
    :param tagged: The line as emitted, tag included.
    :return: None.
    """
    for listener in list(_listeners):
        try:
            listener(name, tagged)
        except Exception as error:          # noqa: BLE001
            os.write(_saved_fd, f"carla_stdout listener {listener!r} raised "
                                f"{error!r}\n".encode("utf-8", "replace"))


def _emit(text: str) -> None:
    """
    Write one line to the real stdout, behind the capture.

    :param text: Line to write, without its newline.
    :return: None.
    """
    os.write(_saved_fd, (text + "\n").encode("utf-8", errors="replace"))
