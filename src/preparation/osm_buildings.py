"""Turn a generated town's OpenStreetMap buildings into scenery blocks.

Run once per generated town, against a live server::

    ./run.sh -m src.preparation.osm_buildings --town TAU \\
        --osm map_osm/TelAvivUniversity.osm --write

**This module imports carla**, like the rest of ``preparation``, and it
needs the town loaded because it asks CARLA itself where things are.

## Why this is not a one-line coordinate conversion

The obvious approach -- project the OSM latitude/longitude with the map's
own projection and place a building there -- does not work, and fails
*plausibly*, which is worse. Two things are in the way.

**The OpenDRIVE carries no georeference.** ``TAU.xodr``'s ``<geoReference>``
element is empty; the projection string survives only inside a netconvert
comment that nothing parses. So CARLA falls back to treating world (0, 0) as
the projection origin, and ``Map.transform_to_geolocation`` is wrong by a
constant.

**netconvert shifted the roads.** Its output starts at the origin --
the header says ``west="0.00" south="0.00"`` -- so OpenDRIVE coordinates are
the projected ones plus an offset that is recorded nowhere.

The two together put buildings about **2 km off the map**, in a render that
otherwise looks entirely reasonable.

## What is done instead

1. Fit latitude/longitude to CARLA world coordinates from CARLA's own
   ``transform_to_geolocation`` over waypoints spread across the whole map.
   Over a campus this relation is affine to well under a metre (measured
   held-out error: mean 0.014 m, max 0.073 m). That inverts CARLA's belief
   and lands in the *unshifted* frame.

   Sample the whole map, not the first N waypoints: ``generate_waypoints``
   returns them clustered by road, and fitting a clustered subset
   extrapolates badly.

2. Recover netconvert's shift by registering the OSM road nodes onto
   CARLA's road network -- a grid search over translation, scoring how many
   nodes land within 10 m of a driving lane.

⚠ **The result is approximate.** netconvert also ran ``geometry.remove``
and ``edges.join``, which reshape the road geometry, so the registration
settles around 27 m median node error and no translation can do better.
Blocks land in the right places between the right roads, which is all a
demonstration needs. **Do not present these positions as survey accurate.**
"""

import argparse
import math
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import carla
import numpy as np
from scipy.spatial import cKDTree

from src.map.carla_props import SceneryBlock, save_props
from src.map.carla_stops import town_name
from src.simulation import carla_world

# OSM highway values netconvert was told to keep, from the
# ``keep-edges.by-type`` list in the generated OpenDRIVE. Registration uses
# these and only these: including types netconvert dropped adds nodes that
# can never match, which drags the search off the true alignment.
DRIVABLE_HIGHWAYS = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "service", "living_street",
})

# Storeys are 3.5 m. Where a building says nothing, guess by what it is --
# TAU's dormitories are towers and its faculties are mid-rise.
STOREY_HEIGHT_M = 3.5
DEFAULT_HEIGHT_M = 8.0
HEIGHT_BY_KIND_M = {"dormitory": 18.0, "university": 12.0}

# A footprint smaller than this is a shed, a bin store or a mapping
# artefact; drawing it adds clutter and no information.
MIN_LENGTH_M = 6.0
MIN_WIDTH_M = 4.0

# Widest cube to use. A very wide building becomes several narrower blocks
# instead, which follows a long facade better than one huge box.
MAX_EDGE_M = 40.0

# Clearance from the centre of the nearest driving lane, on top of half a
# lane and the generated apron. A block closer than this is dropped
# outright: scenery that fouls a lane would stop the bus, and no
# decoration is worth a failed run.
ROAD_CLEARANCE_M = 3.0
GENERATED_APRON_M = 4.0                 # carla_world.OPENDRIVE_PARAMETERS

# Registration search, in metres.
COARSE_HALF_WIDTH_M = 120.0
COARSE_STEP_M = 10.0
REFINE_STEPS_M = (4.0, 1.0, 0.25)
MATCH_TOLERANCE_M = 10.0


def _read_ways(
    osm_path: Path
) -> tuple[np.ndarray, list[tuple[dict, np.ndarray]]]:
    """
    Pull road nodes and building footprints out of an OSM extract.

    :param osm_path: The ``.osm`` file the town was generated from.
    :return: Road node latitude/longitude as an (N, 2) array, and a list of
        (tags, footprint) pairs where the footprint is an (M, 2) array of
        latitude/longitude.
    """
    root = ET.parse(osm_path).getroot()
    nodes = {
        node.get("id"): (float(node.get("lat")), float(node.get("lon")))
        for node in root.findall("node")
    }
    roads: list[tuple[float, float]] = []
    buildings: list[tuple[dict, np.ndarray]] = []
    for way in root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        points = [
            nodes[ref.get("ref")] for ref in way.findall("nd")
            if ref.get("ref") in nodes
        ]
        if tags.get("highway") in DRIVABLE_HIGHWAYS and len(points) >= 2:
            roads.extend(points)
        if "building" in tags and len(points) >= 4:
            buildings.append((tags, np.array(points)))
    return np.array(roads), buildings


