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

import carla

# How far apart successive waypoints are while searching. Small enough to
# resolve short junction links, large enough that a whole town stays a few
# thousand nodes.
STEP_M = 2.0

# How close a search node must come to the destination to count as arrival.
GOAL_TOLERANCE_M = 4.0

# Heading change across a junction, in degrees, beyond which the manoeuvre
# is a turn rather than going straight on.
TURN_THRESHOLD_DEG = 35.0

# Guard against pathological searches on a large map.
MAX_SEARCH_NODES = 200000


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


def _heading_change(before: float, after: float) -> float:
    """
    Signed heading change in degrees, wrapped to (-180, 180].

    :param before: Yaw on entering the junction.
    :param after: Yaw on leaving it.
    :return: Change in yaw; positive is a right turn in CARLA's left-handed
        coordinates.
    """
    return (after - before + 180.0) % 360.0 - 180.0


def turns_along(route: list["carla.Waypoint"]) -> list[str]:
    """
    Read the junction manoeuvres off a planned route.

    Between junctions there is nothing to decide — the lane decides — so
    only the turns are reported, in the order they must be made. The names
    are the strings the traffic manager itself uses, as returned by
    `TrafficManager.get_next_action`.
    :param route: Waypoints in travel order, as from `plan_route`.
    :return: Ordered turn names, e.g. ``["Left", "Left", "Straight"]``.
    """
    turns: list[str] = []
    entry_yaw: float | None = None

    for previous, waypoint in zip(route, route[1:]):
        entering = waypoint.is_junction and not previous.is_junction
        leaving = previous.is_junction and not waypoint.is_junction
        if entering:
            # Only a junction that offers a choice is a decision. Where the
            # lane has exactly one continuation the traffic manager has
            # nothing to be told, and CARLA's own planner reports nothing
            # there either. Emitting a turn anyway would be worse than
            # useless: `set_route` consumes the list in order, so one
            # spurious entry makes every later turn happen at the wrong
            # junction.
            entry_yaw = (
                previous.transform.rotation.yaw
                if len(previous.next(STEP_M)) > 1
                else None
            )
        elif leaving and entry_yaw is not None:
            change = _heading_change(
                entry_yaw, waypoint.transform.rotation.yaw
            )
            if change > TURN_THRESHOLD_DEG:
                turns.append("Right")
            elif change < -TURN_THRESHOLD_DEG:
                turns.append("Left")
            else:
                turns.append("Straight")
            entry_yaw = None
    return turns


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
