"""CarlaBackend: a SimulationBackend driving a real bus in CARLA.

The whole point of Phase 2's seam: this is one class against three abstract
methods, and nothing above it changes. The runner still owns the clock, the
tick order, demand, abandonment and metrics; this only takes destinations and
reports arrivals.

Steering is `TrafficManager.set_path`, which keeps CARLA's own autonomous
driving in charge — what the design review settled on. The bus is an ordinary
autopilot vehicle that happens to be told where to go next.

**This module imports carla.** Nothing else under `src/simulation` does, and
nothing above the backend imports this.
"""

from src.algorithm.RequestPool import RequestPool
from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend

import carla

# The only blueprint CARLA 0.9.16 tags base_type=Bus. It is 10.3 x 3.9 m,
# and Town01's lanes are 3.5 m wide -- measured, it stalls on 85% of ticks
# and completes no legs at all. Usable only on a town with wider lanes.
FULL_SIZE_BUS_ID = "vehicle.mitsubishi.fusorosa"

# 5.9 x 2.0 m, a minibus. Completes the same route the full-size bus cannot,
# which is why it is the default until a wider town is chosen.
DEFAULT_BUS_ID = "vehicle.mercedes.sprinter"

# Above CARLA's substepping limit (max_substep_delta_time * max_substeps =
# 0.01 * 10) the physics silently stops keeping up and vehicle AI degrades —
# measured: at 0.5 s vehicles cover half the ground per simulated second.
MAX_SAFE_DELTA_S = 0.1


