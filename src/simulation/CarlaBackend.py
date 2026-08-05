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

import random

from src.algorithm.RequestPool import RequestPool
from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend

import carla

# The only blueprint CARLA 0.9.16 tags base_type=Bus. It is 10.3 x 3.9 m,
# and Town01's lanes are 3.5 m wide -- measured, it stalls on 85% of ticks
# and completes no legs at all. Usable only on a town with wider lanes.
FULL_SIZE_BUS_ID = "vehicle.mitsubishi.fusorosa"

# 5.9 x 2.0 m, a minibus. Completes the same route the full-size bus cannot,
# which is why it is the default until a wider town is chosen.
DEFAULT_BUS_ID = "vehicle.volkswagen.t2_2021"

# Above CARLA's substepping limit (max_substep_delta_time * max_substeps =
# 0.01 * 10) the physics silently stops keeping up and vehicle AI degrades —
# measured: at 0.5 s vehicles cover half the ground per simulated second.
MAX_SAFE_DELTA_S = 0.1
# MAX_SAFE_DELTA_S = 1.5

# How far right of the shoulder's centre a waiting passenger stands.
# Measured on Town01: the shoulder centre sits 2.15 m from a 4.0 m lane's
# centre, which clears the lane edge by only 0.15 m -- less than a walker's
# own body. This pushes them properly out of the road.
WALKER_LATERAL_OFFSET_M = 1.0

