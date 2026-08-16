"""Working out which way to turn to reach a stop in CARLA.

`TrafficManager.set_path` looks like it should take a destination, but it is
a junction-decision API: given one distant point it can only choose greedily
at each junction, and on a grid that cycles. Measured on Town01, a bus told
only where its stop was drove a ten-road loop indefinitely and never once
entered the road the stop was on.

`set_route` is the honest interface — it takes the turn to make at each
junction in order — and this module works out that list. It plans a route
along lanes, then reads off the turns.

CARLA ships an equivalent, `agents.navigation.GlobalRoutePlanner`, but the
pip wheel is client bindings only (`libcarla.so` plus stubs) and does not
include it; it exists only in CARLA's source tree and container image. Since
it is ordinary Python over the same public API, and using it would mean
copying ~1200 lines into this repository, the search is done here instead.
It was validated against that implementation while it was written.

**This module imports carla**, like `CarlaBackend` and unlike everything
else under `src/simulation`. It deliberately knows nothing about the transit
graph or bus stops: it takes two world locations and answers with turns.
"""

from collections import deque
from typing import NamedTuple

import carla

# How far apart successive waypoints are while searching. Small enough to
# resolve short junction links, large enough that a whole town stays a few
# thousand nodes.
STEP_M = 2.0

# How close a search node must come to the destination to count as arrival.
GOAL_TOLERANCE_M = 4.0

# Heading change across a junction, in degrees, beyond which the manoeuvre
# is a turn rather than going straight on. **CARLA's own value**, from
# `Constants.h`: `static float const STRAIGHT_DEG = 19.0f;`.
#
# It was 35.0 until 2026-08-16, measured over the approach-to-exit span
# rather than the junction interior. Naming has to agree with CARLA's,
# because closed-loop steering hands CARLA the word and compares it against
# the label CARLA reports — a manoeuvre we call `Straight` and CARLA calls
# `Right` is a turn we then ask for and CARLA cannot supply.
STRAIGHT_DEG = 19.0

# CARLA's topology resolution, from `Constants.h`. Its junction interiors
# are sampled this coarsely, ours at ``STEP_M``; the two therefore pick
# slightly different first and last waypoints inside a junction. Recorded
# here because it bounds how exactly the naming can be matched.
CARLA_MAP_RESOLUTION_M = 5.0

# Guard against pathological searches on a large map.
MAX_SEARCH_NODES = 200000

# Landmark types CARLA accepts as evidence that a junction is a real one:
# traffic light, stop sign, yield sign. From `InMemoryMap.cpp`, which will
# not label a non-branching junction approach without one of them.
SIGNAL_LANDMARK_TYPES = frozenset({"1000001", "206", "205"})

# How far ahead CARLA looks for that landmark. Its own figure; changing it
# stops this matching what the traffic manager does.
SIGNAL_RANGE_M = 15.0


def _lane_key(waypoint: "carla.Waypoint") -> tuple[int, int, int]:
    """
    Identify a waypoint coarsely enough that the search terminates.

    Two waypoints on the same lane a few centimetres apart are the same
    place for routing purposes; without quantising, `next()` returns
    slightly different objects for ever and the frontier never closes.

    Rounded rather than truncated, and that is load-bearing. `next()`
    accumulates a little floating-point error at every step, so a waypoint
    that should sit at ``s = 30.0`` arrives as ``29.999995``. Truncating
    drops it into the *previous* bucket, where it collides with its own
    predecessor, is pruned as already-seen, and — on a lane with a single
    continuation — empties the frontier and reports no route along a clear
    road. Measured on Town03: a search from ``s = 18.0`` died after six
    nodes while the same search from ``s = 20.0`` explored 6,747 and
    arrived. Rounding tolerates drift up to half a bucket, a metre here,
    against the micrometres actually seen.
    :param waypoint: Waypoint to identify.
    :return: Road, lane, and quantised distance along the lane.
    """
    return (waypoint.road_id, waypoint.lane_id, round(waypoint.s / STEP_M))


def plan_route(
    carla_map: "carla.Map",
    origin: "carla.Location",
    destination: "carla.Location",
) -> list["carla.Waypoint"] | None:
    """
    Find a driveable path along lanes from one location to another.

    Breadth-first over `waypoint.next()`, which follows lane direction and
    expands every branch of a junction, so the result respects one-way
    roads. Steps are a fixed length, so fewest steps is also shortest
    distance.
    :param carla_map: Map to route over.
    :param origin: Where the vehicle is now.
    :param destination: Where it should end up.
    :return: Waypoints from origin to destination, or None if unreachable.
    """
    start = carla_map.get_waypoint(
        origin, lane_type=carla.LaneType.Driving
    )
    goal = carla_map.get_waypoint(
        destination, lane_type=carla.LaneType.Driving
    )
    goal_lane = (goal.road_id, goal.lane_id)
    goal_location = goal.transform.location

    frontier = deque([start])
    came_from: dict[tuple[int, int, int], tuple[int, int, int] | None] = {
        _lane_key(start): None
    }
    seen_at = {_lane_key(start): start}

    while frontier and len(came_from) < MAX_SEARCH_NODES:
        current = frontier.popleft()
        arrived = (
            (current.road_id, current.lane_id) == goal_lane
            and current.transform.location.distance(goal_location)
            < GOAL_TOLERANCE_M
        )
        if arrived:
            return _reconstruct(came_from, seen_at, _lane_key(current))

        for following in current.next(STEP_M):
            key = _lane_key(following)
            if key in came_from:
                continue
            came_from[key] = _lane_key(current)
            seen_at[key] = following
            frontier.append(following)
    return None