class CarlaBackend(SimulationBackend):
    """
    Drives a bus around a CARLA town, reporting arrivals at bus stops.

    :param stop_positions: TransitGraph node ID to world ``(x, y, z)`` for
        every stop the bus may be sent to. Built from a CarlaMapProvider's
        ``get_stops()``; taken as plain coordinates so this class needs no
        map layer of its own.
    :param position: TransitGraph node ID of the stop the bus starts at.
    :param town: Town to load. The server defaults to Town10HD on startup,
        so this is loaded unless it is already the current map.
    :param host: CARLA server host.
    :param port: CARLA server port.
    :param timeout_s: Client timeout in seconds.
    :param fixed_delta_s: Simulation step. Must not exceed
        ``MAX_SAFE_DELTA_S``; the runner's ``dt`` must be a whole multiple
        of it, or the two clocks drift.
    :param arrival_radius_m: How close counts as arrived. The bus is ~12 m
        long and its location is its centre, so this cannot be tight.
    :param dwell_s: Seconds the bus is held stationary at each stop reached.
    :param no_rendering: Run without rendering, which measured ~6x faster
        and does not affect vehicle AI — in synchronous mode the world
        advances by exactly ``fixed_delta_s`` per tick however long the tick
        took in wall time. Set False to watch it drive.
    :param npc_vehicles: Autopilot traffic to spawn. Each costs throughput.
    :param seed: Seed for the traffic manager and NPC placement.
    :param blueprint_id: Vehicle to drive. Defaults to a minibus, because
        the only true bus blueprint is wider than Town01's lanes and cannot
        get round the town at all — see ``FULL_SIZE_BUS_ID``.
    :param repath_after_s: Seconds of no movement before the destination is
        re-issued to the traffic manager. Cheap and often enough.
    :param unwedge_after_s: Seconds of no movement before the vehicle is
        lifted onto the nearest lane waypoint. Measured on Town01, a vehicle
        that jams against kerb geometry never recovers on its own and stalls
        for the rest of the run; without this a trial silently dies. Every
        use is counted in ``stats()`` because it is a departure from
        physical simulation. Set to 0 to disable.
    :raises ValueError: If ``fixed_delta_s`` exceeds the safe limit.
    """

    def __init__(
        self,
        stop_positions: dict[str, tuple[float, float, float]],
        position: str,
        town: str = "Town01",
        host: str = "localhost",
        port: int = 2000,
        timeout_s: float = 120.0,
        fixed_delta_s: float = 0.05,
        arrival_radius_m: float = 6.0,
        dwell_s: float = 0.0,
        no_rendering: bool = True,
        npc_vehicles: int = 0,
        seed: int = 0,
        blueprint_id: str = DEFAULT_BUS_ID,
        repath_after_s: float = 10.0,
        unwedge_after_s: float = 30.0,
    ) -> None:
        if fixed_delta_s > MAX_SAFE_DELTA_S:
            raise ValueError(
                f"fixed_delta_s={fixed_delta_s} exceeds {MAX_SAFE_DELTA_S}; "
                f"beyond CARLA's substepping limit the physics stops "
                f"keeping up and vehicle AI degrades"
            )
        if position not in stop_positions:
            raise ValueError(
                f"starting position {position!r} is not a known stop"
            )

        self._stop_positions = dict(stop_positions)
        self._position = position
        self._town = town
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._delta = fixed_delta_s
        self._arrival_radius_m = arrival_radius_m
        self._dwell_s = dwell_s
        self._no_rendering = no_rendering
        self._npc_vehicles = npc_vehicles
        self._seed = seed
        self._blueprint_id = blueprint_id
        self._repath_after_s = repath_after_s
        self._unwedge_after_s = unwedge_after_s

        self._client: carla.Client | None = None
        self._world: carla.World | None = None
        self._traffic_manager: carla.TrafficManager | None = None
        self._bus_id: int | None = None
        self._npc_ids: list[int] = []
        self._original_settings: carla.WorldSettings | None = None

        self._destination: str | None = None
        self._leg_armed = False
        self._dwell_remaining = 0.0
        self._stalled_s = 0.0
        self._since_repath_s = 0.0

        self._stats = {
            "ticks": 0,
            "arrivals": 0,
            "legs_started": 0,
            "distance_m": 0.0,
            "repaths": 0,
            "unwedges": 0,
        }
        self._last_location: carla.Location | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Connect, put the world in synchronous mode and spawn the bus.

        Separate from ``__init__`` precisely because this can fail and take
        seconds: there is no vehicle to place until there is a server.
        """
        self._client = carla.Client(self._host, self._port)
        self._client.set_timeout(self._timeout_s)

        world = self._client.get_world()
        if self._town not in world.get_map().name:
            world = self._client.load_world(self._town)
        self._world = world
        self._original_settings = world.get_settings()

        self._traffic_manager = self._client.get_trafficmanager()
        self._traffic_manager.set_synchronous_mode(True)
        self._traffic_manager.set_random_device_seed(self._seed)

        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.no_rendering_mode = self._no_rendering
        settings.fixed_delta_seconds = self._delta
        world.apply_settings(settings)

        self._spawn_npcs()
        self._spawn_bus()

    def close(self) -> None:
        """
        Destroy every actor and restore the world's original settings.

        Called from the runner's ``finally``, so it runs even when a trial
        fails part-way. Leaked actors are the classic cross-trial
        contaminant, and a world left in synchronous mode looks frozen to
        every other client.
        """
        if self._client is None or self._world is None:
            return
        actor_ids = [i for i in ([self._bus_id] + self._npc_ids)
                     if i is not None]
        if actor_ids:
            self._client.apply_batch_sync(
                [carla.command.DestroyActor(i) for i in actor_ids], True
            )
        self._bus_id = None
        self._npc_ids = []

        if self._traffic_manager is not None:
            self._traffic_manager.set_synchronous_mode(False)
        if self._original_settings is not None:
            self._world.apply_settings(self._original_settings)

    # ------------------------------------------------------------------
    # SimulationBackend interface
    # ------------------------------------------------------------------

    def set_destination(self, stop_id: str) -> None:
        """
        Hand the target to the traffic manager.

        Idempotent, because the runner re-issues the destination on every
        pass of its movement loop; re-pathing each time would make the bus
        hesitate. Still re-arms after an arrival, since a policy may
        legitimately re-issue the stop the bus is standing on.
        :param stop_id: TransitGraph node ID of the stop to drive to.
        :raises KeyError: If the stop is not one this backend knows.
        """
        if stop_id == self._destination and self._leg_armed:
            return
        if stop_id not in self._stop_positions:
            raise KeyError(f"unknown stop {stop_id!r}")

        self._destination = stop_id
        self._leg_armed = True
        self._stats["legs_started"] += 1

        bus = self._bus()
        if bus is not None and self._traffic_manager is not None:
            self._traffic_manager.set_path(
                bus, [self._location_of(stop_id)]
            )

    def advance(self, dt: float) -> AdvanceResult:
        """
        Tick the world until the stop is reached or the budget runs out.

        One arrival at most: the agent boards, alights and re-plans on each
        one, and the leg that follows is not known until it has.
        :param dt: Time budget in seconds. Must be a whole multiple of
            ``fixed_delta_s`` or the runner's clock and the world's drift.
        :return: AdvanceResult naming the stop reached, if any, and the time
            spent.
        :raises ValueError: If ``dt`` is not a multiple of the delta.
        """
        ticks = int(round(dt / self._delta))
        if abs(ticks * self._delta - dt) > 1e-9:
            raise ValueError(
                f"dt={dt} is not a whole multiple of "
                f"fixed_delta_seconds={self._delta}; the runner's clock and "
                f"CARLA's would drift apart"
            )

        for tick in range(ticks):
            self._tick_once()
            if self._destination is None or not self._leg_armed:
                continue
            if self._dwell_remaining > 0.0:
                continue
            if self._has_arrived():
                self._position = self._destination
                self._leg_armed = False
                self._dwell_remaining = self._dwell_s
                self._stats["arrivals"] += 1
                return AdvanceResult(self._position, (tick + 1) * self._delta)

        return AdvanceResult(None, dt)

    def current_location(self) -> str:
        """Return the last stop the bus reached."""
        return self._position

    def sync_passengers(self, pool: RequestPool) -> None:
        """
        No-op until passengers become walker actors.

        Left explicit rather than inherited so the walking skeleton says out
        loud that it carries nobody yet.
        :param pool: The shared request pool, ignored here.
        """

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, float]:
        """Return counters describing what the bus actually did."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _bus(self) -> "carla.Actor | None":
        """
        Return the bus actor, looked up fresh every time.

        Never cache the handle: touching a destroyed actor aborts the whole
        Python process from the C++ layer, uncatchably.
        :return: The actor, or None if it is gone.
        """
        if self._world is None or self._bus_id is None:
            return None
        return self._world.get_actor(self._bus_id)

    def _location_of(self, stop_id: str) -> "carla.Location":
        """Return the world location of a known stop."""
        x, y, z = self._stop_positions[stop_id]
        return carla.Location(x=x, y=y, z=z)

    def _tick_once(self) -> None:
        """Advance the world one step and accumulate diagnostics."""
        self._world.tick()
        self._stats["ticks"] += 1

        bus = self._bus()
        if bus is None:
            return

        if self._dwell_remaining > 0.0:
            # Hold the bus still for the dwell. Autopilot is left on so the
            # traffic manager keeps its route; the brake simply overrides it.
            bus.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            self._dwell_remaining -= self._delta
            if self._dwell_remaining <= 0.0:
                self._dwell_remaining = 0.0
                bus.apply_control(carla.VehicleControl(brake=0.0))

        here = bus.get_location()
        if self._last_location is not None:
            step = here.distance(self._last_location)
            self._stats["distance_m"] += step
            self._track_stall(bus, step)
        self._last_location = here

    def _track_stall(self, bus: "carla.Actor", step: float) -> None:
        """
        Notice a bus that has stopped making progress, and free it.

        Measured on Town01: a vehicle that wedges against kerb geometry at a
        junction never recovers, and every subsequent leg reports zero
        movement for the rest of the run. Re-issuing the path is tried
        first because it is free and physically honest; lifting the vehicle
        onto the lane is a last resort and is counted.
        :param bus: The bus actor.
        :param step: Metres travelled in the tick just taken.
        """
        if self._dwell_remaining > 0.0:
            return
        if step > 0.05:
            self._stalled_s = 0.0
            self._since_repath_s = 0.0
            return

        # Deliberately not gated on throttle. A wedged bus is one the
        # traffic manager has given up on, and it commands *zero* throttle —
        # gating on throttle > 0 was measured to never fire at all. Time is
        # the only reliable discriminator, so the thresholds are set well
        # past any credible red light.

        self._stalled_s += self._delta

        # Two independent clocks on purpose. Decrementing the stall total on
        # each repath was measured to starve the unwedge threshold outright:
        # repathing fired every 10 s forever, unwedge never once, and a bus
        # the traffic manager had abandoned stayed abandoned for the whole
        # run. Repath cadence must not reset how long we have been stuck.
        if (self._unwedge_after_s > 0.0
                and self._stalled_s >= self._unwedge_after_s):
            self._unwedge(bus)
            self._stalled_s = 0.0
            self._since_repath_s = 0.0
            return

        self._since_repath_s += self._delta
        if self._since_repath_s >= self._repath_after_s:
            self._repath(bus)
            self._since_repath_s = 0.0

    def _repath(self, bus: "carla.Actor") -> None:
        """Re-issue the current destination to the traffic manager."""
        if self._destination is None or self._traffic_manager is None:
            return
        self._traffic_manager.set_path(
            bus, [self._location_of(self._destination)]
        )
        self._stats["repaths"] += 1

    def _unwedge(self, bus: "carla.Actor") -> None:
        """
        Lift a jammed bus back onto the lane, a little way ahead.

        Deliberately crude and deliberately counted: it is not physics, and
        a run that needs it often is telling you the vehicle is too big for
        the town.
        :param bus: The bus actor to free.
        """
        carla_map = self._world.get_map()
        waypoint = carla_map.get_waypoint(
            bus.get_location(), lane_type=carla.LaneType.Driving
        )
        ahead = waypoint.next(6.0)
        target = ahead[0] if ahead else waypoint

        transform = target.transform
        transform.location.z += 0.5
        bus.set_target_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        bus.set_target_angular_velocity(carla.Vector3D(0.0, 0.0, 0.0))
        bus.set_transform(transform)
        self._stats["unwedges"] += 1
        self._repath(bus)

    def _has_arrived(self) -> bool:
        """
        Return whether the bus is close enough to its destination.

        Proximity only, never exact waypoint equality: the traffic manager
        routes *through* a location rather than stopping on it, and the bus
        is about twelve metres long with its origin at the centre.
        :return: True if the destination has been reached.
        """
        bus = self._bus()
        if bus is None or self._destination is None:
            return False
        target = self._location_of(self._destination)
        return bus.get_location().distance(target) <= self._arrival_radius_m

    def _spawn_bus(self) -> None:
        """
        Place the bus at its starting stop and enable autopilot.

        :raises RuntimeError: If no nearby spawn point is free.
        """
        blueprint = self._world.get_blueprint_library().find(
            self._blueprint_id
        )
        carla_map = self._world.get_map()
        start = self._location_of(self._position)
        waypoint = carla_map.get_waypoint(
            start, lane_type=carla.LaneType.Driving
        )

        bus = None
        # The stop itself may be blocked; try a little way along the lane.
        for offset in (0.0, 5.0, 10.0, 20.0, -5.0):
            candidates = (
                [waypoint] if offset == 0.0
                else (waypoint.next(offset) if offset > 0
                      else waypoint.previous(-offset))
            )
            for candidate in candidates:
                transform = candidate.transform
                transform.location.z += 0.5
                bus = self._world.try_spawn_actor(blueprint, transform)
                if bus is not None:
                    break
            if bus is not None:
                break
        if bus is None:
            raise RuntimeError(
                f"could not spawn the bus near stop {self._position}"
            )

        self._bus_id = bus.id
        bus.set_autopilot(True, self._traffic_manager.get_port())
        self._last_location = bus.get_location()

        # Let the vehicle settle onto its suspension before anything reads
        # its position or speed.
        for _ in range(int(round(0.5 / self._delta))):
            self._world.tick()
            self._stats["ticks"] += 1

    def _spawn_npcs(self) -> None:
        """Spawn autopilot traffic, if any was asked for."""
        if self._npc_vehicles <= 0:
            return
        import random

        blueprints = self._world.get_blueprint_library().filter("vehicle.*")
        points = self._world.get_map().get_spawn_points()
        generator = random.Random(self._seed)
        chosen = generator.sample(
            points, min(self._npc_vehicles, len(points))
        )
        for transform in chosen:
            actor = self._world.try_spawn_actor(
                generator.choice(blueprints), transform
            )
            if actor is not None:
                actor.set_autopilot(True, self._traffic_manager.get_port())
                self._npc_ids.append(actor.id)


def stop_positions_from(provider: object) -> dict[str, tuple[float, float,
                                                             float]]:
    """
    Build the stop table CarlaBackend needs from a map provider.

    Keeps the backend free of any map-layer import: it only ever sees plain
    coordinates keyed by TransitGraph node ID.
    :param provider: Any MapProvider whose stops carry world coordinates.
    :return: Mapping from stop node ID to ``(x, y, z)``.
    """
    return {
        stop.edge.id: (stop.x, stop.y, stop.z)
        for stop in provider.get_stops()
    }
