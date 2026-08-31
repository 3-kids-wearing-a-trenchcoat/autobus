"""Tests for dressing a generated town.

No server: a fake world records what it was asked to spawn, and the tests
read that back. The arithmetic under test is the sinking of a uniformly
scaled cube so that a wide, low building draws at the right height.
"""

import pytest

# Guarded rather than imported bare: an ImportError at module scope is a
# *collection* error, and pytest abandons the whole run on the first one --
# so a missing optional dependency reports this project as entirely broken
# rather than as one suite that cannot run here.
pytest.importorskip("carla", reason="the CARLA client wheel is not installed")
import carla

from src.map.carla_props import SceneryBlock, save_props
from src.simulation import carla_scenery


class _FakeBlueprint:
    """Records the attributes set on it."""

    def __init__(self, blueprint_id: str) -> None:
        self.id = blueprint_id
        self.attributes: dict[str, str] = {}

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


class _FakeLibrary:
    def find(self, blueprint_id: str) -> _FakeBlueprint:
        return _FakeBlueprint(blueprint_id)


class _FakeActor:
    def __init__(self, actor_id: int) -> None:
        self.id = actor_id


class _FakeWaypoint:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.transform = carla.Transform(
            carla.Location(x=x, y=y, z=z), carla.Rotation()
        )
        self.lane_width = 3.5


class _FakeMap:
    def __init__(self, waypoints: list[_FakeWaypoint]) -> None:
        self._waypoints = waypoints

    def generate_waypoints(self, _spacing: float) -> list[_FakeWaypoint]:
        return list(self._waypoints)

    def get_waypoint(self, location, project_to_road=True):  # noqa: ANN001
        return _FakeWaypoint(location.x, location.y, location.z)


class _FakeWorld:
    """Accepts spawns and remembers them."""

    def __init__(self, waypoints: list[_FakeWaypoint] | None = None) -> None:
        self.spawned: list[tuple[_FakeBlueprint, carla.Transform]] = []
        self.weather = None
        self._map = _FakeMap(waypoints if waypoints is not None else [
            _FakeWaypoint(0.0, 0.0, 0.0), _FakeWaypoint(100.0, -50.0, 0.0),
        ])

    def get_blueprint_library(self) -> _FakeLibrary:
        return _FakeLibrary()

    def get_map(self) -> _FakeMap:
        return self._map

    def try_spawn_actor(self, blueprint, transform):  # noqa: ANN001
        self.spawned.append((blueprint, transform))
        return _FakeActor(len(self.spawned))

    def set_weather(self, weather) -> None:  # noqa: ANN001
        self.weather = weather


# ---------------------------------------------------------------------------
# The sinking arithmetic
# ---------------------------------------------------------------------------

def test_a_block_is_sunk_so_its_top_lands_at_its_height():
    """A 20 m cube drawn 8 m tall must sit 2 m below ground at its centre."""
    world = _FakeWorld()
    block = SceneryBlock(x=5.0, y=-7.0, edge_m=20.0, height_m=8.0)
    carla_scenery._spawn_blocks(world, [block])
    _, transform = world.spawned[0]
    assert transform.location.z == pytest.approx(8.0 - 10.0)
    # ...so the visible top is exactly the requested height.
    top = transform.location.z + block.edge_m / 2
    assert top == pytest.approx(block.height_m)


def test_a_block_carries_its_footprint_and_rotation():
    """Scale is the footprint edge, and the yaw is passed through."""
    world = _FakeWorld()
    carla_scenery._spawn_blocks(world, [
        SceneryBlock(x=1.0, y=2.0, edge_m=12.5, height_m=9.0, yaw_deg=42.0),
    ])
    blueprint, transform = world.spawned[0]
    assert blueprint.attributes["scale"] == "12.500"
    assert blueprint.attributes["mesh_path"] == carla_scenery.CUBE_MESH
    assert transform.rotation.yaw == pytest.approx(42.0)
    assert (transform.location.x, transform.location.y) == (1.0, 2.0)