def _reconstruct(came_from, seen_at, key) -> list["carla.Waypoint"]:
    """
    Walk the search tree back to the start.

    :param came_from: Each node's predecessor.
    :param seen_at: The waypoint each node stands for.
    :param key: Node to walk back from.
    :return: Waypoints in travel order.
    """
    path = []
    while key is not None:
        path.append(seen_at[key])
        key = came_from[key]
    path.reverse()
    return path


class Crossing(NamedTuple):
    """
    One junction a route passes through, and the waypoints that describe it.

    :param approach: Last waypoint before the junction. What ``_is_labelled``
        interrogates, and the only part of a crossing that is not geometry.
    :param first_inside: First waypoint inside the junction.
    :param last_inside: Last waypoint inside it. With ``first_inside`` this
        is the span CARLA measures the manoeuvre over.
    :param junction_id: Id of the junction, or None if CARLA does not name
        one for it.
    """

    approach: "carla.Waypoint"
    first_inside: "carla.Waypoint"
    last_inside: "carla.Waypoint"
    junction_id: int | None


def _crossings(route: list["carla.Waypoint"]) -> list[Crossing]:
    """
    Walk a route and pick out every junction it passes through.

    Single source of truth for both readers below. They differ only in what
    they do with a crossing — never in where they think the junction starts
    and ends — so they cannot drift apart and name the same corner
    differently.
    :param route: Waypoints in travel order, as from ``plan_route``.
    :return: One entry per junction crossed, in travel order.
    """
    found: list[Crossing] = []
    approach: "carla.Waypoint | None" = None
    inside: list["carla.Waypoint"] = []

    for previous, waypoint in zip(route, route[1:]):
        if waypoint.is_junction and not previous.is_junction:
            approach = previous
            inside = [waypoint]
        elif waypoint.is_junction and inside:
            inside.append(waypoint)
        elif previous.is_junction and not waypoint.is_junction and inside:
            junction = inside[0].get_junction()
            found.append(Crossing(
                approach=approach,
                first_inside=inside[0],
                last_inside=inside[-1],
                junction_id=None if junction is None else int(junction.id),
            ))
            approach, inside = None, []
    return found


def _heading_change(before: float, after: float) -> float:
    """
    Signed heading change in degrees, wrapped to (-180, 180].

    :param before: Yaw on entering the junction.
    :param after: Yaw on leaving it.
    :return: Change in yaw; positive is a right turn in CARLA's left-handed
        coordinates.
    """
    return (after - before + 180.0) % 360.0 - 180.0


def _turn_name(first_inside_yaw: float, last_inside_yaw: float) -> str:
    """
    Name a junction manoeuvre exactly as CARLA names it.

    Takes CARLA's **threshold** and CARLA's **span**, but not CARLA's raw
    arithmetic. `InMemoryMap.cpp` reads::

        int16_t diff_angle = (junction_end_angle - current_angle) % 360;
        bool straight = (diff_angle < STRAIGHT_DEG && diff_angle > -STRAIGHT_DEG);
        bool right    = (diff_angle >= STRAIGHT_DEG && diff_angle <= 180) ||
                        (diff_angle <= -180 && diff_angle >= -360+STRAIGHT_DEG);

    ⚠ **Transcribing that literally was tried on 2026-08-16 and is wrong
    here.** Its ``% 360`` only rescues magnitudes above 360, so a raw
    difference of −358 — which is a 2° bend across the yaw wrap — falls
    outside both the straight band and the right band and drops through to
    ``Left``. That is safe for CARLA, which feeds it CARLA's own waypoints;
    it is not safe for us. CARLA's yaws are **not normalised** — measured
    values on our sampled waypoints include −270.21 and −539.91 — so the
    literal version turned 1.44° bends into ``Left`` requests that CARLA
    then could not satisfy. Measured over 56 legs it took Town05 from 14/14
    legs and 0 dropped routes to 12/14 and **170**, and every one of the
    nine junctions it renamed went ``Straight`` → a direction.

    So the angle is wrapped properly first, which is what CARLA's arithmetic
    is trying and failing to do, and only then compared against CARLA's
    threshold.
    :param first_inside_yaw: Heading at the first waypoint **inside** the
        junction.
    :param last_inside_yaw: Heading at the last waypoint inside it.
    :return: ``"Left"``, ``"Right"`` or ``"Straight"``.
    """
    change = _heading_change(first_inside_yaw, last_inside_yaw)
    if change >= STRAIGHT_DEG:
        return "Right"
    if change <= -STRAIGHT_DEG:
        return "Left"
    return "Straight"


