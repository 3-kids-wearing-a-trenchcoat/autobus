"""Planning a drivable route between two points in CARLA.

The bus is driven by CARLA's own `BasicAgent`, which follows a list of
waypoints, so this module's whole job is to produce that list: a
travel-time-shortest path along lanes, including legal lane changes.

CARLA ships an equivalent, `agents.navigation.GlobalRoutePlanner`, but the
pip wheel is client bindings only (`libcarla.so` plus stubs) and does not
include it; it exists only in CARLA's source tree and container image. Since
it is ordinary Python over the same public API, and using it would mean
copying ~1200 lines into this repository, the search is done here instead.
It was validated against that implementation while it was written.

**This module imports carla**, like `CarlaBackend` and unlike everything
else under `src/simulation`. It deliberately knows nothing about the transit
graph or bus stops: it takes two world locations and answers with waypoints.
"""

import heapq
from itertools import count
from typing import Callable

import carla

# The one place a lane change is priced. Imported rather than repeated
# because TransitGraph charges the same thing, and a planner that
# disagrees with the policy has the bus beating its own estimates.
# ``map_data`` imports nothing from this project, so this cannot cycle.
from src.map.map_data import (LANE_CHANGE_PENALTY_S,
                              LANE_CHANGE_RAMP_M)

# How far apart successive waypoints are while searching. Small enough to
# resolve short junction links, large enough that a whole town stays a few
# thousand nodes.
STEP_M = 2.0

# How close a search node must come to the destination to count as arrival.
GOAL_TOLERANCE_M = 4.0

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
    speed_for: Callable[[int], float] | None = None,
    lane_changes: bool = False,
) -> list["carla.Waypoint"] | None:
    """
    Find the quickest driveable path along lanes from one location to another.

    Dijkstra over `waypoint.next()`, which follows lane direction and expands
    every branch of a junction, so the result respects one-way roads.

    **What is being minimised matters.** Steps are a fixed length, so counting
    them minimises *distance*; charging each step the time it takes minimises
    *travel time*. Those are different routes wherever speed limits vary, and
    `TransitGraph` prices legs in time — so a distance-optimal route makes the
    bus drive something the policy never costed. Measured on Town01, that is
    26% of stop pairs at a median 1.096x time penalty.

    ``traffic_efficiency`` is deliberately absent: it is a single per-town
    scalar, so it multiplies every path's cost equally and cannot change which
    route is cheapest. The planner needs the speed limits, not the calibration.
    :param carla_map: Map to route over.
    :param origin: Where the vehicle is now.
    :param destination: Where it should end up.
    :param speed_for: Posted speed limit in km/h for an OpenDRIVE road ID —
        normally ``CarlaTopology.speed_limit_for``. Defaults to None, which
        charges every step equally and therefore minimises distance, the
        behaviour this planner had before travel time was priced.
    :param lane_changes: Let the search move sideways into a legal adjacent
        lane. Without it a route can only finish in the goal's lane by
        driving round the block — measured on Town05, a 164 m hop becomes a
        706 m drive. Defaults to False, which reproduces every run recorded
        before 2026-08-17.
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

    def speed_mps(waypoint: "carla.Waypoint") -> float:
        """Posted limit on this waypoint's road, in metres per second."""
        return speed_for(waypoint.road_id) * 1000 / 3600

    def step_cost(waypoint: "carla.Waypoint") -> float:
        """Cost of stepping STEP_M on from this waypoint."""
        if speed_for is None:
            return 1.0
        return STEP_M / speed_mps(waypoint)

    def sideways(waypoint: "carla.Waypoint") -> list:
        """
        Legal lane changes leaving this waypoint, already along their ramp.

        The neighbour is advanced LANE_CHANGE_RAMP_M so the move is a
        diagonal rather than a sideways teleport: a single lateral waypoint
        asks BasicAgent's controller for a step it cannot track.
        :param waypoint: Waypoint to leave sideways.
        :return: (waypoint, cost) pairs.
        """
        if not lane_changes:
            return []
        change = waypoint.lane_change
        moves = []
        for neighbour, permitted in (
            (waypoint.get_left_lane(),
             (carla.LaneChange.Left, carla.LaneChange.Both)),
            (waypoint.get_right_lane(),
             (carla.LaneChange.Right, carla.LaneChange.Both)),
        ):
            if neighbour is None or change not in permitted:
                continue
            if neighbour.lane_type != carla.LaneType.Driving:
                continue
            # get_left_lane can hand back the oncoming carriageway; the sign
            # of lane_id is CARLA's own marker for direction of travel.
            if (neighbour.lane_id > 0) != (waypoint.lane_id > 0):
                continue
            if speed_for is None:
                # Untimed: charge the ramp in steps, to stay commensurate
                # with the uniform step cost above.
                cost = LANE_CHANGE_RAMP_M / STEP_M
            else:
                cost = (LANE_CHANGE_RAMP_M / speed_mps(waypoint)
                        + LANE_CHANGE_PENALTY_S)
            moves.extend((ahead, cost)
                         for ahead in neighbour.next(LANE_CHANGE_RAMP_M))
        return moves

    start_key = _lane_key(start)
    came_from: dict[tuple[int, int, int], tuple[int, int, int] | None] = {
        start_key: None
    }
    seen_at = {start_key: start}
    best = {start_key: 0.0}
    # A monotonic counter breaks ties by discovery order. With a uniform
    # step cost that makes this pop nodes in exactly the order the previous
    # breadth-first search did, so the untimed fallback is not merely
    # equivalent in cost but returns the same route.
    order = count()
    frontier = [(0.0, next(order), start_key)]

    while frontier and len(came_from) < MAX_SEARCH_NODES:
        cost, _, key = heapq.heappop(frontier)
        if cost > best.get(key, float("inf")):
            continue                    # a cheaper way here was already found
        current = seen_at[key]
        arrived = (
            (current.road_id, current.lane_id) == goal_lane
            and current.transform.location.distance(goal_location)
            < GOAL_TOLERANCE_M
        )
        if arrived:
            return _reconstruct(came_from, seen_at, key)

        # Driving lanes only. ``next()` will happily lead off the road
        # network -- on the TAU campus the service roads and parking aisles
        # are `Restricted`, and a route down one had the bus drive 270 m
        # through a car park to a dead end. ``sideways`` already filters
        # this way; the forward expansion did not.
        moves = [(following, step_cost(current))
                 for following in current.next(STEP_M)
                 if following.lane_type == carla.LaneType.Driving]
        moves += sideways(current)
        for following, step in moves:
            onward = cost + step
            following_key = _lane_key(following)
            if onward >= best.get(following_key, float("inf")):
                continue
            best[following_key] = onward
            came_from[following_key] = key
            seen_at[following_key] = following
            heapq.heappush(frontier, (onward, next(order), following_key))
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
