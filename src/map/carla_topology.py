"""A dumped CARLA road network: the schema, and reading and writing it.

`CarlaMapProvider` is built against a dumped file rather than a live server,
so the provider and its tests need no CARLA at all. `carla_dump` produces the
file; this module defines what is in it and how to read it back.

The dump holds **raw** topology segments, exactly as CARLA reports them, and
never the split ones the provider derives. Splitting depends on the authored
stops, and keeping that out of the dump means re-dumping a town's roads can
never invalidate hours of stop authoring — and re-authoring stops never needs
a server.

Deliberately free of any ``carla`` import.
"""

import json
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

from src.map.carla_stops import CARLA_MAPS_DIR, town_name

SCHEMA_VERSION = 1

# Applied to roads carrying no speed-limit sign of their own. Leaving it
# unset would fall through to DEFAULT_SPEED_LIMIT (130 km/h) and make every
# urban estimate four times too fast.
#
# CORRECTION, 2026-08-11: this constant previously carried a comment claiming
# CARLA 0.9.16's towns publish no speed limits at all -- "no type-274
# landmarks and no traffic.speed_limit.* actors". **That is false.** Measured
# on a live Town01: 21 traffic.speed_limit.* actors, 22 type-274 landmarks,
# and 3,041 samples of vehicle.get_speed_limit() taken while driving, all
# three agreeing on values of 30, 60 and 90 km/h. The flat 30 was mispricing
# every fast road on the map by a factor of two to three. Limits are now read
# from the landmarks by ``carla_dump.read_speed_limits`` and this is only the
# fallback it was always meant to be.
DEFAULT_TOWN_SPEED_LIMIT_KPH = 30.0

# How much of the posted limit a bus actually averages door to door, once
# traffic lights, junctions and acceleration are paid for. 1.0 means the
# posted limit is achieved exactly, which is true of the mock backend by
# construction and of nothing that drives.
#
# Measured on Town01 (``carla_calibrate``, 45 legs x 2 traffic levels): the
# graph's free-flow estimate is 1.75x optimistic, of which 1.57x is time
# spent stationary at red lights -- 31-41% of every run. This is a *bias*,
# not noise; execution noise measured 0.003 (no traffic) to 0.028 (20 NPCs),
# which is an order of magnitude below anything safety_factor was swept over.
# A margin cannot repair a biased estimator, so the correction belongs here.
DEFAULT_TRAFFIC_EFFICIENCY = 1.0


@dataclass(frozen=True)
class SegmentPoint:
    """
    One sampled point along a lane segment.

    :param x: World X in metres.
    :param y: World Y in metres.
    :param z: World Z in metres.
    :param s: Distance along the road's OpenDRIVE reference line, in metres.
        This is what lets an authored stop, which stores only ``s``, be
        located along the polyline.
    """

    x: float
    y: float
    z: float
    s: float


@dataclass(frozen=True)
class RoadSegment:
    """
    An uninterrupted stretch of one lane, as CARLA's topology reports it.

    :param road_id: OpenDRIVE road ID.
    :param lane_id: OpenDRIVE lane ID. Its sign gives the driving direction
        relative to the reference line, so ``s`` **decreases** along a
        positive-lane segment.
    :param is_junction: Whether the segment lies inside a junction. Junction
        segments are kept, so graph connectivity is what CARLA says is legal
        rather than something inferred.
    :param points: Sampled positions from the segment's start to its end, in
        driving order.
    """

    road_id: int
    lane_id: int
    is_junction: bool
    points: tuple[SegmentPoint, ...]

    @property
    def start(self) -> SegmentPoint:
        """First point, where the segment is entered."""
        return self.points[0]

    @property
    def end(self) -> SegmentPoint:
        """Last point, where the segment is left."""
        return self.points[-1]

    @property
    def length_m(self) -> float:
        """Total driven length in metres, summed along the polyline."""
        return self.cumulative_lengths()[-1]

    def cumulative_lengths(self) -> list[float]:
        """
        Return distance travelled from the segment start to each point.
        :return: One value per point, starting at 0.0 and non-decreasing.
        """
        totals = [0.0]
        for before, after in zip(self.points, self.points[1:]):
            step = (
                (after.x - before.x) ** 2
                + (after.y - before.y) ** 2
                + (after.z - before.z) ** 2
            ) ** 0.5
            totals.append(totals[-1] + step)
        return totals

    def covers_s(self, s: float) -> bool:
        """
        Return whether an OpenDRIVE ``s`` falls within this segment.

        Handles both directions, since ``s`` decreases along a positive-lane
        segment.
        :param s: Distance along the road's reference line, in metres.
        :return: True if ``s`` lies between the segment's endpoints.
        """
        low, high = sorted((self.start.s, self.end.s))
        return low <= s <= high

    def distance_at_s(self, s: float) -> float:
        """
        Return how far along the segment a given OpenDRIVE ``s`` sits.

        Linearly interpolates between the two sampled points bracketing
        ``s``, so a stop lands where it was authored rather than at the
        nearest 2 m sample.
        :param s: Distance along the road's reference line, in metres.
        :return: Distance from the segment start, in metres.
        :raises ValueError: If ``s`` is outside this segment.
        """
        if not self.covers_s(s):
            raise ValueError(
                f"s={s} is outside segment road {self.road_id} "
                f"lane {self.lane_id} "
                f"({self.start.s}..{self.end.s})"
            )
        totals = self.cumulative_lengths()

        # s runs backwards along a positive lane, so search a normalised copy
        # and mirror the result back.
        descending = self.end.s < self.start.s
        keys = [(-p.s if descending else p.s) for p in self.points]
        target = -s if descending else s

        index = bisect_left(keys, target)
        if index <= 0:
            return totals[0]
        if index >= len(keys):
            return totals[-1]

        before, after = keys[index - 1], keys[index]
        if after == before:
            return totals[index]
        fraction = (target - before) / (after - before)
        return totals[index - 1] + fraction * (
            totals[index] - totals[index - 1]
        )


