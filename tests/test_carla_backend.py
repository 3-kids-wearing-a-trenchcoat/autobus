"""Tests for CarlaBackend that need no server.

Everything here is construction and contract checking. The driving itself
cannot be unit tested — it needs a live CARLA — so it is exercised by the
walking-skeleton scripts instead, and their measured results are recorded in
`roadmap.md`.
"""

import pytest

from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.map.CarlaMapProvider import CarlaMapProvider
from src.simulation.CarlaBackend import (
    DEFAULT_BUS_ID,
    FULL_SIZE_BUS_ID,
    MAX_SAFE_DELTA_S,
    WALKER_LATERAL_OFFSET_M,
    CarlaBackend,
    stop_positions_from,
)
from src.simulation.SimulationBackend import SimulationBackend

_POSITIONS = {
    "n0001_n_s01": (10.0, 20.0, 0.0),
    "n0002_n_s02": (30.0, 40.0, 0.0),
}


def _backend(**overrides: object) -> CarlaBackend:
    """Build a backend without connecting to anything."""
    arguments = dict(
        stop_positions=_POSITIONS, position="n0001_n_s01",
    )
    arguments.update(overrides)
    return CarlaBackend(**arguments)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# It is a SimulationBackend
# ---------------------------------------------------------------------------

def test_is_a_simulation_backend() -> None:
    assert isinstance(_backend(), SimulationBackend)


def test_constructing_touches_no_server() -> None:
    """
    All the work belongs in start(). Constructing must stay cheap and
    failure-free, since CARLA has no vehicle to place until it has a server.
    """
    backend = _backend()
    assert backend.current_location() == "n0001_n_s01"


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

def test_an_unsafe_delta_is_rejected() -> None:
    """
    Beyond max_substep_delta_time * max_substeps the physics silently stops
    keeping up: measured, vehicles cover half the ground per simulated
    second at 0.5 s. Refusing loudly beats degrading quietly.
    """
    with pytest.raises(ValueError, match="substepping"):
        _backend(fixed_delta_s=0.5)


def test_the_safe_delta_limit_matches_carlas_substepping() -> None:
    assert MAX_SAFE_DELTA_S == pytest.approx(0.1)


def test_a_delta_at_the_limit_is_allowed() -> None:
    assert _backend(fixed_delta_s=MAX_SAFE_DELTA_S) is not None


def test_an_unknown_starting_stop_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a known stop"):
        _backend(position="nowhere")


def test_an_unknown_destination_is_rejected() -> None:
    with pytest.raises(KeyError):
        _backend().set_destination("nowhere")


# ---------------------------------------------------------------------------
# The advance() contract
# ---------------------------------------------------------------------------

def test_a_dt_that_is_not_a_multiple_of_the_delta_is_rejected() -> None:
    """
    CARLA can only advance in whole ticks. Accepting an odd dt would drift
    the runner's clock against the world's, silently, forever.
    """
    with pytest.raises(ValueError, match="multiple"):
        _backend(fixed_delta_s=0.05).advance(0.07)


def test_a_whole_multiple_of_the_delta_is_accepted() -> None:
    """No server, so this reaches the tick and fails there, not on dt."""
    backend = _backend(fixed_delta_s=0.05)
    with pytest.raises(Exception) as caught:
        backend.advance(1.0)
    assert "multiple" not in str(caught.value)


# ---------------------------------------------------------------------------
# The vehicle
# ---------------------------------------------------------------------------

def test_the_default_vehicle_is_not_the_full_size_bus() -> None:
    """
    The only base_type=Bus blueprint is 3.9 m wide and Town01's lanes are
    3.5 m. Measured on a fixed route it stalled 85% of ticks and completed
    no legs at all, while every narrower vehicle completed the same route.
    """
    assert DEFAULT_BUS_ID != FULL_SIZE_BUS_ID


# ---------------------------------------------------------------------------
# stop_positions_from
# ---------------------------------------------------------------------------

def test_stop_positions_are_keyed_by_graph_node_id() -> None:
    """
    The backend is commanded with TransitGraph node IDs, which are edge IDs
    — not the authored stop labels.
    """
    provider = CarlaMapProvider("Town01")
    positions = stop_positions_from(provider)
    assert len(positions) == 15
    for stop in provider.get_stops():
        assert positions[stop.edge.id] == (stop.x, stop.y, stop.z)


def test_a_provider_can_be_driven_without_a_server() -> None:
    """Building the backend from a real map must not need CARLA running."""
    provider = CarlaMapProvider("Town01")
    positions = stop_positions_from(provider)
    first = provider.get_stops()[0].edge.id
    assert CarlaBackend(positions, first).current_location() == first


# ---------------------------------------------------------------------------
# sync_passengers — the diff against the pool
# ---------------------------------------------------------------------------

class _FakeClient:
    """Records batch commands instead of talking to a server."""

    def __init__(self) -> None:
        self.destroyed = 0

    def apply_batch_sync(self, commands: list, _flag: bool) -> None:
        """Count the actors a batch would destroy."""
        self.destroyed += len(commands)


def _pooled(*requests: tuple[str, str]) -> RequestPool:
    """Build a pool of PENDING requests as (id, pickup_stop) pairs."""
    pool = RequestPool()
    for request_id, pickup in requests:
        pool.add(Request(
            id=request_id, pickup_stop_id=pickup,
            dropoff_stop_id="n0002_n_s02", created_at=0.0,
        ))
    return pool


