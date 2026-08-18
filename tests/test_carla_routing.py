"""Tests for planning a drivable route through CARLA's lanes.

The search needs a real CARLA map and is exercised against one in the tour
campaigns recorded in `roadmap.md`. What is tested here against stub
waypoints is the part that is pure bookkeeping -- lane quantisation and the
cost the search minimises -- which is where the bugs live anyway.
"""

from src.simulation.carla_routing import _lane_key, plan_route



# ---------------------------------------------------------------------------
# The search survives floating-point drift along a lane
# ---------------------------------------------------------------------------

class _Point:
    """Stands in for carla.Location, for the search."""

    def __init__(self, x: float) -> None:
        self.x = x

    def distance(self, other: "_Point") -> float:
        """Distance along a straight lane."""
        return abs(self.x - other.x)


class _SearchTransform:
    """Stands in for carla.Transform, carrying a location."""

    def __init__(self, x: float) -> None:
        self.location = _Point(x)


class _LaneWaypoint:
    """
    Stands in for carla.Waypoint on a single straight lane.

    :param s: Distance along the lane, as CARLA reports it -- including the
        error `next()` accumulates.
    """

    def __init__(self, s: float, road_id: int = 1) -> None:
        self.s = s
        self.road_id = road_id
        self.lane_id = -2
        self.is_junction = False
        self.transform = _SearchTransform(s)
        self.following: list["_LaneWaypoint"] = []

    def next(self, distance: float) -> list["_LaneWaypoint"]:
        """Return the single continuation of this lane."""
        return list(self.following)


class _StubMap:
    """A map with one lane, answering `get_waypoint` by nearest node."""

    def __init__(self, lane: list["_LaneWaypoint"]) -> None:
        self._lane = lane

    def get_waypoint(self, location, lane_type=None) -> "_LaneWaypoint":
        """Snap a location to the nearest waypoint on the lane."""
        return min(self._lane,
                   key=lambda w: abs(w.transform.location.x - location.x))


def _drifting_lane() -> list[_LaneWaypoint]:
    """
    One lane whose `s` drifts a hair below each 2 m mark, as CARLA's does.

    The drift is what makes the last waypoint quantise into its own
    predecessor's bucket when the key is truncated rather than rounded.
    """
    lane = [_LaneWaypoint(18.0), _LaneWaypoint(20.0), _LaneWaypoint(22.0),
            _LaneWaypoint(24.0), _LaneWaypoint(26.0), _LaneWaypoint(28.0),
            _LaneWaypoint(29.999995), _LaneWaypoint(31.999995),
            _LaneWaypoint(33.999995), _LaneWaypoint(35.999995),
            _LaneWaypoint(37.999995), _LaneWaypoint(39.999995)]
    for current, following in zip(lane, lane[1:]):
        current.following = [following]
    return lane


def test_a_lane_is_followed_across_a_quantisation_boundary() -> None:
    """
    A waypoint landing a micrometre below a bucket boundary must not be
    mistaken for its own predecessor. Truncating the key did exactly that,
    and on a lane with one continuation it emptied the frontier and
    reported no route along a completely clear road -- measured on Town03,
    where a search from s=18.0 died after six nodes while the same search
    from s=20.0 explored 6,747 and arrived.
    """
    lane = _drifting_lane()
    route = plan_route(_StubMap(lane), lane[0].transform.location,
                       lane[-1].transform.location)

    assert route is not None
    assert route[0].s == lane[0].s
    # The point of the test: the search got *past* the drifting waypoint
    # rather than mistaking it for the one before and giving up.
    assert 29.999995 in [waypoint.s for waypoint in route]


def test_the_drifting_waypoint_gets_its_own_key() -> None:
    """The specific collision, pinned at the level it happens."""
    lane = _drifting_lane()
    assert _lane_key(lane[5]) != _lane_key(lane[6])


# ---------------------------------------------------------------------------
# What the search minimises: distance, or travel time
# ---------------------------------------------------------------------------

