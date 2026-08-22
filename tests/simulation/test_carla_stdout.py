"""Tests for tagging the traffic manager's diagnostics.

No CARLA here. What this module does is entirely about file descriptors, so
the messages are written by hand -- including one written through C's
``stdout`` rather than Python's, which is the path libcarla actually uses and
the only one that exercises the buffering fix.

Each test stands its own pipe in for the real stdout before installing the
capture, so what would have reached the terminal can be read back and
asserted on.

``capturing`` is a context manager rather than a fixture, and that is not a
style choice: pytest's own capture re-points fd 1 at its temporary file
between the setup and call phases, so a capture installed in a fixture is
silently undone before the test body runs.
"""

import ctypes
import os
import select
from collections.abc import Iterator
from contextlib import contextmanager

from src.simulation import carla_stdout

MESSAGE = ("We couldn't find the RoadOption you were looking for. This "
           "route might diverge from the one expected.")

_libc = ctypes.CDLL("libc.so.6")


class _Terminal:
    """
    Stands in for the real stdout, so the tap's output can be read back.

    :param read_fd: Read end of the pipe now behind fd 1.
    """

    def __init__(self, read_fd: int) -> None:
        self._read_fd = read_fd

    def read(self) -> str:
        """
        Take everything written to the far side of the capture so far.

        :return: What was written, decoded.
        """
        chunks = []
        while select.select([self._read_fd], [], [], 0.05)[0]:
            chunk = os.read(self._read_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode()


@contextmanager
def capturing() -> Iterator[_Terminal]:
    """
    Install the capture over a readable stand-in for the terminal.

    :return: The stand-in terminal, for reading back what got through.
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    real_stdout = os.dup(1)
    os.dup2(write_fd, 1)
    os.close(write_fd)

    carla_stdout.install()
    try:
        yield _Terminal(read_fd)
    finally:
        carla_stdout.restore()
        os.dup2(real_stdout, 1)
        os.close(real_stdout)
        os.close(read_fd)


def _write(text: str) -> None:
    """Write straight to fd 1, the way a C++ library does."""
    os.write(1, text.encode())


def test_a_known_message_is_tagged_and_counted() -> None:
    with capturing() as terminal:
        _write(MESSAGE + "\n")

        hits = carla_stdout.drain(lambda: "Town03 seed=7 junction=True")

        assert hits == {"route_drops": 1}
        assert terminal.read() == f"[Town03 seed=7 junction=True] {MESSAGE}\n"


def test_unknown_output_passes_through_untouched() -> None:
    with capturing() as terminal:
        _write("something else entirely\n")

        hits = carla_stdout.drain(lambda: "TAG")

        assert hits == {}
        assert terminal.read() == "something else entirely\n"


def test_a_partial_line_waits_for_its_newline() -> None:
    with capturing() as terminal:
        _write(MESSAGE[:20])

        assert carla_stdout.drain(lambda: "TAG") == {}
        assert terminal.read() == ""

        _write(MESSAGE[20:] + "\n")

        assert carla_stdout.drain(lambda: "TAG") == {"route_drops": 1}
        assert terminal.read() == f"[TAG] {MESSAGE}\n"


def test_the_tag_is_built_only_when_a_message_arrives() -> None:
    with capturing() as terminal:
        calls = []

        def tag_for() -> str:
            calls.append(1)
            return "TAG"

        carla_stdout.drain(tag_for)                 # nothing pending at all
        _write("unrelated chatter\n")
        carla_stdout.drain(tag_for)                 # pending, but not ours
        assert calls == []

        _write(f"{MESSAGE}\n{MESSAGE}\n")
        assert carla_stdout.drain(tag_for) == {"route_drops": 2}
        # Once per drain, not once per message: the bus has not moved between
        # two messages read from the same tick.
        assert len(calls) == 1


def test_a_failing_tag_never_costs_the_message() -> None:
    with capturing() as terminal:
        def tag_for() -> str:
            raise RuntimeError("actor gone")

        _write(MESSAGE + "\n")
        hits = carla_stdout.drain(tag_for)

        assert hits == {"route_drops": 1}
        output = terminal.read()
        assert "context unavailable" in output
        assert MESSAGE in output


def test_c_stdout_is_not_left_block_buffered() -> None:
    """The reason for setvbuf: libcarla writes through C, not Python."""
    with capturing() as terminal:
        # No flush, exactly as libcarla leaves it.
        _libc.printf(b"%s\n", MESSAGE.encode())

        assert carla_stdout.drain(lambda: "TAG") == {"route_drops": 1}
        assert terminal.read() == f"[TAG] {MESSAGE}\n"


def test_restoring_hands_the_descriptor_back() -> None:
    with capturing() as terminal:
        _write("still in flight")                   # no newline, still held
        carla_stdout.restore()

        # The residual is not lost, and fd 1 now reaches the terminal directly.
        _write("\nafter restore\n")
        assert terminal.read() == "still in flight\n\nafter restore\n"
        assert carla_stdout.drain(lambda: "TAG") == {}


def test_installing_twice_is_harmless() -> None:
    with capturing() as terminal:
        carla_stdout.install()
        _write(MESSAGE + "\n")

        assert carla_stdout.drain(lambda: "TAG") == {"route_drops": 1}
        assert terminal.read() == f"[TAG] {MESSAGE}\n"


@contextmanager
def listening() -> Iterator[list[tuple[str, str]]]:
    """
    Register a recording listener, and take it away again afterwards.

    Removal is in a ``finally`` because the listener list is module state:
    one left behind would be fed by every later test in the session.
    :return: The list the listener appends to.
    """
    seen: list[tuple[str, str]] = []

    def listener(name: str, line: str) -> None:
        """Record one attributed message."""
        seen.append((name, line))

    carla_stdout.add_listener(listener)
    try:
        yield seen
    finally:
        carla_stdout.remove_listener(listener)


def test_a_listener_is_shown_the_tagged_message() -> None:
    with capturing(), listening() as seen:
        _write(MESSAGE + "\n")

        carla_stdout.drain(lambda: "Town05 seed=3 lane=-2")

        # The counter name, and the line exactly as a reader would see it.
        assert seen == [("route_drops",
                         f"[Town05 seed=3 lane=-2] {MESSAGE}")]


def test_a_listener_is_not_shown_pass_through_output() -> None:
    """Unattributed lines carry no context, so they are not a listener's."""
    with capturing(), listening() as seen:
        _write("something else entirely\n")

        carla_stdout.drain(lambda: "TAG")

        assert seen == []


def test_registering_the_same_listener_twice_shows_it_once() -> None:
    """A notebook cell that registers a listener stays safe to re-run."""
    with capturing(), listening() as seen:
        carla_stdout.add_listener(carla_stdout._listeners[0])
        _write(MESSAGE + "\n")

        carla_stdout.drain(lambda: "TAG")

        assert len(seen) == 1


def test_a_failing_listener_never_costs_the_message() -> None:
    """Same rule as a failing tag: a diagnostic must not abort a trial."""
    def explode(name: str, line: str) -> None:
        """Fail the way a half-written notebook listener would."""
        raise RuntimeError("bad listener")

    carla_stdout.add_listener(explode)
    try:
        with capturing() as terminal, listening() as seen:
            _write(MESSAGE + "\n")

            hits = carla_stdout.drain(lambda: "TAG")

            assert hits == {"route_drops": 1}
            # The healthy listener is still served, and the message still
            # reaches the terminal with the complaint beside it.
            assert len(seen) == 1
            output = terminal.read()
            assert MESSAGE in output
            assert "bad listener" in output
    finally:
        carla_stdout.remove_listener(explode)


def test_a_removed_listener_hears_nothing_more() -> None:
    with capturing(), listening() as seen:
        _write(MESSAGE + "\n")
        carla_stdout.drain(lambda: "TAG")
        assert len(seen) == 1

        carla_stdout.remove_listener(carla_stdout._listeners[0])
        _write(MESSAGE + "\n")
        carla_stdout.drain(lambda: "TAG")

        assert len(seen) == 1


def test_removing_a_listener_that_was_never_added_is_harmless() -> None:
    """Teardown never has to know whether setup got that far."""
    carla_stdout.remove_listener(lambda name, line: None)