def _is_labelled(approach: "carla.Waypoint") -> bool:
    """
    Say whether the traffic manager will label the junction ahead.

    This mirrors `InMemoryMap.cpp:388` in CARLA 0.9.16, because that is what
    decides whether an entry gets spent. A waypoint is given a directional
    `RoadOption` when it branches, **or** when it is about to enter a
    junction and a traffic light, stop sign or yield sign lies within
    ``SIGNAL_RANGE_M``. Otherwise it stays ``LaneFollow``, and a route entry
    aimed at it is never consumed there.

    The distinction matters only off a single-lane grid. Where every
    junction branches the first clause always fires, which is why Town01 and
    Town02 never needed the second: measured 2026-08-14, Town03's tour has
    15 junctions of 94 that do not branch but are signalised, and Town01 has
    0 of 52.
    :param approach: Last waypoint before the junction.
    :return: True if an entry will be consumed at this junction.
    """
    if len(approach.next(STEP_M)) > 1:
        return True
    return any(
        landmark.type in SIGNAL_LANDMARK_TYPES
        for landmark in approach.get_landmarks(SIGNAL_RANGE_M)
    )


def turns_along(
    route: list["carla.Waypoint"], branching_only: bool = False
) -> list[str]:
    """
    Read the junction manoeuvres off a planned route.

    Only the junctions where the traffic manager will actually spend an
    entry are reported, in the order they must be made. The names are the
    strings the traffic manager itself uses, as returned by
    `TrafficManager.get_next_action`.
    :param route: Waypoints in travel order, as from `plan_route`.
    :param branching_only: Decide on branching alone, ignoring signals.
        This is the behaviour every CARLA result recorded before
        2026-08-14 was measured under, kept so the two can be compared.
    :return: Ordered turn names, e.g. ``["Left", "Left", "Straight"]``.
    """
    turns: list[str] = []
    for crossing in _crossings(route):
        # `set_route` consumes the list in order, so an entry too many and
        # an entry too few are equally fatal — each throws every later turn
        # onto the wrong junction. The test therefore has to be the traffic
        # manager's own, not a plausible stand-in for it.
        #
        # It was `len(approach.next(STEP_M)) > 1` until 2026-08-14, which
        # asks whether the *lane* branches. The traffic manager asks whether
        # the waypoint is *labelled*, which on a multi-lane map is a
        # different question: a dedicated turn lane has one continuation
        # through a junction that is signalised, so it is labelled, an entry
        # is spent there, and a list built on branching is one short from
        # that junction onward. Traced end to end on Town03 junction 655.
        #
        # Both readings are now known to be wrong about *which* junctions
        # CARLA labels — see `junction_turns` — but this path is kept as it
        # was so the results recorded under it stay comparable.
        decides = (
            len(crossing.approach.next(STEP_M)) > 1 if branching_only
            else _is_labelled(crossing.approach)
        )
        if decides:
            turns.append(_turn_name(
                crossing.first_inside.transform.rotation.yaw,
                crossing.last_inside.transform.rotation.yaw,
            ))
    return turns


def junction_turns(
    route: list["carla.Waypoint"],
) -> list[tuple[int, str]]:
    """
    Read the manoeuvre at **every** junction the route crosses, keyed by
    junction.

    The difference from ``turns_along`` is not the geometry — it is that
    nothing here tries to predict which junctions the traffic manager will
    spend a route entry at. That prediction is what ``_is_labelled`` exists
    to make, and it is not derivable: measured 2026-08-15, CARLA labels
    waypoints *inside* junction connections, never approaches, and no
    geometric feature separates a labelled connection from an unlabelled
    one.

    Carrying the junction id removes the need for it. A consumer can match
    each turn to the junction it belongs to rather than to a position in a
    list, so an extra or missing entry costs nothing instead of throwing
    every later turn onto the wrong junction. It also expresses something a
    positional list cannot: a route that crosses the **same junction
    twice** and turns differently each time — which the Town03 leg
    ``s05 -> s11`` does, at junction 861.
    :param route: Waypoints in travel order, as from ``plan_route``.
    :return: (junction id, turn name) in the order the route meets them,
        with repeats where the route revisits a junction.
    """
    return [
        (crossing.junction_id, _turn_name(
            crossing.first_inside.transform.rotation.yaw,
            crossing.last_inside.transform.rotation.yaw,
        ))
        for crossing in _crossings(route)
        if crossing.junction_id is not None
    ]


def plan_turns(
    carla_map: "carla.Map",
    origin: "carla.Location",
    destination: "carla.Location",
) -> list[str]:
    """
    Work out the turns that lead from one location to another.

    :param carla_map: Map to route over.
    :param origin: Where the vehicle is now.
    :param destination: Where it should end up.
    :return: Ordered turn names for `TrafficManager.set_route`. Empty when
        the destination needs no junction decisions, and also when no route
        exists — in both cases there is nothing useful to tell the traffic
        manager, and lane-following is the honest fallback.
    """
    route = plan_route(carla_map, origin, destination)
    if route is None:
        return []
    return turns_along(route)