class _BranchWaypoint:
    """
    Stands in for carla.Waypoint on one of two branches to the same place.

    Position and lane distance are kept apart here, unlike ``_LaneWaypoint``:
    the two branches run alongside each other, so they share ``s`` values
    while sitting at different points in the world.

    :param x: Where the waypoint is, for the arrival test.
    :param s: Distance along its own lane, which keys the search.
    :param road_id: OpenDRIVE road this waypoint belongs to.
    """

    def __init__(self, x: float, s: float, road_id: int) -> None:
        self.s = s
        self.road_id = road_id
        self.lane_id = -1
        self.is_junction = False
        self.transform = _SearchTransform(x)
        self.following: list["_BranchWaypoint"] = []

    def next(self, distance: float) -> list["_BranchWaypoint"]:
        """Return every continuation this waypoint was given."""
        return list(self.following)


def _forked_map() -> tuple["_StubMap", _BranchWaypoint, _BranchWaypoint]:
    """
    Build a start and a goal joined by a short slow road and a long fast one.

    Branch A (road 2) is 4 steps; branch B (road 3) is 8. Counting steps
    therefore picks A. Priced at 30 km/h against 90, A costs 0.96 s and B
    0.64 s, so pricing time picks B. That divergence is the whole point:
    it is what makes ``plan_route`` and ``TransitGraph`` disagree on 26% of
    Town01's stop pairs.
    :return: The map, its start waypoint and its goal waypoint.
    """
    start = _BranchWaypoint(0.0, 0.0, road_id=1)
    goal = _BranchWaypoint(100.0, 0.0, road_id=9)

    slow = [_BranchWaypoint(2.0 * i, 2.0 * i, road_id=2)
            for i in range(1, 5)]
    fast = [_BranchWaypoint(-2.0 * i, 2.0 * i, road_id=3)
            for i in range(1, 9)]

    for chain in (slow, fast):
        start.following.append(chain[0])
        for current, following in zip(chain, chain[1:]):
            current.following = [following]
        chain[-1].following = [goal]

    every = [start, goal, *slow, *fast]
    return _StubMap(every), start, goal


def _road_ids(route: list[_BranchWaypoint]) -> set[int]:
    """Roads a route passes along."""
    return {waypoint.road_id for waypoint in route}


def test_without_speed_limits_the_search_minimises_distance() -> None:
    """
    The untimed fallback must behave exactly as the old breadth-first search
    did, so every run recorded before travel time was priced reproduces.
    """
    stub_map, start, goal = _forked_map()

    route = plan_route(stub_map, start.transform.location,
                       goal.transform.location)

    assert route is not None
    assert 2 in _road_ids(route), "should take the shorter road"
    assert 3 not in _road_ids(route)


def test_with_speed_limits_the_search_minimises_travel_time() -> None:
    """
    Given the limits, the longer road wins because it is quicker -- which is
    the route TransitGraph priced and the bus previously did not drive.
    """
    stub_map, start, goal = _forked_map()
    limits = {1: 30.0, 2: 30.0, 3: 90.0, 9: 30.0}

    route = plan_route(stub_map, start.transform.location,
                       goal.transform.location, limits.get)

    assert route is not None
    assert 3 in _road_ids(route), "should take the quicker road"
    assert 2 not in _road_ids(route)


def test_a_uniform_speed_limit_changes_nothing() -> None:
    """
    Pricing time only diverges from counting steps where limits differ. With
    one limit everywhere the two must agree, which is why Town02 and Town03
    barely move while Town01 moves a great deal.
    """
    stub_map, start, goal = _forked_map()

    by_distance = plan_route(stub_map, start.transform.location,
                             goal.transform.location)
    by_time = plan_route(stub_map, start.transform.location,
                         goal.transform.location, lambda road_id: 30.0)

    assert by_distance is not None and by_time is not None
    assert _road_ids(by_distance) == _road_ids(by_time)
