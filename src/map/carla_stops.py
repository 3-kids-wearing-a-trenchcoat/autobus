"""Authored CARLA bus stops: the schema, and reading and writing it.

Bus stops are an **input** to the map layer, not something derived from it.
In a real deployment you would say where the stops are rather than let a
program guess, so they are hand-authored, version-controlled, and one file
per town.

Deliberately free of any ``carla`` import. The provider has to read these
without a live server -- and so do the tests.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# Authored stop files live beside this module, one per town.
CARLA_MAPS_DIR = Path(__file__).parent / "carla_maps"

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CarlaStop:
    """
    One authored bus stop, addressed by its OpenDRIVE identity.

    ``road_id``, ``lane_id`` and ``s`` are the authority: CARLA's
    ``Map.get_waypoint_xodr`` recovers the exact waypoint from them, which
    was measured to round-trip to within 0.0000 m. The cached coordinates
    exist so the file can be read, plotted and reviewed without a running
    server, and so a load-time check can catch a file written against a
    different version of the town.

    :param id: Stable identifier, unique within the town.
    :param road_id: OpenDRIVE road ID.
    :param lane_id: OpenDRIVE lane ID. Its sign gives the driving direction,
        so a stop inherently serves one direction of the road.
    :param s: Distance in metres along the road's reference line.
    :param x: Cached world X of the waypoint, in metres.
    :param y: Cached world Y of the waypoint, in metres.
    :param z: Cached world Z of the waypoint, in metres.
    :param name: Optional human-readable label.
    """

    id: str
    road_id: int
    lane_id: int
    s: float
    x: float
    y: float
    z: float = 0.0
    name: str | None = None


def town_name(carla_map_name: str) -> str:
    """
    Reduce a CARLA map name to the bare town.

    CARLA reports ``Carla/Maps/Town01``; files are keyed on ``Town01`` so
    that a name from a live world and one typed by hand agree.
    :param carla_map_name: Name as CARLA reports it, or a bare town name.
    :return: The town name with any path prefix removed.
    """
    return carla_map_name.rsplit("/", 1)[-1]


def stops_path(town: str, directory: Path | None = None) -> Path:
    """
    Return the path of a town's authored stop file.
    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the files. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Path to ``<town>.stops.json``.
    """
    base = CARLA_MAPS_DIR if directory is None else directory
    return base / f"{town_name(town)}.stops.json"


def load_stops(town: str, directory: Path | None = None) -> list[CarlaStop]:
    """
    Read a town's authored stops.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the files. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The authored stops, in file order.
    :raises FileNotFoundError: If the town has no authored stop file.
    :raises ValueError: If the file is malformed, names a different town, or
        holds duplicate stop IDs.
    """
    path = stops_path(town, directory)
    if not path.exists():
        raise FileNotFoundError(
            f"no authored stops for {town_name(town)} at {path}"
        )
    document = json.loads(path.read_text())

    if town_name(document.get("map", "")) != town_name(town):
        raise ValueError(
            f"{path} declares map {document.get('map')!r}, "
            f"which is not {town_name(town)}"
        )

    stops = [
        CarlaStop(
            id=str(entry["id"]),
            road_id=int(entry["road_id"]),
            lane_id=int(entry["lane_id"]),
            s=float(entry["s"]),
            x=float(entry["x"]),
            y=float(entry["y"]),
            z=float(entry.get("z", 0.0)),
            name=entry.get("name"),
        )
        for entry in document["stops"]
    ]

    identifiers = [stop.id for stop in stops]
    if len(set(identifiers)) != len(identifiers):
        duplicates = sorted(
            {i for i in identifiers if identifiers.count(i) > 1}
        )
        raise ValueError(f"{path} has duplicate stop IDs: {duplicates}")
    return stops


def save_stops(
    town: str,
    stops: list[CarlaStop],
    directory: Path | None = None,
    note: str | None = None,
) -> Path:
    """
    Write a town's authored stops, overwriting any existing file.

    :param town: Town name, with or without CARLA's path prefix.
    :param stops: The stops to write, in the order they should be read.
    :param directory: Directory to write into. Defaults to the packaged
        ``carla_maps`` directory.
    :param note: Optional provenance line stored alongside the stops.
    :return: The path written.
    :raises ValueError: If two stops share an ID.
    """
    identifiers = [stop.id for stop in stops]
    if len(set(identifiers)) != len(identifiers):
        duplicates = sorted(
            {i for i in identifiers if identifiers.count(i) > 1}
        )
        raise ValueError(f"duplicate stop IDs: {duplicates}")

    document = {
        "schema_version": SCHEMA_VERSION,
        "map": town_name(town),
        "note": note,
        "stops": [
            {
                "id": stop.id,
                "name": stop.name,
                "road_id": stop.road_id,
                "lane_id": stop.lane_id,
                "s": round(stop.s, 3),
                "x": round(stop.x, 3),
                "y": round(stop.y, 3),
                "z": round(stop.z, 3),
            }
            for stop in stops
        ],
    }

    path = stops_path(town, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def available_towns(directory: Path | None = None) -> list[str]:
    """
    List the towns that have authored stops.
    :param directory: Directory to scan. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Sorted town names.
    """
    base = CARLA_MAPS_DIR if directory is None else directory
    if not base.exists():
        return []
    return sorted(
        path.name[: -len(".stops.json")]
        for path in base.glob("*.stops.json")
    )
