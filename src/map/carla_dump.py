"""Dump a CARLA town's road network to a file the provider can read offline.

Run once per town, against a live server::

    ./run.sh -m src.map.carla_dump --town Town01

**This is the only module under ``src/`` that imports carla.** Everything
else in the map layer reads the file this produces, which is what lets
``CarlaMapProvider`` and its tests run with no server and no wheel.

The dump is a pure function of the CARLA map: raw topology segments, never
the split ones the provider derives from the authored stops. So re-dumping a
town's roads cannot invalidate stop authoring.
"""

import argparse
import sys

from dataclasses import replace

import carla

from src.map.carla_stops import town_name
from src.simulation import carla_world
from src.map.carla_topology import (
    DEFAULT_TRAFFIC_EFFICIENCY,
    DEFAULT_TOWN_SPEED_LIMIT_KPH,
    CarlaTopology,
    RoadSegment,
    SegmentPoint,
    save_topology,
)

# Sampling resolution along a lane, in metres. Fine enough to place a stop
# accurately after interpolation, coarse enough to keep the file small:
# Town01 is roughly 6 km of road, so a few thousand points.
SAMPLE_STEP_M = 2.0

# A walk should terminate at the segment end long before this; the cap only
# stops a malformed map from looping forever.
MAX_STEPS = 2000


def walk_segment(
    entry: "carla.Waypoint", exit_waypoint: "carla.Waypoint"
) -> list["carla.Waypoint"]:
    """
    Walk a segment from its entry, staying on the entry's own road and lane.

    ``next()`` forks where a lane approaches a junction, so the walk follows
    the branch that stays put. Without that it wanders into a neighbouring
    road and the segment comes back the wrong length.

    The exit waypoint is **not** included: ``get_topology`` returns adjacency
    pairs, so the exit is the start of the *following* road and its ``s``
    belongs to that road, not this one. ``dump_world`` adds its position back
    with an interpolated ``s`` -- the position is needed so segments meeting
    at a junction share a node, but its ``s`` would corrupt the segment.
    :param entry: Waypoint at the start of the segment.
    :param exit_waypoint: Waypoint where the following road begins.
    :return: Waypoints on the entry's road and lane, SAMPLE_STEP_M apart.
    """
    walked = [entry]
    current = entry
    for _ in range(MAX_STEPS):
        forward = [
            candidate for candidate in current.next(SAMPLE_STEP_M)
            if candidate.road_id == entry.road_id
            and candidate.lane_id == entry.lane_id
        ]
        if not forward:
            break
        current = forward[0]
        if current.transform.location.distance(
                exit_waypoint.transform.location) < SAMPLE_STEP_M:
            break
        walked.append(current)
    return walked


def lane_change_along(waypoints: list["carla.Waypoint"]) -> str:
    """
    Name the lane changes legal along a whole segment.

    ``lane_change`` is a property of a waypoint, not of a segment: the road
    markings can turn solid part way along. The **most restrictive** value
    wins, because a manoeuvre is only safe to plan if it is legal for the
    whole stretch the planner might place it on -- and the graph places a
    lane change at a segment boundary without knowing where along the lane
    the vehicle will actually move over.
    :param waypoints: The segment's sampled waypoints.
    :return: One of "None", "Left", "Right", "Both".
    """
    import carla

    left = right = True
    for waypoint in waypoints:
        change = waypoint.lane_change
        left = left and change in (carla.LaneChange.Left,
                                   carla.LaneChange.Both)
        right = right and change in (carla.LaneChange.Right,
                                     carla.LaneChange.Both)
        if not (left or right):
            return "None"
    if left and right:
        return "Both"
    if left:
        return "Left"
    return "Right" if right else "None"


def with_terminus(
    points: tuple[SegmentPoint, ...], location: "carla.Location"
) -> tuple[SegmentPoint, ...]:
    """
    Append the segment's end position, extrapolating ``s`` to match.

    The end position comes from a waypoint on the *next* road, so its own
    ``s`` cannot be used. Extending at the rate ``s`` was already changing
    keeps the sequence monotonic, which is what ``distance_at_s`` needs in
    order to interpolate -- and ``s`` decreases along a positive lane, so the
    rate carries the sign.
    :param points: Points already sampled along the segment.
    :param location: World position where the segment ends.
    :return: The points with the terminus appended.
    """
    last = points[-1]
    gap = (
        (location.x - last.x) ** 2
        + (location.y - last.y) ** 2
        + (location.z - last.z) ** 2
    ) ** 0.5
    if gap < 1e-6:
        return points

    rate = 1.0
    if len(points) >= 2:
        previous = points[-2]
        step = (
            (last.x - previous.x) ** 2
            + (last.y - previous.y) ** 2
            + (last.z - previous.z) ** 2
        ) ** 0.5
        if step > 1e-9:
            rate = (last.s - previous.s) / step

    return points + (SegmentPoint(
        x=location.x, y=location.y, z=location.z, s=last.s + rate * gap,
    ),)


