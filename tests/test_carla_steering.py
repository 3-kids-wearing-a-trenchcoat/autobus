"""Tests for correcting junction decisions as the bus drives.

The controller holds no CARLA objects -- the traffic manager and the bus are
handed to ``step`` -- so all of it is testable against stubs. That is the
half worth testing: the driving was measured against a live server, but the
decision logic is where a silent off-by-one lived for a month.
"""

from src.simulation.carla_steering import (
    ClosedLoopSteering,
    buffered_junction,
)


class _Location:
    """Stands in for carla.Location."""

    def __init__(self, x: float) -> None:
        self.x = x

    def distance(self, other: "_Location") -> float:
        """Metres between two points, along the only axis there is."""
        return abs(self.x - other.x)


class _Transform:
    """Stands in for carla.Transform."""

    def __init__(self, x: float) -> None:
        self.location = _Location(x)


class _Junction:
    """Stands in for carla.Junction."""

    def __init__(self, id: int) -> None:
        self.id = id


class _Waypoint:
    """
    Stands in for carla.Waypoint, as it appears in the action buffer.

    :param x: Position along the only axis, for the distance ahead.
    :param junction: Junction id, or None for ordinary road.
    """

    def __init__(self, x: float, junction: int | None = None) -> None:
        self.transform = _Transform(x)
        self.is_junction = junction is not None
        self._junction = junction

    def get_junction(self) -> "_Junction | None":
        """Return the junction this point is in, as CARLA does."""
        return None if self._junction is None else _Junction(self._junction)


class _Bus:
    """Stands in for the bus actor."""

    def __init__(self, x: float = 0.0) -> None:
        self._x = x

    def get_location(self) -> _Location:
        """Where the bus is."""
        return _Location(self._x)


class _TrafficManager:
    """
    Stands in for carla.TrafficManager.

    :param buffer: (label, waypoint) pairs it is to report as its lookahead.
    """

    def __init__(self, buffer: list[tuple[str, _Waypoint]]) -> None:
        self.buffer = buffer
        self.issued: list[list[str]] = []

    def get_all_actions(self, bus: _Bus) -> list[tuple[str, _Waypoint]]:
        """Report the lookahead buffer."""
        return self.buffer

    def set_route(self, bus: _Bus, turns: list[str],
                  empty_buffer: bool = True) -> None:
        """Record the turn issued, as the real one would act on it."""
        self.issued.append(list(turns))


def _tm(*entries: tuple[str, float, int | None]) -> _TrafficManager:
    """
    Build a traffic manager whose buffer holds the given entries.

    :param entries: (label, metres ahead, junction id or None) in order.
    :return: The stub traffic manager.
    """
    return _TrafficManager(
        [(label, _Waypoint(x, junction)) for label, x, junction in entries]
    )


# ---------------------------------------------------------------------------
# Reading the buffer
# ---------------------------------------------------------------------------

def test_the_first_buffered_junction_is_found_past_open_road() -> None:
    tm = _tm(("LaneFollow", 5.0, None), ("Right", 24.0, 861))
    assert buffered_junction(tm, _Bus()) == (861, "Right", 24.0)


def test_a_buffer_with_no_junction_reports_nothing() -> None:
    tm = _tm(("LaneFollow", 5.0, None), ("LaneFollow", 12.0, None))
    assert buffered_junction(tm, _Bus()) is None


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def test_a_disagreement_is_corrected() -> None:
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right")])
    tm = _tm(("Straight", 24.0, 861))

    assert steering.step(tm, _Bus()) is None
    assert tm.issued == [["Right"]]
    assert steering.corrections == 1


def test_agreement_issues_nothing() -> None:
    """Silence is the common case; issuing anyway would be churn."""
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right")])
    tm = _tm(("Right", 24.0, 861))

    assert steering.step(tm, _Bus()) is None
    assert tm.issued == []
    assert steering.agreements == 1


def test_a_junction_is_decided_once_while_it_stays_in_the_buffer() -> None:
    """Otherwise every tick of the approach re-issues the same turn."""
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right")])
    tm = _tm(("Straight", 24.0, 861))

    for _ in range(5):
        steering.step(tm, _Bus())
    assert tm.issued == [["Right"]]


def test_lane_follow_is_left_alone() -> None:
    """
    CARLA is the oracle for what counts as a decision point.

    ``LaneFollow`` at a junction means it is spending no entry there, so an
    entry issued against it would be consumed at some later junction --
    which is the original bug, reintroduced.
    """
    steering = ClosedLoopSteering()
    steering.set_plan([(1469, "Right")])
    tm = _tm(("LaneFollow", 3.0, 1469))

    assert steering.step(tm, _Bus()) is None
    assert tm.issued == []


def test_a_junction_off_the_plan_asks_for_a_replan() -> None:
    """The off-route trigger, which comes free with keying by junction."""
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right")])
    tm = _tm(("Right", 20.0, 1205))

    assert steering.step(tm, _Bus()) is not None
    assert tm.issued == []


def test_a_junction_crossed_twice_is_decided_twice() -> None:
    """
    The case a positional list cannot express.

    The plan turns Right at 861 and later Straight at the same junction. It
    must be decided again on the second approach, so the junction already
    acted on is forgotten once it leaves the buffer.
    """
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right"), (20, "Right"), (861, "Straight")])
    bus = _Bus()

    steering.step(_tm(("Straight", 24.0, 861)), bus)   # corrected to Right
    steering.step(_tm(("LaneFollow", 5.0, None)), bus)  # 861 passed
    steering.step(_tm(("Right", 24.0, 20)), bus)        # agreed
    steering.step(_tm(("LaneFollow", 5.0, None)), bus)
    tm = _tm(("Right", 21.0, 861))
    assert steering.step(tm, bus) is None
    assert tm.issued == [["Straight"]]


def test_planning_afresh_forgets_the_junction_last_acted_on() -> None:
    """
    A re-plan may put the bus back at the junction it is standing at.

    Without clearing it, the first junction of the new plan is skipped --
    which is exactly the junction the re-plan was called to get right.
    """
    steering = ClosedLoopSteering()
    steering.set_plan([(861, "Right")])
    steering.step(_tm(("Right", 24.0, 861)), _Bus())

    steering.set_plan([(861, "Left")])
    tm = _tm(("Right", 24.0, 861))
    assert steering.step(tm, _Bus()) is None
    assert tm.issued == [["Left"]]


def test_an_empty_plan_asks_for_a_replan_rather_than_guessing() -> None:
    steering = ClosedLoopSteering()
    steering.set_plan([])
    assert steering.step(_tm(("Right", 20.0, 861)), _Bus()) is not None
