"""Scenery blocks that make a generated town look like somewhere: the
schema, and reading and writing it.

A town generated from OpenDRIVE is a bare ribbon of road. There is no
terrain, no building and no street furniture -- ``get_environment_objects``
returns 52 things for TAU against 14,670 for Town01. That is fine for
driving and useless for showing someone what the bus is doing, so this
module carries a per-town list of blocks to stand in for the buildings.

The blocks are **decoration and nothing else.** They are placed clear of
every driving lane, no policy or metric can see them, and a town without a
props file behaves exactly as before. Positions are derived from
OpenStreetMap building footprints by ``src.preparation.osm_buildings``;
they are close enough to read as the right campus and are **not survey
accurate** -- see that module for why.

Deliberately free of any ``carla`` import, like the rest of the map layer.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from src.map.carla_stops import CARLA_MAPS_DIR, town_name

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SceneryBlock:
    """
    One rectangular block of scenery, in world coordinates.

    Sized to a building's footprint and tagged with the height its top
    should reach. ``height_m`` is the height **above ground**, not the size
    of the mesh: the cube used to draw it is scaled to ``edge_m`` and sunk
    so that only ``height_m`` shows, which is how a wide, low building is
    drawn from a mesh that can only scale uniformly.

    :param x: World X of the block's centre, in metres.
    :param y: World Y of the block's centre, in metres.
    :param edge_m: Edge length of the cube to place, in metres.
    :param height_m: Height above ground the block should reach, in metres.
    :param yaw_deg: Rotation about the vertical axis, in degrees.
    """

    x: float
    y: float
    edge_m: float
    height_m: float
    yaw_deg: float = 0.0


def props_path(town: str, directory: Path | None = None) -> Path:
    """
    Where a town's scenery file lives.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Path to the town's ``.props.json``, which need not exist.
    """
    base = CARLA_MAPS_DIR if directory is None else directory
    return base / f"{town_name(town)}.props.json"


def has_props(town: str, directory: Path | None = None) -> bool:
    """
    Whether a town has scenery authored for it.

    Most towns do not, and never will: CARLA's shipped towns come with
    their own art. Callers use this to skip the whole business rather than
    handle a missing file as an error.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: True if a scenery file exists for the town.
    """
    return props_path(town, directory).is_file()


def load_props(
    town: str, directory: Path | None = None
) -> list[SceneryBlock]:
    """
    Read a town's scenery blocks.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The blocks, in file order.
    :raises FileNotFoundError: If the town has no scenery file.
    :raises ValueError: If the file was written by a different schema.
    """
    path = props_path(town, directory)
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version {version}, expected "
            f"{SCHEMA_VERSION}"
        )
    return [
        SceneryBlock(
            x=float(block["x"]),
            y=float(block["y"]),
            edge_m=float(block["edge_m"]),
            height_m=float(block["height_m"]),
            yaw_deg=float(block.get("yaw_deg", 0.0)),
        )
        for block in payload["blocks"]
    ]


def save_props(
    town: str,
    blocks: list[SceneryBlock],
    note: str = "",
    directory: Path | None = None,
) -> Path:
    """
    Write a town's scenery blocks, replacing any existing file.

    :param town: Town name, with or without CARLA's path prefix.
    :param blocks: The blocks to record.
    :param note: Free text describing how the blocks were derived, kept in
        the file so a reader can tell what produced it.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The path written.
    """
    path = props_path(town, directory)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "map": town_name(town),
        "note": note,
        "blocks": [
            {
                "x": round(block.x, 3),
                "y": round(block.y, 3),
                "edge_m": round(block.edge_m, 3),
                "height_m": round(block.height_m, 3),
                "yaw_deg": round(block.yaw_deg, 3),
            }
            for block in blocks
        ],
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path