@dataclass(frozen=True)
class CarlaTopology:
    """
    A whole dumped town.

    :param map: Town name, without CARLA's path prefix.
    :param default_speed_limit_kph: Speed limit applied to every road with no
        override. See ``DEFAULT_TOWN_SPEED_LIMIT_KPH`` for why this is
        configured rather than read from CARLA.
    :param speed_limit_overrides: Per-road speed limits, keyed by road ID,
        read from the town's own speed-limit landmarks.
    :param segments: Every lane segment in the town.
    :param traffic_efficiency: Fraction of the posted limit a bus averages
        door to door. See ``DEFAULT_TRAFFIC_EFFICIENCY``.
    """

    map: str
    default_speed_limit_kph: float
    speed_limit_overrides: dict[int, float]
    segments: tuple[RoadSegment, ...]
    traffic_efficiency: float = DEFAULT_TRAFFIC_EFFICIENCY

    def speed_limit_for(self, road_id: int) -> float:
        """
        Return the posted speed limit that applies to a road.

        This is what the signs say, not what a bus achieves — see
        ``traffic_efficiency`` for the difference, and note that the two are
        deliberately kept apart: a limit is a fact about the map, an
        efficiency is a fact about driving through it.
        :param road_id: OpenDRIVE road ID.
        :return: Speed limit in km/h.
        """
        return self.speed_limit_overrides.get(
            road_id, self.default_speed_limit_kph
        )


def topology_path(town: str, directory: Path | None = None) -> Path:
    """
    Return the path of a town's topology dump.
    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the files. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Path to ``<town>.topology.json``.
    """
    base = CARLA_MAPS_DIR if directory is None else directory
    return base / f"{town_name(town)}.topology.json"


def load_topology(
    town: str, directory: Path | None = None
) -> CarlaTopology:
    """
    Read a town's topology dump.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the files. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The dumped topology.
    :raises FileNotFoundError: If the town has no dump.
    :raises ValueError: If the file names a different town or holds a
        segment with fewer than two points.
    """
    path = topology_path(town, directory)
    if not path.exists():
        raise FileNotFoundError(
            f"no topology dump for {town_name(town)} at {path}"
        )
    document = json.loads(path.read_text())

    if town_name(document.get("map", "")) != town_name(town):
        raise ValueError(
            f"{path} declares map {document.get('map')!r}, "
            f"which is not {town_name(town)}"
        )

    segments = []
    for entry in document["segments"]:
        points = tuple(
            SegmentPoint(x=p[0], y=p[1], z=p[2], s=p[3])
            for p in entry["points"]
        )
        if len(points) < 2:
            raise ValueError(
                f"{path}: segment road {entry['road_id']} "
                f"lane {entry['lane_id']} has fewer than two points"
            )
        segments.append(RoadSegment(
            road_id=int(entry["road_id"]),
            lane_id=int(entry["lane_id"]),
            is_junction=bool(entry["is_junction"]),
            points=points,
        ))

    return CarlaTopology(
        map=town_name(document["map"]),
        default_speed_limit_kph=float(
            document.get("default_speed_limit_kph",
                         DEFAULT_TOWN_SPEED_LIMIT_KPH)
        ),
        # JSON object keys are strings; road IDs are integers.
        speed_limit_overrides={
            int(road_id): float(limit)
            for road_id, limit in
            document.get("speed_limit_overrides", {}).items()
        },
        segments=tuple(segments),
        # Absent in schema 1 dumps, which predate the measurement.
        traffic_efficiency=float(
            document.get("traffic_efficiency", DEFAULT_TRAFFIC_EFFICIENCY)
        ),
    )


def save_topology(
    topology: CarlaTopology,
    directory: Path | None = None,
    note: str | None = None,
) -> Path:
    """
    Write a town's topology dump, overwriting any existing file.

    :param topology: The topology to write.
    :param directory: Directory to write into. Defaults to the packaged
        ``carla_maps`` directory.
    :param note: Optional provenance line stored alongside the segments.
    :return: The path written.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "map": town_name(topology.map),
        "note": note,
        "default_speed_limit_kph": topology.default_speed_limit_kph,
        "traffic_efficiency": topology.traffic_efficiency,
        "speed_limit_overrides": {
            str(road_id): limit
            for road_id, limit in topology.speed_limit_overrides.items()
        },
        "segments": [
            {
                "road_id": segment.road_id,
                "lane_id": segment.lane_id,
                "is_junction": segment.is_junction,
                "points": [
                    [round(p.x, 3), round(p.y, 3),
                     round(p.z, 3), round(p.s, 3)]
                    for p in segment.points
                ],
            }
            for segment in topology.segments
        ],
    }

    path = topology_path(topology.map, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1) + "\n")
    return path
