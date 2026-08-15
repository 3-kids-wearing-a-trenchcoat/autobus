"""Tests for CarlaBackend that need no server.

Everything here is construction and contract checking. The driving itself
cannot be unit tested — it needs a live CARLA — so it is exercised by the
walking-skeleton scripts instead, and their measured results are recorded in
`roadmap.md`.
"""

import pytest

from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.map.CarlaMapProvider import CarlaMapProvider
import carla

from src.simulation import carla_debug_text
from src.simulation.CarlaBackend import (
    DEBUG_HUD_REDRAW_S,
    DEBUG_LABEL_MAX_M,
    DEBUG_LABEL_MIN_M,
    DEBUG_LABEL_MAX_STOPS,
    DEBUG_MAX_SEGMENTS,
    DEBUG_ROUTE_THICKNESS_M,
    DEBUG_STOP_BEACON_M,
    DEBUG_STOP_IDLE_COLOR,
    DEBUG_STOP_WAITING_COLOR,
    DEBUG_REDRAW_S,
    DEBUG_ROUTE_STEP_M,
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


# ---------------------------------------------------------------------------
# _is_queued_behind_traffic — waiting for the car in front is not a stall
# ---------------------------------------------------------------------------

class _Vec:
    """Enough of carla.Vector3D for the projection arithmetic."""

    def __init__(self, x: float, y: float) -> None:
        self.x, self.y, self.z = x, y, 0.0

    def __sub__(self, other: "_Vec") -> "_Vec":
        return _Vec(self.x - other.x, self.y - other.y)

    def length(self) -> float:
        """Return the vector's magnitude."""
        return (self.x ** 2 + self.y ** 2) ** 0.5


class _Transform:
    """A pose that only has to answer for its location and heading."""

    def __init__(self, x: float, y: float) -> None:
        self.location = _Vec(x, y)

    def get_forward_vector(self) -> _Vec:
        """Point along +x, so 'ahead' is the x axis."""
        return _Vec(1.0, 0.0)


class _NpcSnapshot:
    """One vehicle as a world snapshot reports it."""

    def __init__(self, x: float, y: float, speed: float) -> None:
        self._transform = _Transform(x, y)
        self._speed = speed

    def get_transform(self) -> _Transform:
        """Where it is."""
        return self._transform

    def get_velocity(self) -> _Vec:
        """How fast it is going, along the road."""
        return _Vec(self._speed, 0.0)


class _SnapshotWorld:
    """A world that answers only with a canned snapshot."""

    def __init__(self, npcs: dict[int, _NpcSnapshot]) -> None:
        self._npcs = npcs

    def get_snapshot(self) -> "_SnapshotWorld":
        """The world is its own snapshot here."""
        return self

    def find(self, actor_id: int) -> _NpcSnapshot | None:
        """Look up one vehicle."""
        return self._npcs.get(actor_id)


class _BusActor:
    """A bus parked at the origin, facing +x."""

    def get_location(self) -> _Vec:
        """The bus sits at the origin."""
        return _Vec(0.0, 0.0)

    def get_transform(self) -> _Transform:
        """Facing along +x."""
        return _Transform(0.0, 0.0)


def _queued(*npcs: _NpcSnapshot) -> bool:
    """Ask the backend whether these vehicles would count as a queue."""
    backend = _backend()
    backend._npc_ids = list(range(len(npcs)))
    backend._world = _SnapshotWorld(dict(enumerate(npcs)))
    return backend._is_queued_behind_traffic(_BusActor())


def test_an_empty_town_costs_nothing_to_check() -> None:
    """
    With no traffic there is nothing to be blocked by, and every earlier
    measurement was taken in exactly that condition.
    """
    backend = _backend()
    backend._npc_ids = []
    backend._world = None  # never touched, which is the point
    assert backend._is_queued_behind_traffic(_BusActor()) is False


def test_a_stopped_car_directly_ahead_is_a_queue() -> None:
    assert _queued(_NpcSnapshot(8.0, 0.0, 0.0)) is True


def test_a_moving_car_ahead_is_not_a_queue() -> None:
    """If the car in front is moving, the bus has no excuse."""
    assert _queued(_NpcSnapshot(8.0, 0.0, 5.0)) is False


def test_a_car_behind_is_not_a_queue() -> None:
    assert _queued(_NpcSnapshot(-8.0, 0.0, 0.0)) is False


def test_a_car_in_the_next_lane_is_not_a_queue() -> None:
    """Otherwise oncoming traffic stopped alongside would mask a real wedge."""
    assert _queued(_NpcSnapshot(8.0, 6.0, 0.0)) is False


def test_a_car_far_up_the_road_is_not_a_queue() -> None:
    assert _queued(_NpcSnapshot(60.0, 0.0, 0.0)) is False


def test_one_blocker_among_many_is_enough() -> None:
    assert _queued(
        _NpcSnapshot(60.0, 0.0, 0.0),
        _NpcSnapshot(8.0, 0.5, 0.0),
        _NpcSnapshot(-3.0, 0.0, 0.0),
    ) is True


def test_queued_ticks_starts_at_zero() -> None:
    """The counter has to exist before anything can report on it."""
    assert _backend().stats()["queued_ticks"] == 0


# ---------------------------------------------------------------------------
# The debug overlay
#
# No server: carla.Location and carla.Transform are client-side value types,
# so a route can be built by hand and the renderer stood in for.
# ---------------------------------------------------------------------------

class _FakeWaypoint:
    """A waypoint that knows only where it is."""

    def __init__(self, x: float, y: float, z: float = 0.0) -> None:
        self.transform = carla.Transform(carla.Location(x=x, y=y, z=z))


class _FakeDebug:
    """Records what would have been drawn."""

    def __init__(self, explode: bool = False) -> None:
        self.lines: list[tuple[object, object]] = []
        self.points: list[object] = []
        self.strings: list[str] = []
        self.colors: list[object] = []
        self.thicknesses: list[float] = []
        self.boxes: list[object] = []
        self._explode = explode

    def draw_box(self, box, rotation, **kwargs) -> None:
        if self._explode:
            raise RuntimeError("renderer gone")
        self.boxes.append(box)
        self.colors.append(kwargs.get("color"))

    def draw_line(self, begin, end, **kwargs) -> None:
        if self._explode:
            raise RuntimeError("renderer gone")
        self.lines.append((begin, end))
        self.colors.append(kwargs.get("color"))
        self.thicknesses.append(kwargs.get("thickness"))

    def draw_point(self, location, **kwargs) -> None:
        if self._explode:
            raise RuntimeError("renderer gone")
        self.points.append(location)

    def draw_string(self, location, text, **kwargs) -> None:
        if self._explode:
            raise RuntimeError("renderer gone")
        self.strings.append(text)


class _FakeSpectator:
    """A camera parked at the origin, looking up the +x axis."""

    def get_transform(self) -> "carla.Transform":
        return carla.Transform(carla.Location(x=0.0, y=0.0, z=2.0),
                               carla.Rotation())


class _DebugWorld:
    """A world that offers nothing but a renderer, a camera and the bus."""

    def __init__(self, debug: _FakeDebug, bus: object = None,
                 spectator: object = None) -> None:
        self.debug = debug
        self._bus = bus
        # None stands for "no camera", which switches the counters off --
        # they are the only part of the overlay that needs one.
        self.spectator = spectator

    def get_actor(self, actor_id: int) -> object:
        return self._bus

    def get_spectator(self) -> "_FakeSpectator":
        return self.spectator


class _LocatedBus:
    """A bus actor that only knows where it stands."""

    def __init__(self, x: float, y: float) -> None:
        self._location = carla.Location(x=x, y=y, z=0.0)

    def get_location(self) -> "carla.Location":
        return self._location


def _straight_route(metres: float, step: float = 2.0) -> list[_FakeWaypoint]:
    """Build a straight route sampled the way plan_route samples one."""
    count = int(metres / step) + 1
    return [_FakeWaypoint(x=i * step, y=0.0) for i in range(count)]


def _drawing_backend(debug: _FakeDebug, **overrides: object) -> CarlaBackend:
    """Build a backend wired to a stand-in renderer."""
    backend = _backend(debug_draw=True, **overrides)
    backend._world = _DebugWorld(debug, _LocatedBus(50.0, 60.0))
    backend._bus_id = 1
    return backend


def test_debug_draw_defaults_off() -> None:
    """A campaign must not pay for an overlay nobody is watching."""
    assert _backend()._debug_draw is False


def test_the_route_is_thinned_before_it_is_drawn() -> None:
    """
    plan_route steps every 2 m. Drawing one segment each would be hundreds
    of calls per redraw, so the overlay samples every DEBUG_ROUTE_STEP_M.
    """
    backend = _drawing_backend(_FakeDebug())

    backend._remember_debug_route(_straight_route(100.0))

    # 100 m at 10 m spacing, plus the endpoint.
    assert len(backend._debug_route) == 11
    spacing = backend._debug_route[1].distance(backend._debug_route[0])
    assert spacing == pytest.approx(DEBUG_ROUTE_STEP_M)


def test_the_drawn_route_reaches_the_destination() -> None:
    """A line that stops at the last whole sample misses the stop."""
    backend = _drawing_backend(_FakeDebug())
    route = _straight_route(95.0)

    backend._remember_debug_route(route)

    final = route[-1].transform.location
    assert backend._debug_route[-1].x == pytest.approx(final.x)


def test_an_unroutable_leg_clears_the_overlay() -> None:
    """A stale line must not outlive the plan it described."""
    backend = _drawing_backend(_FakeDebug())
    backend._remember_debug_route(_straight_route(100.0))

    backend._remember_debug_route(None)

    assert backend._debug_route == []


def test_the_route_is_not_redrawn_every_tick() -> None:
    """No camera here, so only the route is under test, not the counters."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    backend._remember_debug_route(_straight_route(100.0))

    ticks = int(DEBUG_REDRAW_S / 0.05)
    for _ in range(ticks - 1):
        backend._draw_debug()
    assert debug.lines == []

    backend._draw_debug()
    assert len(debug.lines) == 10           # 11 points, 10 segments


def test_nothing_is_drawn_when_the_flag_is_off() -> None:
    debug = _FakeDebug()
    backend = _backend(debug_draw=False)
    backend._world = _DebugWorld(debug, _LocatedBus(0.0, 0.0))
    backend._bus_id = 1

    backend._remember_debug_route(_straight_route(100.0))
    for _ in range(int(DEBUG_REDRAW_S / 0.05) + 1):
        backend._draw_debug()

    assert backend._debug_route == []
    assert debug.lines == []


def test_a_dropped_route_leaves_a_marker_where_it_happened() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._stats["ticks"] = 4000          # 200 s at the default delta

    backend._mark_route_drop(1)

    assert len(debug.points) == 1
    assert debug.points[0].x == pytest.approx(50.0)
    assert debug.strings == ["drop t=200s"]


def test_several_drops_in_one_tick_say_so() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug)

    backend._mark_route_drop(3)

    assert debug.strings == ["drop t=0s x3"]


def test_a_failing_renderer_never_costs_the_run() -> None:
    """Same rule as a failing tag: a diagnostic must not abort a trial."""
    backend = _drawing_backend(_FakeDebug(explode=True))
    backend._remember_debug_route(_straight_route(100.0))

    for _ in range(int(DEBUG_REDRAW_S / 0.05) + 1):
        backend._draw_debug()
    backend._mark_route_drop(1)


def test_a_pathological_route_cannot_stall_the_tick() -> None:
    backend = _drawing_backend(_FakeDebug())

    backend._remember_debug_route(_straight_route(100000.0))

    assert len(backend._debug_route) <= DEBUG_MAX_SEGMENTS + 1


# ---------------------------------------------------------------------------
# Stop beacons
# ---------------------------------------------------------------------------

def _demand_pool(
    waiting: tuple[str, ...] = (), aboard: tuple[str, ...] = ()
) -> RequestPool:
    """
    Build a pool of passengers waiting at, and riding towards, given stops.

    :param waiting: Pickup stop for each PENDING request.
    :param aboard: Dropoff stop for each ON_BUS request.
    :return: The populated pool.
    """
    pool = RequestPool()
    for index, pickup in enumerate(waiting):
        pool.add(Request(
            id=f"w{index}", pickup_stop_id=pickup,
            dropoff_stop_id="n0002_n_s02", created_at=0.0,
        ))
    for index, dropoff in enumerate(aboard):
        pool.add(Request(
            id=f"b{index}", pickup_stop_id="n0001_n_s01",
            dropoff_stop_id=dropoff, created_at=0.0,
            state=RequestState.ON_BUS,
        ))
    return pool


def test_stop_demand_counts_both_directions() -> None:
    backend = _drawing_backend(_FakeDebug())

    backend._note_stop_demand(_demand_pool(
        waiting=("n0001_n_s01", "n0001_n_s01", "n0002_n_s02"),
        aboard=("n0002_n_s02",)))

    assert backend._stop_demand["n0001_n_s01"] == (2, 0)
    assert backend._stop_demand["n0002_n_s02"] == (1, 1)


def test_every_stop_gets_a_count_even_with_no_demand() -> None:
    """An idle stop is still drawn, so it needs a row of its own."""
    backend = _drawing_backend(_FakeDebug())

    backend._note_stop_demand(_demand_pool())

    assert backend._stop_demand == {
        "n0001_n_s01": (0, 0), "n0002_n_s02": (0, 0)}


def test_demand_for_an_unknown_stop_is_ignored() -> None:
    """A pool may outlive a stop table; it must not crash the overlay."""
    backend = _drawing_backend(_FakeDebug())

    backend._note_stop_demand(_demand_pool(waiting=("nowhere",)))

    assert "nowhere" not in backend._stop_demand


def test_demand_is_not_counted_when_the_flag_is_off() -> None:
    backend = _backend(debug_draw=False)

    backend._note_stop_demand(_demand_pool(waiting=("n0001_n_s01",)))

    assert backend._stop_demand == {}


def test_every_stop_gets_a_box_and_a_beacon() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool(
        waiting=("n0001_n_s01",), aboard=("n0002_n_s02",)))

    backend._draw_stops(debug, _FakeSpectator())

    assert len(debug.boxes) == 2                    # one box per stop
    beacons = 2
    strokes = sum(carla_debug_text.stroke_count(text) for text in (
        "n0001_n_s01", "waiting 1", "alighting 0",
        "n0002_n_s02", "waiting 0", "alighting 1"))
    # Beacons plus the lettering; nothing goes through draw_string any more,
    # which has no font size and is unreadable on a 4K display.
    assert len(debug.lines) == beacons + strokes
    assert debug.strings == []


def test_only_the_nearest_stops_are_lettered() -> None:
    """
    Stroke text costs a call per stroke. Fifteen stops of it would cost more
    than the whole rest of the overlay, and the far ones are illegible.
    """
    debug = _FakeDebug()
    far = {f"s{i}": (float(i) * 40.0, 0.0, 0.0) for i in range(12)}
    backend = _backend(debug_draw=True, stop_positions=far, position="s0")
    backend._world = _DebugWorld(debug, _LocatedBus(0.0, 0.0))
    backend._bus_id = 1
    backend._note_stop_demand(RequestPool())

    backend._draw_stops(debug, _FakeSpectator())

    assert len(debug.boxes) == 12                   # every stop is boxed
    lettered = backend._labelled_stops(carla.Location(x=0.0, y=0.0, z=2.0))
    assert len(lettered) == DEBUG_LABEL_MAX_STOPS
    assert lettered[0] == "s0"                      # nearest first


def test_the_beacon_stands_up_from_the_stop() -> None:
    """A marker on the road is invisible from a chase camera."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool())

    backend._draw_stops(debug, _FakeSpectator())

    foot, head = debug.lines[0]
    assert head.z - foot.z == pytest.approx(DEBUG_STOP_BEACON_M)
    assert (foot.x, foot.y) == pytest.approx((10.0, 20.0))


def test_waiting_outranks_alighting_in_the_colour() -> None:
    """
    Both at once is common. Waiting wins because a passenger not yet picked
    up is the one whose promise can still be broken.
    """
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool(
        waiting=("n0001_n_s01",), aboard=("n0001_n_s01",)))

    backend._draw_stops(debug, _FakeSpectator())

    assert backend._stop_demand["n0001_n_s01"] == (1, 1)
    assert debug.colors[0] == DEBUG_STOP_WAITING_COLOR