def _wired(monkeypatch: pytest.MonkeyPatch) -> tuple[CarlaBackend, list]:
    """A backend with spawning stubbed out, so no server is needed."""
    backend = _backend()
    backend._world = object()          # only checked for None
    backend._client = _FakeClient()
    spawned: list[tuple[str, str]] = []

    def fake_spawn(request_id: str, stop_id: str) -> None:
        spawned.append((request_id, stop_id))
        backend._walkers[request_id] = len(spawned)
        backend._walker_stops[request_id] = stop_id

    monkeypatch.setattr(backend, "_spawn_walker", fake_spawn)
    return backend, spawned


def test_sync_before_start_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner calls this every tick, including before start()."""
    _backend().sync_passengers(_pooled(("r1", "n0001_n_s01")))


def test_a_waiting_request_gets_a_walker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, spawned = _wired(monkeypatch)
    backend.sync_passengers(_pooled(("r1", "n0001_n_s01")))
    assert spawned == [("r1", "n0001_n_s01")]


def test_syncing_twice_does_not_duplicate_walkers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Called every tick, so this is the common case, not an edge case."""
    backend, spawned = _wired(monkeypatch)
    pool = _pooled(("r1", "n0001_n_s01"))
    backend.sync_passengers(pool)
    backend.sync_passengers(pool)
    backend.sync_passengers(pool)
    assert len(spawned) == 1


def test_boarding_removes_the_walker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _wired(monkeypatch)
    pool = _pooled(("r1", "n0001_n_s01"))
    backend.sync_passengers(pool)
    pool.update_state("r1", RequestState.ON_BUS)
    backend.sync_passengers(pool)
    assert backend._walkers == {}
    assert backend._client.destroyed == 1


def test_abandoning_removes_the_walker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passenger who gives up stops being rendered too."""
    backend, _ = _wired(monkeypatch)
    pool = _pooled(("r1", "n0001_n_s01"))
    backend.sync_passengers(pool)
    pool.cancel("r1")
    backend.sync_passengers(pool)
    assert backend._walkers == {}


def test_several_waiting_passengers_each_get_a_walker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, spawned = _wired(monkeypatch)
    backend.sync_passengers(_pooled(
        ("r1", "n0001_n_s01"), ("r2", "n0001_n_s01"),
        ("r3", "n0002_n_s02"),
    ))
    assert len(spawned) == 3


def test_removal_forgets_which_stop_a_walker_was_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The per-stop count drives how walkers are fanned out along the shoulder,
    so a stale entry would push later arrivals further and further away.
    """
    backend, _ = _wired(monkeypatch)
    pool = _pooled(("r1", "n0001_n_s01"))
    backend.sync_passengers(pool)
    pool.update_state("r1", RequestState.ON_BUS)
    backend.sync_passengers(pool)
    assert backend._walker_stops == {}


def test_walkers_stand_clear_of_the_driving_lane() -> None:
    """
    Measured on Town01: the shoulder centre clears a 4 m lane's edge by only
    0.15 m, which is less than a walker's own body. A pedestrian in the road
    makes the traffic manager brake, and a walker with no AI controller will
    not move aside.
    """
    assert WALKER_LATERAL_OFFSET_M > 0.0


# ---------------------------------------------------------------------------
# _acquire_world — a clean world per trial
# ---------------------------------------------------------------------------

class _FakeMap:
    """Answers for its name, which is all the town check reads."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeWorld:
    """A world that knows which map it is."""

    def __init__(self, name: str) -> None:
        self._map = _FakeMap(name)

    def get_map(self) -> _FakeMap:
        """Return this world's map."""
        return self._map


class _WorldClient:
    """Records town loads instead of talking to a server."""

    def __init__(self, current_town: str) -> None:
        self.current = _FakeWorld(f"Carla/Maps/{current_town}")
        self.loaded: list[str] = []

    def get_world(self) -> _FakeWorld:
        """Return whatever world is already up."""
        return self.current

    def load_world(self, town: str) -> _FakeWorld:
        """Record the load and hand back a new world."""
        self.loaded.append(town)
        return _FakeWorld(f"Carla/Maps/{town}")


def _with_client(current_town: str, **overrides: object) -> tuple:
    """Build a backend wired to a fake client, and return both."""
    backend = _backend(**overrides)
    client = _WorldClient(current_town)
    backend._client = client
    return backend, client


def test_the_right_town_is_not_reloaded_by_default() -> None:
    """
    A map load costs seconds, and an interactive re-run must not pay it.
    """
    backend, client = _with_client("Town01", town="Town01")
    world = backend._acquire_world()
    assert client.loaded == []
    assert world is client.current


def test_the_wrong_town_is_always_loaded() -> None:
    """A fresh server starts on Town10HD, so this is the common case."""
    backend, client = _with_client("Town10HD_Opt", town="Town01")
    backend._acquire_world()
    assert client.loaded == ["Town01"]


def test_reload_world_loads_a_town_already_up() -> None:
    """
    The campaign's reason for existing: trial 2 must not inherit trial 1's
    world. close() destroys the actors this backend made, but a trial that
    failed part-way may have left others behind.
    """
    backend, client = _with_client(
        "Town01", town="Town01", reload_world=True
    )
    world = backend._acquire_world()
    assert client.loaded == ["Town01"]
    assert world is not client.current


def test_reload_world_defaults_off() -> None:
    """The demo notebook's behaviour must be what an unset flag gives."""
    assert _backend()._reload_world is False