# ---------------------------------------------------------------------------
# The ground plane
# ---------------------------------------------------------------------------

def test_the_ground_top_sits_just_below_the_lowest_road():
    """Any higher and it z-fights with the road surface."""
    world = _FakeWorld([
        _FakeWaypoint(0.0, 0.0, 0.0), _FakeWaypoint(50.0, -50.0, -1.5),
    ])
    carla_scenery._spawn_ground(world)
    _, transform = world.spawned[0]
    top = transform.location.z + carla_scenery.GROUND_EDGE_M / 2
    # `carla.Location` stores single precision, and the ground's centre is
    # 2 km down, so the round trip costs about a tenth of a millimetre.
    assert top == pytest.approx(
        -1.5 - carla_scenery.GROUND_CLEARANCE_M, abs=1e-3
    )


def test_no_ground_without_a_road_to_put_it_under():
    """An empty map is not something to guess a size from."""
    assert carla_scenery._spawn_ground(_FakeWorld([])) is None


# ---------------------------------------------------------------------------
# Stop posts
# ---------------------------------------------------------------------------

def test_a_post_is_placed_beside_each_stop():
    """One post per stop, and never in the lane."""
    world = _FakeWorld()
    positions = {"a": (0.0, 0.0, 0.0), "b": (10.0, 0.0, 0.0)}
    spawned = carla_scenery._spawn_stop_posts(world, positions)
    assert len(spawned) == 2
    for blueprint, _ in world.spawned:
        assert blueprint.id == carla_scenery.STOP_POST_BLUEPRINT
    # Offset to the kerb: half a lane plus the clearance.
    _, transform = world.spawned[0]
    assert transform.location.y == pytest.approx(
        3.5 / 2 + carla_scenery.STOP_POST_CLEARANCE_M
    )


# ---------------------------------------------------------------------------
# dress()
# ---------------------------------------------------------------------------

def test_a_town_without_scenery_is_left_alone(tmp_path):
    """Every shipped CARLA town takes this path."""
    world = _FakeWorld()
    assert carla_scenery.dress(world, "Town01", directory=tmp_path) == []
    assert world.spawned == []


def test_dress_places_ground_blocks_and_posts(tmp_path):
    """One ground, one cube per block, one post per stop."""
    save_props("TAU", [
        SceneryBlock(x=0.0, y=0.0, edge_m=10.0, height_m=6.0),
        SceneryBlock(x=20.0, y=0.0, edge_m=8.0, height_m=12.0),
    ], directory=tmp_path)
    world = _FakeWorld()
    actors = carla_scenery.dress(
        world, "TAU", {"a": (0.0, 0.0, 0.0)}, directory=tmp_path
    )
    assert len(actors) == 1 + 2 + 1
    ids = [blueprint.id for blueprint, _ in world.spawned]
    assert ids.count(carla_scenery.MESH_BLUEPRINT) == 3      # ground + 2
    assert ids.count(carla_scenery.STOP_POST_BLUEPRINT) == 1


def test_posts_are_skipped_when_no_stops_are_given(tmp_path):
    """The blocks stand on their own."""
    save_props("TAU", [
        SceneryBlock(x=0.0, y=0.0, edge_m=10.0, height_m=6.0),
    ], directory=tmp_path)
    world = _FakeWorld()
    carla_scenery.dress(world, "TAU", None, directory=tmp_path)
    ids = [blueprint.id for blueprint, _ in world.spawned]
    assert carla_scenery.STOP_POST_BLUEPRINT not in ids


def test_clearing_the_air_removes_the_fog():
    """The demo's weather, which the default hides the campus behind."""
    world = _FakeWorld()
    carla_scenery.clear_the_air(world)
    assert world.weather.fog_density == 0.0
    assert world.weather.wetness == 0.0
