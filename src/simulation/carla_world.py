"""Getting a world up, whether the map is a CARLA town or one we generated.

CARLA ships its towns baked into the server, and ``load_world`` names one.
A map built from OpenStreetMap has no such presence: it exists only as an
OpenDRIVE file, and the server builds the road procedurally from it every
time through ``generate_opendrive_world``.

Which of the two applies is decided by the data, not by a list kept here: a
town is *generated* when a ``<town>.xodr`` sits beside its dump in
``carla_maps``. Adding another such map is a matter of dropping three files
in, with no code change.

Lives under ``simulation`` rather than ``map`` on purpose. ``map`` already
depends on ``simulation`` -- ``carla_calibrate`` imports ``CarlaBackend`` --
so putting it here keeps the dependency flowing one way. Both
``CarlaBackend`` and ``carla_dump`` read it.
"""

import xml.etree.ElementTree as ET

from pathlib import Path

import carla

from src.map.carla_stops import CARLA_MAPS_DIR, town_name

# What CARLA calls every procedurally generated world, whichever OpenDRIVE
# built it. There is no slot to record which map is up, so the name cannot
# distinguish two generated maps from each other -- see ``load`` for why
# that does not matter here.
GENERATED_MAP_NAME = "OpenDriveMap"

# How the road is built from OpenDRIVE. These are **not** CARLA's defaults
# and the two that differ are load-bearing, both measured on the campus maps
# in August 2026:
#
# * ``wall_height`` -- CARLA defaults to 1.0, which walls the road in. The
#   bus wedges against those walls: 2 of 8 legs completed with them, against
#   6 of 8 without.
# * ``additional_width`` -- CARLA defaults to 0.6. A generated map is a bare
#   ribbon of road with nothing beside it (geometry 4 m off the lane centre:
#   Town01 100%, a generated campus 67%), so any lane departure drops the
#   bus out of the world. Widening to 4.0 m took Ben-Gurion from 6/8 legs to
#   12/12.
#
# ``enable_mesh_visibility`` is off because the bus is driven headless and
# the mesh only costs memory; it does not affect physics.
OPENDRIVE_PARAMETERS = carla.OpendriveGenerationParameters(
    vertex_distance=2.0,
    max_road_length=500.0,
    wall_height=0.0,
    additional_width=4.0,
    smooth_junctions=True,
    enable_mesh_visibility=False,
    enable_pedestrian_navigation=False,
)


def xodr_path(town: str, directory: Path | None = None) -> Path:
    """
    Return the path an OpenDRIVE file for this town would occupy.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory to look in. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The path, which need not exist.
    """
    base = CARLA_MAPS_DIR if directory is None else directory
    return base / f"{town_name(town)}.xodr"


def is_generated(town: str, directory: Path | None = None) -> bool:
    """
    Whether this town is built from OpenDRIVE rather than baked into CARLA.
    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: True when an OpenDRIVE file exists for the town.
    """
    return xodr_path(town, directory).is_file()


def load(
    client: "carla.Client", town: str, directory: Path | None = None
) -> "carla.World":
    """
    Load a town, generating it from OpenDRIVE when that is what it is.

    A generated world is rebuilt unconditionally. CARLA reports the same map
    name for every one of them, so there is no way to ask the server which
    OpenDRIVE is currently up, and a stale world silently carried into the
    next trial is the contamination this exists to prevent. The rebuild was
    measured at about 2.3 s, which is cheap enough not to trade correctness
    for.
    :param client: Connected CARLA client.
    :param town: Town to load, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: The loaded world.
    :raises FileNotFoundError: If a generated town's OpenDRIVE is missing.
    """
    path = xodr_path(town, directory)
    if not path.is_file():
        return client.load_world(town_name(town))
    return client.generate_opendrive_world(
        path.read_text(encoding="utf-8"), OPENDRIVE_PARAMETERS
    )


def _lane_speed_kph(speed: ET.Element) -> float:
    """
    Read one OpenDRIVE lane ``<speed>`` element as km/h.
    :param speed: The ``<speed>`` element.
    :return: Its ``max`` in km/h, whatever unit it declares.
    """
    value = float(speed.get("max"))
    unit = speed.get("unit", "m/s")
    if unit == "m/s":
        return value * 3.6
    if unit == "mph":
        return value * 1.609344
    return value


def speed_limits(
    town: str, directory: Path | None = None
) -> dict[int, float]:
    """
    Read per-road speed limits out of a generated town's OpenDRIVE.

    ``carla_dump.read_speed_limits`` reads CARLA's type-274 landmarks, and a
    generated map carries **none** -- measured zero on every campus map,
    against Town01's 22 -- so without this every road would fall back to the
    town default and the whole map would be priced at one speed.

    The limits are not missing, only stored elsewhere: the converter writes
    OpenStreetMap's ``maxspeed`` onto each **driving lane** as a ``<speed>``
    element. A road is one edge in ``TransitGraph``, so one number per road
    is all the model can carry; where a road's driving lanes disagree the
    fastest wins, since that is the lane a bus would use.
    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Speed limit in km/h per OpenDRIVE road ID, omitting roads that
        declare none.
    :raises FileNotFoundError: If the town has no OpenDRIVE file.
    """
    path = xodr_path(town, directory)
    if not path.is_file():
        raise FileNotFoundError(f"{town} has no OpenDRIVE file at {path}")

    table: dict[int, float] = {}
    for road in ET.parse(path).getroot().findall("road"):
        fastest = 0.0
        for lane in road.iter("lane"):
            if lane.get("type") != "driving":
                continue
            for speed in lane.findall("speed"):
                if speed.get("max"):
                    fastest = max(fastest, _lane_speed_kph(speed))
        if fastest > 0.0:
            table[int(road.get("id"))] = fastest
    return table