def to_points(waypoints: list["carla.Waypoint"]) -> tuple[SegmentPoint, ...]:
    """
    Convert waypoints to dump points, dropping coincident neighbours.

    A zero-length step would make interpolation ambiguous and can appear when
    the walk stops within one sample of the exit.
    :param waypoints: Waypoints in driving order.
    :return: The sampled points.
    """
    points: list[SegmentPoint] = []
    for waypoint in waypoints:
        location = waypoint.transform.location
        point = SegmentPoint(
            x=location.x, y=location.y, z=location.z, s=waypoint.s
        )
        if points:
            previous = points[-1]
            gap = (
                (point.x - previous.x) ** 2
                + (point.y - previous.y) ** 2
                + (point.z - previous.z) ** 2
            ) ** 0.5
            if gap < 1e-6:
                continue
        points.append(point)
    return tuple(points)


# OpenDRIVE's signal type code for a speed-limit sign.
SPEED_LIMIT_LANDMARK_TYPE = "274"


def read_speed_limits(
    carla_map: "carla.Map",
    segments: list[RoadSegment],
    default_kph: float = DEFAULT_TOWN_SPEED_LIMIT_KPH,
) -> dict[int, float]:
    """
    Read per-road speed limits from a town's own speed-limit landmarks.

    Until 2026-08-11 this was not attempted, on the recorded belief that
    0.9.16's towns publish nothing. They do: Town01 carries 22 type-274
    landmarks at 30, 60 and 90, and a driving vehicle reads all three back
    from ``get_speed_limit()``. Treating every road as 30 mispriced the
    town's arterials — **37.7% of its length** — by two to three times.

    **A road can carry several signs**, each governing from where it stands
    until the next: Town01's road 8 runs 30, 90, 60, 30 along its length.
    Taking the minimum would throw the fast sections away and taking the
    maximum would promise pickups the bus cannot reach, so the sections are
    combined by a **length-weighted harmonic mean** — the average that
    preserves total travel time, since time is the sum of ``length / speed``
    and not of the speeds. A road is a single graph edge, so one number per
    road is all the model can carry.

    Two known approximations, both quantified by ``carla_calibrate``, which
    checks this table against what a driving vehicle reports:

    * **Orientation is ignored.** A sign is valid for one direction of
      travel, but both directions of a road share a road ID here.
    * **Sign units are ignored.** CARLA declares these landmarks in ``mph``
      while ``get_speed_limit()`` returns the bare number and the traffic
      manager drives it as km/h. The number is what governs the driving, so
      the number is what is recorded.
    :param carla_map: The map to read landmarks from.
    :param segments: Already-dumped segments, used to learn how far each
        road extends so the sections can be weighted.
    :param default_kph: Limit in force before a road's first sign.
    :return: Speed limit in km/h per OpenDRIVE road ID; roads with no sign
        are absent, and fall back to the town default.
    """
    # How far each road runs, in its own s coordinate.
    extent: dict[int, float] = {}
    for segment in segments:
        reach = max(abs(point.s) for point in segment.points)
        extent[segment.road_id] = max(extent.get(segment.road_id, 0.0), reach)

    signs: dict[int, set[tuple[float, float]]] = {}
    for landmark in carla_map.get_all_landmarks_of_type(
            SPEED_LIMIT_LANDMARK_TYPE):
        value = float(landmark.value)
        if value <= 0.0:
            # A sign with no usable value tells us nothing, and zero would
            # divide by zero below.
            continue
        # Both directions post their own sign at the same place; identical
        # (position, value) pairs are one constraint, not two.
        signs.setdefault(int(landmark.road_id), set()).add(
            (round(float(landmark.s), 1), value)
        )

    limits: dict[int, float] = {}
    for road_id, marks in signs.items():
        road_length = extent.get(road_id)
        if not road_length:
            # A sign on a road that was not dumped governs nothing we route
            # over, so it cannot be weighted and is dropped.
            continue
        ordered = sorted(marks)
        # Walk the road, charging each section the limit in force along it.
        boundaries = [s for s, _ in ordered] + [road_length]
        in_force = default_kph
        travelled = travel_time = 0.0
        for index, upper in enumerate(boundaries):
            section = min(upper, road_length) - travelled
            if section > 0.0:
                travelled += section
                travel_time += section / in_force
            if index < len(ordered):
                in_force = ordered[index][1]
        if travelled > 0.0 and travel_time > 0.0:
            limits[road_id] = round(travelled / travel_time, 2)
    return limits