def _fit_geolocation(carla_map: "carla.Map") -> np.ndarray:
    """
    Fit latitude/longitude to world coordinates, using CARLA's own belief.

    Inverts ``transform_to_geolocation``, which over a campus is affine to
    well under a metre. Waypoints are shuffled before sampling because
    ``generate_waypoints`` returns them grouped by road, and a spatially
    clustered fit extrapolates wildly.

    :param carla_map: The loaded map.
    :return: A (3, 2) coefficient array mapping ``[lat, lon, 1]`` to
        ``[x, y]``.
    """
    waypoints = carla_map.generate_waypoints(15.0)
    random.Random(0).shuffle(waypoints)
    sample = waypoints[:1500]
    geo = []
    world = []
    for waypoint in sample:
        location = waypoint.transform.location
        point = carla_map.transform_to_geolocation(location)
        geo.append([point.latitude, point.longitude])
        world.append([location.x, location.y])
    design = np.c_[np.array(geo), np.ones(len(geo))]
    coefficients, *_ = np.linalg.lstsq(design, np.array(world), rcond=None)
    return coefficients


def _register(
    osm_roads_xy: np.ndarray, road_xy: np.ndarray
) -> tuple[np.ndarray, float, float]:
    """
    Find netconvert's translation by aligning OSM roads onto CARLA's.

    A coarse grid around the bounding-box guess, then successive refinement.
    Scored by the share of OSM road nodes landing within
    ``MATCH_TOLERANCE_M`` of a driving lane, which is robust to the nodes
    netconvert dropped in a way that a mean distance is not.

    :param osm_roads_xy: OSM road nodes in the unshifted frame, (N, 2).
    :param road_xy: CARLA driving-lane samples, (M, 2).
    :return: The offset, the matched fraction, and the median node error.
    """
    tree = cKDTree(road_xy)

    def score(offset: np.ndarray) -> tuple[float, float]:
        distance, _ = tree.query(osm_roads_xy + offset)
        return float((distance < MATCH_TOLERANCE_M).mean()), \
            float(np.median(distance))

    best = np.array([
        road_xy[:, 0].min() - osm_roads_xy[:, 0].min(),
        road_xy[:, 1].min() - osm_roads_xy[:, 1].min(),
    ])
    best_score = score(best)[0]
    span = np.arange(-COARSE_HALF_WIDTH_M,
                     COARSE_HALF_WIDTH_M + 1e-9, COARSE_STEP_M)
    origin = best.copy()
    for dx in span:
        for dy in span:
            candidate = origin + [dx, dy]
            matched, _ = score(candidate)
            if matched > best_score:
                best_score, best = matched, candidate
    for step in REFINE_STEPS_M:
        fine = np.arange(-step * 5, step * 5 + 1e-9, step)
        for dx in fine:
            for dy in fine:
                candidate = best + [dx, dy]
                matched, _ = score(candidate)
                if matched > best_score:
                    best_score, best = matched, candidate
    matched, median = score(best)
    return best, matched, median


def _oriented_box(
    footprint: np.ndarray
) -> tuple[np.ndarray, float, float, float]:
    """
    Smallest sensible rectangle around a footprint, via its principal axes.

    :param footprint: Footprint vertices in world coordinates, (N, 2).
    :return: Centre, length along the long axis, width across it, and the
        long axis's yaw in degrees.
    """
    centre = footprint.mean(axis=0)
    _, _, axes = np.linalg.svd(footprint - centre, full_matrices=False)
    projected = (footprint - centre) @ axes.T
    low, high = projected.min(axis=0), projected.max(axis=0)
    middle = centre + ((low + high) / 2) @ axes
    yaw = math.degrees(math.atan2(axes[0, 1], axes[0, 0]))
    return middle, float(high[0] - low[0]), float(high[1] - low[1]), yaw


def _height_of(tags: dict) -> float:
    """
    How tall to draw a building.

    :param tags: The way's OSM tags.
    :return: Height above ground in metres.
    """
    levels = tags.get("building:levels")
    if levels:
        try:
            return max(3.0, float(levels) * STOREY_HEIGHT_M)
        except ValueError:
            pass                        # "2;3" and friends: fall through
    return HEIGHT_BY_KIND_M.get(tags.get("building"), DEFAULT_HEIGHT_M)


