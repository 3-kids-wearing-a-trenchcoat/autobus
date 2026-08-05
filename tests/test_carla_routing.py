"""Tests for reading junction turns off a planned route.

The search itself needs a real CARLA map and is exercised against one in the
tour campaigns recorded in `roadmap.md`. Turn classification is pure
geometry, so it is tested here against stub waypoints -- which is where the
bugs live anyway, because `set_route` consumes the list in order and one
wrong or missing entry throws every later turn onto the wrong junction.
"""

from src.simulation.carla_routing import (
    TURN_THRESHOLD_DEG,
    _heading_change,
    turns_along,
)


class _Rotation:
    """Stands in for carla.Rotation."""

    def __init__(self, yaw: float) -> None:
        self.yaw = yaw


class _Transform:
    """Stands in for carla.Transform."""

    def __init__(self, yaw: float) -> None:
        self.rotation = _Rotation(yaw)


class _Waypoint:
    """
    Stands in for carla.Waypoint.

    :param yaw: Heading in degrees.
    :param is_junction: Whether this point lies inside a junction.
    :param exits: How many continuations ``next()`` offers, which is how a
        real junction advertises that there is a choice to make.
    """

    def __init__(self, yaw: float, is_junction: bool = False,
                 exits: int = 2) -> None:
        self.transform = _Transform(yaw)
        self.is_junction = is_junction
        self._exits = exits

    def next(self, distance: float) -> list["_Waypoint"]:
        """Return as many continuations as this waypoint was given."""
        return [self] * self._exits


def _through_junction(entry_yaw: float, exit_yaw: float,
                      exits: int = 2) -> list[_Waypoint]:
    """
    Build the shortest route that enters and leaves one junction.

    :param entry_yaw: Heading on the approach.
    :param exit_yaw: Heading on the way out.
    :param exits: Continuations offered on the approach.
    :return: Waypoints in travel order.
    """
    return [
        _Waypoint(entry_yaw, is_junction=False, exits=exits),
        _Waypoint(entry_yaw, is_junction=True),
        _Waypoint(exit_yaw, is_junction=False),
    ]


# ---------------------------------------------------------------------------
# Which way did we turn
# ---------------------------------------------------------------------------

def test_carrying_straight_on_is_reported_as_straight() -> None:
    assert turns_along(_through_junction(0.0, 0.0)) == ["Straight"]


def test_a_quarter_turn_clockwise_is_a_right() -> None:
    """CARLA's coordinates are left-handed, so increasing yaw is a right."""
    assert turns_along(_through_junction(0.0, 90.0)) == ["Right"]


def test_a_quarter_turn_anticlockwise_is_a_left() -> None:
    assert turns_along(_through_junction(0.0, -90.0)) == ["Left"]


def test_a_slight_bend_is_not_a_turn() -> None:
    """Roads are not perfectly straight; only real manoeuvres count."""
    gentle = TURN_THRESHOLD_DEG - 5.0
    assert turns_along(_through_junction(0.0, gentle)) == ["Straight"]


# ---------------------------------------------------------------------------
# Angles wrap
# ---------------------------------------------------------------------------

def test_heading_change_wraps_across_the_backwards_direction() -> None:
    """
    170 degrees to -170 is a 20 degree nudge, not a 340 degree spin. Getting
    this wrong turns a straight-on into a phantom turn at every junction
    that happens to sit near the wrap.
    """
    assert _heading_change(170.0, -170.0) == 20.0
    assert _heading_change(-170.0, 170.0) == -20.0


def test_a_turn_that_straddles_the_wrap_is_still_a_turn() -> None:
    assert turns_along(_through_junction(170.0, -100.0)) == ["Right"]


# ---------------------------------------------------------------------------
# Only decisions are reported
# ---------------------------------------------------------------------------

def test_a_junction_with_no_choice_yields_no_instruction() -> None:
    """
    Where the lane has one continuation there is nothing to tell the
    traffic manager. Emitting a turn anyway would be worse than saying
    nothing, because `set_route` consumes the list in order and every later
    turn would then land on the wrong junction.
    """
    assert turns_along(_through_junction(0.0, 90.0, exits=1)) == []


def test_a_route_with_no_junctions_yields_no_turns() -> None:
    straight = [_Waypoint(0.0) for _ in range(5)]
    assert turns_along(straight) == []


def test_turns_come_back_in_the_order_they_must_be_made() -> None:
    route = (
        _through_junction(0.0, 90.0)
        + _through_junction(90.0, 90.0)[1:]
        + _through_junction(90.0, 0.0)[1:]
    )
    assert turns_along(route) == ["Right", "Straight", "Left"]


def test_an_empty_route_is_handled() -> None:
    assert turns_along([]) == []
