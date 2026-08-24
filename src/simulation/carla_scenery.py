"""Dress a generated town so a viewer can tell what they are looking at.

A town built from OpenDRIVE has no terrain and no buildings -- only the
road ribbon, floating in fog. With the fog cleared you can see sky through
the gaps between roads. This module lays a ground plane under the town,
stands a block where each building is, and puts a post at each stop.

**Decoration only, and demo-only in practice.** Everything here is spawned
clear of the driving lanes, nothing above the backend can see it, and a
town with no scenery file is left exactly as it was. It is kept out of
measured runs not because it changes them -- ``enable_mesh_visibility`` was
measured bit-identical over 500 ticks of road contact, and these props
never touch a lane -- but because a campaign has nobody watching it and
the props cost about a third of the tick budget for nothing.

CARLA ships no building mesh, so a block is ``/Engine/BasicShapes/Cube``
scaled to the footprint and **sunk** so only its intended height shows;
``static.prop.mesh`` scales uniformly, so that is the only way to draw
something wider than it is tall.
"""

from pathlib import Path

import carla

from src.map.carla_props import SceneryBlock, has_props, load_props

# The one mesh in the engine that is a plain box. A ``mesh_path`` that does
# not resolve spawns an actor with an infinite bounding box and draws
# nothing, raising no error at all, so this is not a string to guess at.
CUBE_MESH = "/Engine/BasicShapes/Cube.Cube"
MESH_BLUEPRINT = "static.prop.mesh"

# A signpost. Chosen over ``static.prop.busstop`` because a shelter is
# large enough to read as an obstacle beside a narrow generated lane.
STOP_POST_BLUEPRINT = "static.prop.streetsign01"
STOP_POST_CLEARANCE_M = 2.0

# One enormous cube standing in for terrain. Sunk so its top sits just
# below the lowest road: any higher and it z-fights with the road surface.
GROUND_EDGE_M = 4000.0
GROUND_CLEARANCE_M = 0.15


def _spawn_cube(
    world: "carla.World",
    x: float,
    y: float,
    z: float,
    edge_m: float,
    yaw_deg: float = 0.0,
) -> "carla.Actor | None":
    """
    Place one uniformly scaled cube.

    :param world: The world to spawn into.
    :param x: World X of the cube's centre, in metres.
    :param y: World Y of the cube's centre, in metres.
    :param z: World Z of the cube's centre, in metres.
    :param edge_m: Edge length, in metres.
    :param yaw_deg: Rotation about the vertical axis, in degrees.
    :return: The spawned actor, or None if CARLA refused the spot.
    """
    blueprint = world.get_blueprint_library().find(MESH_BLUEPRINT)
    blueprint.set_attribute("mesh_path", CUBE_MESH)
    blueprint.set_attribute("scale", f"{edge_m:.3f}")
    return world.try_spawn_actor(
        blueprint,
        carla.Transform(carla.Location(x=x, y=y, z=z),
                        carla.Rotation(yaw=yaw_deg)),
    )


def _spawn_ground(world: "carla.World") -> "carla.Actor | None":
    """
    Lay a ground plane under the whole road network.

    :param world: The world to spawn into.
    :return: The spawned actor, or None if CARLA refused the spot.
    """
    waypoints = world.get_map().generate_waypoints(25.0)
    if not waypoints:
        return None
    xs = [w.transform.location.x for w in waypoints]
    ys = [w.transform.location.y for w in waypoints]
    lowest = min(w.transform.location.z for w in waypoints)
    top = lowest - GROUND_CLEARANCE_M
    return _spawn_cube(
        world,
        (min(xs) + max(xs)) / 2,
        (min(ys) + max(ys)) / 2,
        top - GROUND_EDGE_M / 2,
        GROUND_EDGE_M,
    )


def _spawn_blocks(
    world: "carla.World", blocks: list[SceneryBlock]
) -> list["carla.Actor"]:
    """
    Stand a cube where each building is.

    :param world: The world to spawn into.
    :param blocks: The blocks to place.
    :return: The actors that were successfully spawned.
    """
    spawned = []
    for block in blocks:
        # Sink the cube so its top lands at height_m: a uniformly scaled
        # mesh cannot be short and wide, but it can be buried.
        actor = _spawn_cube(
            world, block.x, block.y,
            block.height_m - block.edge_m / 2,
            block.edge_m, block.yaw_deg,
        )
        if actor is not None:
            spawned.append(actor)
    return spawned


def _spawn_stop_posts(
    world: "carla.World",
    stop_positions: dict[str, tuple[float, float, float]],
) -> list["carla.Actor"]:
    """
    Put a post beside each stop, on the kerb side of its lane.

    Takes plain coordinates rather than the map layer's stop records,
    matching ``CarlaBackend``, which is deliberately free of map-layer
    types.

    :param world: The world to spawn into.
    :param stop_positions: Stop ID to its world (x, y, z).
    :return: The actors that were successfully spawned.
    """
    carla_map = world.get_map()
    spawned = []
    for x, y, z in stop_positions.values():
        waypoint = carla_map.get_waypoint(
            carla.Location(x=x, y=y, z=z), project_to_road=True,
        )
        if waypoint is None:
            continue
        transform = waypoint.transform
        offset = transform.get_right_vector() * (
            waypoint.lane_width / 2 + STOP_POST_CLEARANCE_M
        )
        blueprint = world.get_blueprint_library().find(STOP_POST_BLUEPRINT)
        actor = world.try_spawn_actor(
            blueprint,
            carla.Transform(transform.location + offset, transform.rotation),
        )
        if actor is not None:
            spawned.append(actor)
    return spawned


def dress(
    world: "carla.World",
    town: str,
    stop_positions: dict[str, tuple[float, float, float]] | None = None,
    directory: Path | None = None,
) -> list["carla.Actor"]:
    """
    Add ground, buildings and stop posts to a generated town.

    Silent and harmless for a town with no scenery file, which is every
    shipped CARLA town -- they come with their own art.

    :param world: The loaded world to dress.
    :param town: Town name, with or without CARLA's path prefix.
    :param stop_positions: Stop ID to its world (x, y, z), for the posts.
        None or empty places no posts.
    :param directory: Directory holding the maps. Defaults to the packaged
        ``carla_maps`` directory.
    :return: Every actor spawned, for the caller to destroy on close.
    """
    if not has_props(town, directory):
        return []
    spawned: list["carla.Actor"] = []
    ground = _spawn_ground(world)
    if ground is not None:
        spawned.append(ground)
    spawned.extend(_spawn_blocks(world, load_props(town, directory)))
    if stop_positions:
        spawned.extend(_spawn_stop_posts(world, stop_positions))
    return spawned


def clear_the_air(world: "carla.World") -> None:
    """
    Switch to bright, fogless weather.

    The default weather hides the campus in fog from a few hundred metres,
    which matters more here than in a shipped town: there is no terrain to
    give the eye anything nearer to hold on to.

    :param world: The world to set the weather on.
    """
    weather = carla.WeatherParameters.ClearNoon
    weather.fog_density = 0.0
    weather.fog_distance = 0.0
    weather.wetness = 0.0
    world.set_weather(weather)