# Chase camera placement, relative to the bus.
CAMERA_BEHIND_M = 9.0
CAMERA_ABOVE_M = 4.5
CAMERA_PITCH_DEG = -14.0


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
        lifted onto the nearest lane waypoint. **Off by default**, because
        it teleports: a vehicle that jams against kerb geometry does not
        recover on its own, but lifting it is not physics, and a leg saved
        that way is a KPI that did not happen. With the default bus it is
        also simply unnecessary — measured over five Town01 tours it never
        fires once. Worth enabling only for a vehicle that genuinely wedges,
        such as ``FULL_SIZE_BUS_ID``, which needs 12 over the same tours.
        Every use is counted in ``stats()``.
    :param follow_camera: Move the spectator camera to sit behind and above
        the bus on every tick, so a rendering server shows a chase view.
        Pointless with ``no_rendering=True``, where there is nothing to look
        at. Defaults to False.
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
        unwedge_after_s: float = 0.0,
        follow_camera: bool = False,
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
        self._follow_camera = follow_camera

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

        # Request ID to walker actor ID, and to the stop it waits at.
        self._walkers: dict[str, int] = {}
        self._walker_stops: dict[str, str] = {}
        self._walker_rng = random.Random(seed)

        self._stats = {
            "ticks": 0,
            "arrivals": 0,
            "legs_started": 0,
            "distance_m": 0.0,
            "repaths": 0,
            "unwedges": 0,
            "red_light_ticks": 0,
            "walkers_spawned": 0,
            "walkers_removed": 0,
            "walker_spawn_failures": 0,
            "stops_without_shoulder": 0,
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

        Order matters here: the town is loaded before the traffic manager is
        touched, and a traffic manager that predates the load is discarded.
        See the comment below — getting this wrong drives the bus into the
        scenery and is silent.
        """
        self._client = carla.Client(self._host, self._port)
        self._client.set_timeout(self._timeout_s)

        world = self._client.get_world()
        if self._town not in world.get_map().name:
            world = self._client.load_world(self._town)
        self._world = world
        self._original_settings = world.get_settings()

        # The traffic manager caches the road network when it is first
        # created and never re-caches it on a map change, so one that
        # predates the current map steers by the *previous* town's
        # waypoints. Measured directly: of its chosen waypoints, 60/60 lay
        # on the old map and 0/60 on the new one; the bus then reached 0 of
        # 5 stops and spent 93% of the run off any lane, sawing into the
        # kerb. Asking for it after the load is no defence, because the call
        # returns whichever instance this process already built -- and the
        # loading is often not ours to sequence, since the demo notebook
        # loads the town itself. So discard whatever is there and build our
        # own against the map that is actually loaded. Unconditional on
        # purpose: there is no API to ask a traffic manager which map it
        # holds, so the only cheap answer is not to inherit one.
        self._client.get_trafficmanager().shut_down()
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
        actor_ids = [
            i for i in ([self._bus_id] + self._npc_ids
                        + list(self._walkers.values()))
            if i is not None
        ]
        if actor_ids:
            self._client.apply_batch_sync(
                [carla.command.DestroyActor(i) for i in actor_ids], True
            )
        self._bus_id = None
        self._npc_ids = []
        self._walkers = {}
        self._walker_stops = {}

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
        Mirror waiting passengers onto walker actors.

        The pool stays authoritative; walkers are only how it is rendered.
        A walker appears when a request starts waiting and disappears when it
        stops — whether it was picked up or the passenger gave up. Nothing
        here feeds back into the simulation, so a failed spawn costs a
        visual, never a result.

        Diffed against the pool rather than driven by callbacks, which keeps
        ``RequestPool`` unaware that anything is watching it.
        :param pool: The shared request pool.
        """
        if self._world is None:
            return

        waiting = {
            request.id: request.pickup_stop_id
            for request in pool.get_pending()
        }

        gone = [rid for rid in self._walkers if rid not in waiting]
        if gone:
            commands = []
            for request_id in gone:
                commands.append(
                    carla.command.DestroyActor(self._walkers.pop(request_id))
                )
                self._walker_stops.pop(request_id, None)
            self._client.apply_batch_sync(commands, True)
            self._stats["walkers_removed"] += len(gone)

        for request_id, stop_id in waiting.items():
            if request_id not in self._walkers:
                self._spawn_walker(request_id, stop_id)

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

        if self._follow_camera:
            self._point_camera_at(bus)

        here = bus.get_location()
        if self._last_location is not None:
            step = here.distance(self._last_location)
            self._stats["distance_m"] += step
            self._track_stall(bus, step)
        self._last_location = here

    def _point_camera_at(self, bus: "carla.Actor") -> None:
        """
        Park the spectator camera behind and above the bus.

        Only worth enabling on a server that renders — with
        ``no_rendering`` there is nothing to look at, and the extra call per
        tick is pure cost.
        :param bus: The bus actor to follow.
        """
        transform = bus.get_transform()
        forward = transform.get_forward_vector()
        self._world.get_spectator().set_transform(carla.Transform(
            carla.Location(
                x=transform.location.x - forward.x * CAMERA_BEHIND_M,
                y=transform.location.y - forward.y * CAMERA_BEHIND_M,
                z=transform.location.z + CAMERA_ABOVE_M,
            ),
            carla.Rotation(
                pitch=CAMERA_PITCH_DEG,
                yaw=transform.rotation.yaw,
            ),
        ))

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

        if self._is_held_at_red(bus):
            # Obeying a traffic light is not stalling, and the thresholds
            # are *not* "well past any credible red light" as this comment
            # used to claim. Town01's 36 lights cycle in groups of three, so
            # an approach waits roughly 28 s of red -- and the clock below
            # starts while the bus is still slowing down, several seconds
            # before it stops. Every long red therefore overshot the 30 s
            # unwedge threshold. Measured over five tours: 20 of 20 unwedges
            # fired on a stationary bus at a red light on a proper driving
            # lane, none on a genuine wedge, and each one teleported the bus
            # through the junction against the light.
            #
            # Frozen rather than reset, so a bus that really is jammed at a
            # junction still carries its accumulated time into the green.
            self._stats["red_light_ticks"] += 1
            return

        # Deliberately not gated on throttle. A wedged bus is one the
        # traffic manager has given up on, and it commands *zero* throttle —
        # gating on throttle > 0 was measured to never fire at all.

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

    def _is_held_at_red(self, bus: "carla.Actor") -> bool:
        """
        Return whether a red light is what is holding the bus still.

        :param bus: The bus actor.
        :return: True if the bus is inside a traffic light's trigger volume
            and that light is red. A green light reads as False, so a bus
            that sits still on green is still treated as stalled.
        """
        if not bus.is_at_traffic_light():
            return False
        return bus.get_traffic_light_state() == carla.TrafficLightState.Red

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

    def _waiting_spot(self, stop_id: str) -> "carla.Transform":
        """
        Return where a passenger waits for the bus at a stop.

        **On the shoulder, never the lane centre.** A pedestrian standing in
        the road makes the traffic manager brake for them, and a walker with
        no AI controller will not move aside — which would hand back the
        stall problem the driving already had to solve.

        Every authored stop has a Shoulder immediately to its right: that was
        the pull-over test the authoring notebook applied, so this is
        guaranteed rather than hoped for. If one is ever missing the stop
        position is used and the failure is counted, not hidden.
        :param stop_id: TransitGraph node ID of the stop.
        :return: Transform where the first passenger at this stop stands.
        """
        carla_map = self._world.get_map()
        waypoint = carla_map.get_waypoint(
            self._location_of(stop_id), lane_type=carla.LaneType.Driving
        )
        shoulder = waypoint.get_right_lane()
        if shoulder is None or shoulder.lane_type != carla.LaneType.Shoulder:
            self._stats["stops_without_shoulder"] += 1
            return waypoint.transform

        base = shoulder.transform
        right = base.get_right_vector()
        return carla.Transform(
            carla.Location(
                x=base.location.x + right.x * WALKER_LATERAL_OFFSET_M,
                y=base.location.y + right.y * WALKER_LATERAL_OFFSET_M,
                z=base.location.z,
            ),
            base.rotation,
        )

    def _spawn_walker(self, request_id: str, stop_id: str) -> None:
        """
        Place one waiting passenger at a stop.

        Walkers are spawned without an AI controller, so they stand still —
        which is what a passenger waiting for a bus does, and it keeps them
        out of the road.
        :param request_id: Request the walker represents.
        :param stop_id: TransitGraph node ID of the pickup stop.
        """
        if stop_id not in self._stop_positions:
            return
        base = self._waiting_spot(stop_id)
        blueprint = self._walker_rng.choice(
            self._world.get_blueprint_library().filter(
                "walker.pedestrian.*"
            )
        )
        if blueprint.has_attribute("is_invincible"):
            blueprint.set_attribute("is_invincible", "true")

        # Several people can wait at one stop, so fan them out along the
        # shoulder; two walkers at the same point simply fail to spawn.
        forward = base.get_forward_vector()
        already = sum(
            1 for waiting_at in self._walker_stops.values()
            if waiting_at == stop_id
        )
        for attempt in range(already, already + 4):
            transform = carla.Transform(
                carla.Location(
                    x=base.location.x + forward.x * 0.9 * attempt,
                    y=base.location.y + forward.y * 0.9 * attempt,
                    z=base.location.z + 1.0,
                ),
                base.rotation,
            )
            walker = self._world.try_spawn_actor(blueprint, transform)
            if walker is not None:
                self._walkers[request_id] = walker.id
                self._walker_stops[request_id] = stop_id
                self._stats["walkers_spawned"] += 1
                return
        self._stats["walker_spawn_failures"] += 1

    def _spawn_npcs(self) -> None:
        """Spawn autopilot traffic, if any was asked for."""
        if self._npc_vehicles <= 0:
            return
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
