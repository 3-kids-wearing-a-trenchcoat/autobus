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

import carla

from src.map.carla_topology import (
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


def dump_world(
    world: "carla.World",
    default_speed_limit_kph: float = DEFAULT_TOWN_SPEED_LIMIT_KPH,
    overrides: dict[int, float] | None = None,
) -> CarlaTopology:
    """
    Read a live world's topology into a CarlaTopology.

    :param world: The connected carla.World to read. Not modified.
    :param default_speed_limit_kph: Limit applied to roads with no override.
    :param overrides: Optional per-road speed limits, keyed by road ID.
    :return: The topology, ready to be saved.
    """
    carla_map = world.get_map()
    segments = []
    # get_topology returns adjacency pairs, so one physical stretch appears
    # once per downstream branch -- road 1 lane -1 in Town01 appears twice,
    # identical but for which junction follows it. Those are the same edge.
    seen: set[tuple] = set()

    for entry, exit_waypoint in carla_map.get_topology():
        points = to_points(walk_segment(entry, exit_waypoint))
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
        ))

    return CarlaTopology(
        map=carla_map.name,
        default_speed_limit_kph=default_speed_limit_kph,
        speed_limit_overrides=dict(overrides or {}),
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

    if not arguments.keep_loaded_map and (
            arguments.town not in world.get_map().name):
        print(f"loading {arguments.town} ...")
        world = client.load_world(arguments.town)

    topology = dump_world(
        world, default_speed_limit_kph=arguments.speed_limit
    )
    path = save_topology(
        topology,
        note=(f"dumped from {world.get_map().name} at "
              f"{SAMPLE_STEP_M} m resolution; speed limit "
              f"{arguments.speed_limit} km/h is an assumption, CARLA "
              f"0.9.16 publishes none"),
    )

    points = sum(len(s.points) for s in topology.segments)
    length_km = sum(s.length_m for s in topology.segments) / 1000.0
    junctions = sum(1 for s in topology.segments if s.is_junction)
    print(f"map                {topology.map}")
    print(f"segments           {len(topology.segments)} "
          f"({junctions} inside junctions)")
    print(f"sampled points     {points}")
    print(f"total lane length  {length_km:.1f} km")
    print(f"speed limit        {topology.default_speed_limit_kph} km/h")
    print(f"wrote              {path}")
    print(f"size               {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