def test_an_idle_stop_is_drawn_dim() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool())

    backend._draw_stops(debug, _FakeSpectator())

    assert debug.colors[0] == DEBUG_STOP_IDLE_COLOR


def test_the_route_is_drawn_wide_enough_to_follow() -> None:
    """0.15 was the CARLA default and reads as a hairline from behind."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._remember_debug_route(_straight_route(100.0))

    backend._draw_route(debug)

    assert debug.thicknesses[0] == pytest.approx(DEBUG_ROUTE_THICKNESS_M)
    assert debug.thicknesses[0] > 0.15


# ---------------------------------------------------------------------------
# The camera-locked counters
# ---------------------------------------------------------------------------

def test_the_clock_reads_minutes_under_an_hour() -> None:
    assert CarlaBackend._clock(0) == "0:00"
    assert CarlaBackend._clock(9.7) == "0:09"
    assert CarlaBackend._clock(605) == "10:05"


def test_the_clock_grows_an_hours_field_when_it_needs_one() -> None:
    assert CarlaBackend._clock(3600) == "1:00:00"
    assert CarlaBackend._clock(7385) == "2:03:05"


def test_a_negative_duration_reads_as_zero() -> None:
    """A leg counter must never show a negative before its first leg."""
    assert CarlaBackend._clock(-5) == "0:00"


def test_the_leg_clock_restarts_when_a_destination_is_given() -> None:
    backend = _backend(debug_draw=True, fixed_delta_s=0.05)
    backend._stats["ticks"] = 2000                  # 100 s in

    backend.set_destination("n0002_n_s02")

    assert backend._leg_started_s == pytest.approx(100.0)


def test_re_issuing_the_same_destination_does_not_restart_the_leg() -> None:
    """The runner re-issues every pass; the counter must not reset with it."""
    backend = _backend(debug_draw=True, fixed_delta_s=0.05)
    backend.set_destination("n0002_n_s02")
    backend._stats["ticks"] = 4000

    backend.set_destination("n0002_n_s02")

    assert backend._leg_started_s == pytest.approx(0.0)


def test_the_counters_are_drawn_in_front_of_the_camera() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    backend._since_hud_draw_s = DEBUG_HUD_REDRAW_S

    backend._draw_hud(debug, _FakeSpectator())

    assert debug.lines, "nothing drawn"
    # The camera looks up +x from the origin, so the text must sit ahead.
    assert all(point.x > 0 for pair in debug.lines for point in pair)


def test_the_counters_say_the_time_the_leg_and_the_next_stop() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    backend._destination = "n0002_n_s02"
    backend._stats["ticks"] = 1200                  # 60 s
    backend._since_hud_draw_s = DEBUG_HUD_REDRAW_S

    backend._draw_hud(debug, _FakeSpectator())

    expected = sum(carla_debug_text.stroke_count(text) for text in (
        "SIM 1:00", "LEG 1:00", "NEXT n0002_n_s02"))
    assert len(debug.lines) == expected


def test_the_counters_are_not_redrawn_every_tick() -> None:
    """They follow the camera, so they run fast -- but not every tick."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    spectator = _FakeSpectator()

    for _ in range(int(DEBUG_HUD_REDRAW_S / 0.05) - 1):
        backend._draw_hud(debug, spectator)
    assert debug.lines == []

    backend._draw_hud(debug, spectator)
    assert debug.lines


def test_no_camera_means_no_counters() -> None:
    """There is nothing to pin them to, and no screen-space API to use."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    backend._since_hud_draw_s = DEBUG_HUD_REDRAW_S

    backend._draw_hud(debug, None)

    assert debug.lines == []


def test_a_label_holds_its_apparent_size_with_distance() -> None:
    """
    A fixed world height is unreadable across town and fills the screen at
    the stop the bus is parked at, which is where the chase camera lives.
    """
    near = CarlaBackend._label_height(20.0)
    far = CarlaBackend._label_height(60.0)
    assert far == pytest.approx(near * 3.0)


def test_label_height_is_clamped_at_both_ends() -> None:
    assert CarlaBackend._label_height(0.0) == DEBUG_LABEL_MIN_M
    assert CarlaBackend._label_height(10000.0) == DEBUG_LABEL_MAX_M