def blocks_from_osm(
    world: "carla.World", osm_path: Path
) -> tuple[list[SceneryBlock], dict]:
    """
    Derive scenery blocks for the loaded town from its OSM extract.

    :param world: The loaded, generated world.
    :param osm_path: The ``.osm`` file the town was generated from.
    :return: The blocks, and a report of how the derivation went.
    """
    carla_map = world.get_map()
    coefficients = _fit_geolocation(carla_map)

    def to_world(lat_lon: np.ndarray) -> np.ndarray:
        return np.c_[lat_lon, np.ones(len(lat_lon))] @ coefficients

    waypoints = carla_map.generate_waypoints(4.0)
    road_xy = np.array([
        [w.transform.location.x, w.transform.location.y] for w in waypoints
    ])
    lane_width = float(np.median([w.lane_width for w in waypoints]))
    clearance = lane_width / 2 + GENERATED_APRON_M + ROAD_CLEARANCE_M

    osm_roads, buildings = _read_ways(osm_path)
    offset, matched, median = _register(to_world(osm_roads), road_xy)

    tree = cKDTree(road_xy)
    blocks: list[SceneryBlock] = []
    too_small = fouls_road = 0
    for tags, footprint in buildings:
        centre, length, width, yaw = _oriented_box(
            to_world(footprint) + offset
        )
        if length < MIN_LENGTH_M or width < MIN_WIDTH_M:
            too_small += 1
            continue
        height = _height_of(tags)
        edge = max(MIN_WIDTH_M, min(width, MAX_EDGE_M))
        count = max(1, int(round(length / edge)))
        along = np.array([math.cos(math.radians(yaw)),
                          math.sin(math.radians(yaw))])
        spots = np.array([
            centre + along * ((i - (count - 1) / 2) * (length / count))
            for i in range(count)
        ])
        distance, _ = tree.query(spots)
        # Half the cube can overhang towards the road, so charge it too.
        if (distance < clearance + edge / 2).any():
            fouls_road += 1
            continue
        for spot in spots:
            blocks.append(SceneryBlock(
                x=float(spot[0]), y=float(spot[1]),
                edge_m=edge, height_m=height, yaw_deg=yaw,
            ))
    report = {
        "footprints": len(buildings),
        "blocks": len(blocks),
        "skipped_too_small": too_small,
        "skipped_fouls_road": fouls_road,
        "offset": (float(offset[0]), float(offset[1])),
        "registration_matched": matched,
        "registration_median_m": median,
    }
    return blocks, report


def main(argv: list[str] | None = None) -> int:
    """
    Command-line entry point.

    :param argv: Argument list, defaulting to the process's own.
    :return: Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--town", required=True)
    parser.add_argument(
        "--osm", required=True, type=Path,
        help="the .osm extract the town's OpenDRIVE was generated from",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--write", action="store_true",
        help="record the blocks into the town's scenery file",
    )
    arguments = parser.parse_args(argv)

    if not carla_world.is_generated(arguments.town):
        print(f"{arguments.town} is not a generated map; shipped towns "
              f"come with their own scenery", file=sys.stderr)
        return 2

    client = carla.Client(arguments.host, arguments.port)
    client.set_timeout(arguments.timeout)
    print(f"loading {arguments.town} ...")
    world = carla_world.load(client, arguments.town)

    blocks, report = blocks_from_osm(world, arguments.osm)
    print(f"footprints          {report['footprints']}")
    print(f"blocks              {report['blocks']}")
    print(f"  skipped, tiny     {report['skipped_too_small']}")
    print(f"  skipped, on road  {report['skipped_fouls_road']}")
    print(f"offset              ({report['offset'][0]:.2f}, "
          f"{report['offset'][1]:.2f})")
    print(f"registration        {report['registration_matched']:.1%} of road "
          f"nodes within {MATCH_TOLERANCE_M:.0f} m, median "
          f"{report['registration_median_m']:.1f} m")

    if not arguments.write:
        print("\n(--write not given; nothing recorded)")
        return 0
    note = (
        f"{report['blocks']} blocks from {report['footprints']} OSM building "
        f"footprints in {arguments.osm.name}; registered onto the road "
        f"network at offset ({report['offset'][0]:.2f}, "
        f"{report['offset'][1]:.2f}), {report['registration_matched']:.0%} of "
        f"road nodes within {MATCH_TOLERANCE_M:.0f} m. Decoration only, and "
        f"not survey accurate."
    )
    path = save_props(town_name(arguments.town), blocks, note=note)
    print(f"wrote               {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