def dump_world(
    world: "carla.World",
    default_speed_limit_kph: float = DEFAULT_TOWN_SPEED_LIMIT_KPH,
    overrides: dict[int, float] | None = None,
    traffic_efficiency: float = DEFAULT_TRAFFIC_EFFICIENCY,
) -> CarlaTopology:
    """
    Read a live world's topology into a CarlaTopology.

    :param world: The connected carla.World to read. Not modified.
    :param default_speed_limit_kph: Limit applied to roads with no sign.
    :param overrides: Per-road speed limits, keyed by road ID. Defaults to
        None, which reads them from the town's own landmarks; pass a dict to
        override that, or an empty dict to record none at all.
    :param traffic_efficiency: Fraction of the posted limit a bus averages
        here, from ``carla_calibrate``. Defaults to 1.0 — free flow, which
        no real town achieves — so a dump is honest about not being
        calibrated rather than silently carrying another town's number.
    :return: The topology, ready to be saved.
    """
    carla_map = world.get_map()
    segments = []
    # get_topology returns adjacency pairs, so one physical stretch appears
    # once per downstream branch -- road 1 lane -1 in Town01 appears twice,
    # identical but for which junction follows it. Those are the same edge.
    seen: set[tuple] = set()

    for entry, exit_waypoint in carla_map.get_topology():
        walked = walk_segment(entry, exit_waypoint)
        points = to_points(walked)
        if not points:
            continue
        points = with_terminus(points, exit_waypoint.transform.location)
        if len(points) < 2:
            # A segment shorter than one sample carries no drivable length.
            continue

        key = (
            entry.road_id, entry.lane_id,
            round(points[0].x, 1), round(points[0].y, 1),
            round(points[-1].x, 1), round(points[-1].y, 1),
        )
        if key in seen:
            continue
        seen.add(key)

        segments.append(RoadSegment(
            road_id=entry.road_id,
            lane_id=entry.lane_id,
            is_junction=entry.is_junction,
            points=points,
            lane_change=lane_change_along(walked),
        ))

    if overrides is None:
        overrides = read_speed_limits(
            carla_map, segments, default_speed_limit_kph
        )

    return CarlaTopology(
        map=carla_map.name,
        default_speed_limit_kph=default_speed_limit_kph,
        speed_limit_overrides=dict(overrides),
        traffic_efficiency=traffic_efficiency,
        segments=tuple(segments),
    )


def main(argv: list[str] | None = None) -> int:
    """
    Connect to a server, dump one town, and report what was written.
    :param argv: Command-line arguments, or None to read sys.argv.
    :return: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", default="Town01",
                        help="town to load and dump (default: Town01)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--speed-limit", type=float,
        default=DEFAULT_TOWN_SPEED_LIMIT_KPH,
        help=("speed limit in km/h for roads with no override; CARLA "
              "0.9.16 publishes none, so this is a stated assumption"),
    )
    parser.add_argument(
        "--keep-loaded-map", action="store_true",
        help="dump whatever map is already loaded instead of loading --town",
    )
    arguments = parser.parse_args(argv)

    client = carla.Client(arguments.host, arguments.port)
    client.set_timeout(arguments.timeout)
    world = client.get_world()

    generated = carla_world.is_generated(arguments.town)
    if not arguments.keep_loaded_map and (
            generated or arguments.town not in world.get_map().name):
        print(f"loading {arguments.town} ...")
        world = carla_world.load(client, arguments.town)

    # A generated map publishes no type-274 landmarks at all, so the limits
    # have to come from its OpenDRIVE instead — see
    # ``carla_world.speed_limits``. Passing them as overrides skips the
    # landmark read entirely, which would otherwise return nothing.
    overrides = (carla_world.speed_limits(arguments.town)
                 if generated else None)
    topology = dump_world(
        world, default_speed_limit_kph=arguments.speed_limit,
        overrides=overrides,
    )
    if generated:
        # Every generated world reports the same map name, so the dump would
        # be filed under that rather than under the town asked for.
        topology = replace(topology, map=town_name(arguments.town))
    signed = len(topology.speed_limit_overrides)
    path = save_topology(
        topology,
        note=(f"dumped from {world.get_map().name} at "
              f"{SAMPLE_STEP_M} m resolution; per-road limits read from "
              f"{signed} signed roads, {arguments.speed_limit} km/h "
              f"applied to the rest; traffic_efficiency "
              f"{topology.traffic_efficiency} (1.0 = uncalibrated)"),
    )

    points = sum(len(s.points) for s in topology.segments)
    length_km = sum(s.length_m for s in topology.segments) / 1000.0
    junctions = sum(1 for s in topology.segments if s.is_junction)
    print(f"map                {topology.map}")
    print(f"segments           {len(topology.segments)} "
          f"({junctions} inside junctions)")
    print(f"sampled points     {points}")
    print(f"total lane length  {length_km:.1f} km")
    print(f"default limit      {topology.default_speed_limit_kph} km/h")
    print(f"signed roads       {signed} "
          f"{sorted(set(topology.speed_limit_overrides.values()))} km/h")
    print(f"traffic efficiency {topology.traffic_efficiency} "
          f"{'(uncalibrated)' if topology.traffic_efficiency == 1.0 else ''}")
    print(f"wrote              {path}")
    print(f"size               {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
