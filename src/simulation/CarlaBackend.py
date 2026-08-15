"""CarlaBackend: a SimulationBackend driving a real bus in CARLA.

The whole point of Phase 2's seam: this is one class against three abstract
methods, and nothing above it changes. The runner still owns the clock, the
tick order, demand, abandonment and metrics; this only takes destinations and
reports arrivals.

Steering keeps CARLA's own autonomous driving in charge, as the design
review settled on: the bus is an ordinary autopilot vehicle, told which turns
to take at each junction by `TrafficManager.set_route`. The turns come from
`carla_routing`, because a destination alone is not something the traffic
manager can navigate to.

**This module imports carla.** Nothing else under `src/simulation` does, and
nothing above the backend imports this.
"""

import math
import random
import re
import socket

from collections.abc import Sequence

from src.algorithm.RequestPool import RequestPool
from src.simulation import carla_debug_text, carla_stdout
from src.simulation.carla_routing import plan_route, turns_along
from src.simulation.SimulationBackend import AdvanceResult, SimulationBackend

import carla

# The only blueprint CARLA 0.9.16 tags base_type=Bus, at 10.3 x 3.9 m
# against Town01's 3.5 m lanes. It does get round the town, but it wedges in
# junctions doing it: measured over three tours it needed 20 recoveries where
# the minibus below needed none, and it cannot route out of five of the
# positions it ends up in. Since recovery was removed it is **not supported**
# on Town01; it is kept named here because "why not the actual bus?" is the
# first question anyone asks.
FULL_SIZE_BUS_ID = "vehicle.mitsubishi.fusorosa"

# A shuttle, and small enough for Town01's lanes: measured over three tours
# it completed every leg without needing a single recovery.
DEFAULT_BUS_ID = "vehicle.volkswagen.t2_2021"

# Above CARLA's substepping limit (max_substep_delta_time * max_substeps =
# 0.01 * 10) the physics silently stops keeping up and vehicle AI degrades —
# measured: at 0.5 s vehicles cover half the ground per simulated second.
MAX_SAFE_DELTA_S = 0.1

# How far right of the shoulder's centre a waiting passenger stands.
# Measured on Town01: the shoulder centre sits 2.15 m from a 4.0 m lane's
# centre, which clears the lane edge by only 0.15 m -- less than a walker's
# own body. This pushes them properly out of the road.
WALKER_LATERAL_OFFSET_M = 1.0

# Where CARLA looks for a traffic manager by default, and how far past it to
# search for one nobody else is using.
# What counts as "queued behind that vehicle rather than wedged". All three
# come from measurement rather than judgement: instrumenting the re-planner
# over a 20-NPC trial, 4 of its 5 firings had a stationary vehicle inside this
# box directly ahead of a bus that was nowhere near a traffic light.
QUEUE_AHEAD_M = 20.0
QUEUE_LATERAL_M = 3.0
QUEUE_STATIONARY_MS = 0.5

DEFAULT_TM_PORT = 8000
TM_PORT_SEARCH_LIMIT = 20

# Chase camera placement, relative to the bus.
CAMERA_BEHIND_M = 9.0
CAMERA_ABOVE_M = 4.5
CAMERA_PITCH_DEG = -14.0

# --- Debug overlay --------------------------------------------------------
#
# Only drawn under ``debug_draw``, and only visible on a server that renders.
#
# **Use `draw_line`, `draw_point`, `draw_box` and `draw_string` only.**
#
# The `draw_hud_*` family is not a screen-space API, however it reads. Its
# own stubs give it `Location` arguments "in the coordinate system (meters)"
# exactly like the world calls: "HUD" names the *render layer*, not the
# coordinate system, and there is no `draw_hud_string` at all. Nor does it
# help with occlusion here -- measured 2026-08-15 on 0.9.16, drawing matched
# pairs of `draw_line` and `draw_hud_line` in a single frame at 0.15, 0.5
# and 2.0 thickness, every world line rendered and no HUD line did, at any
# thickness. The shape types reach the server (`DebugShape::HUDLine` and
# friends are in the RPC layer); it simply does not draw them.
#
# The cost of world-space lines is that a building hides them. That is worth
# paying for shapes that exist.
#
# The route is *re-drawn on a timer* rather than drawn once and cleared,
# because ``clear_debug_shape`` takes no arguments and wipes every shape in
# the world -- including the drop markers, whose whole value is that they
# accumulate. Giving the route a finite lifetime lets it expire on its own
# instead, leaving the markers alone.
DEBUG_REDRAW_S = 2.0
# Comfortably more than twice the redraw period, so consecutive drawings
# overlap and the line never blinks between them.
DEBUG_ROUTE_LIFETIME_S = 5.0
# One segment per this many metres. ``plan_route`` steps every 2 m, so a
# 900 m leg is ~450 waypoints and a line each would be 450 calls per redraw.
DEBUG_ROUTE_STEP_M = 10.0
# Ceiling on segments, so a pathological route cannot stall the tick.
DEBUG_MAX_SEGMENTS = 250
# Lift shapes off the road surface, or they z-fight with it.
DEBUG_ROUTE_HEIGHT_M = 0.5
DEBUG_MARK_HEIGHT_M = 1.5
# The debug material is emissive and blooms with both width and brightness.
# 0.15 was a hairline and 0.6 flared over the whole frame; 0.20 against a
# muted green is followable without glare, which is what a line you look
# past all run wants to be.
DEBUG_ROUTE_THICKNESS_M = 0.20

# --- Stop beacons ---------------------------------------------------------
#
# A stop is marked by a box around it and a bar standing out of it: the box
# says where the stop is, and the bar carries it above the parked cars and
# street furniture so it can be picked out from down the road.
DEBUG_STOP_BEACON_M = 6.0
DEBUG_STOP_THICKNESS_M = 0.20
DEBUG_STOP_BOX_M = 2.6                  # half-extent of the cube, per axis
DEBUG_STOP_BOX_THICKNESS_M = 0.12

# Labels are stroke-drawn by ``carla_debug_text``, not ``draw_string``, so
# they have a real size. See that module: CARLA's own text has no size
# parameter at all and is a few pixels tall on a 4K display.
#
# The cost is a draw call per stroke, so only the nearest few stops are
# labelled. Boxes and beacons are one call each and are drawn for every stop.
# Labels are sized *by distance*, so they hold a roughly constant size on
# screen. A fixed world height cannot work at both ends: 0.85 m reads well
# from across the street and fills the entire frame when the bus is standing
# at the stop, which is exactly when the chase camera is closest to it.
# Cap height per metre of distance -- 0.035 is the 0.85 m that looked right
# at 24 m, generalised.
DEBUG_LABEL_ANGULAR = 0.035
DEBUG_LABEL_MIN_M = 0.30
DEBUG_LABEL_MAX_M = 2.50
# Line spacing follows the size, or the block spreads apart as it shrinks.
DEBUG_LABEL_LINE_RATIO = 1.55
DEBUG_LABEL_BASE_M = 6.6
DEBUG_LABEL_RANGE_M = 150.0
DEBUG_LABEL_MAX_STOPS = 4

# --- Camera-locked counters -----------------------------------------------
#
# There is genuinely no screen-space drawing in CARLA's API. ``draw_hud_*``
# is not it: those take world coordinates in metres, "HUD" naming the render
# layer rather than the coordinate system, and there is no
# ``draw_hud_string`` at all, so text could never go there. A real 2D HUD
# needs a client-side viewer -- a pygame window over a camera sensor, the way
# CARLA's own manual_control.py does it.
#
# So the counters are text placed a few metres in front of the spectator,
# redrawn **every tick** so they never lag behind the camera. Measured: 300
# ticks took 3.09 s with the overlay off, 3.14 s redrawing this four times a
# second, and 3.14 s redrawing it every single tick. The cheap option and the
# smooth option are the same option.
#
# The lifetime is in ticks for the same reason: an old copy surviving at an
# old position is a ghost trailing a moving camera.
DEBUG_HUD_LIFETIME_TICKS = 2.0
# How many stops of the plan the route line shows. The tail of a plan is the
# least reliable part of it -- everything past the next few will be re-planned
# before the bus gets there -- and a longer line runs off the panel.
HUD_ROUTE_STOPS = 4

# The live-request table. Columns are the passenger's whole story: where they
# are going from and to, how long they stood there, how long they have been
# riding, and which of the two they are doing now. Fulfilled and cancelled
# requests are not listed at all -- the table is about work outstanding, and
# a finished job never becomes interesting again.
REQUEST_COLUMNS = ("FROM", "TO", "WAIT", "RIDE", "STATE")
# Enough to see a backlog forming without burying the view. Demand runs to 30
# requests against a capacity of 20, so this does overflow, and the count of
# what is hidden is worth more than the rows themselves would be.
HUD_MAX_REQUEST_ROWS = 12
DEBUG_HUD_AHEAD_M = 3.0                 # how far in front of the spectator
DEBUG_HUD_LEFT_M = 1.55                 # and how far up and to the left
DEBUG_HUD_UP_M = 0.78
DEBUG_HUD_HEIGHT_M = 0.085
DEBUG_HUD_LINE_M = 0.135

# A stop nobody wants is drawn dim, so the ones in play stand out. Amber for
# passengers waiting to be collected, blue for passengers due to alight.
DEBUG_STOP_IDLE_COLOR = carla.Color(r=70, g=75, b=85)
DEBUG_STOP_WAITING_COLOR = carla.Color(r=190, g=125, b=30)
DEBUG_STOP_ALIGHTING_COLOR = carla.Color(r=45, g=115, b=190)
# CARLA reads 0.0 as "permanent". The markers are meant to pile up: by the
# end of a run the junctions that keep dropping routes are painted onto the
# map, which is how Town03's problem became a junction ID and not a count.
DEBUG_MARK_LIFETIME_S = 0.0
# Muted rather than saturated: this line is on screen the whole run, and a
# bright one flares across everything behind it.
DEBUG_ROUTE_COLOR = carla.Color(r=25, g=115, b=75)
DEBUG_DROP_COLOR = carla.Color(r=235, g=60, b=40)
DEBUG_HUD_COLOR = carla.Color(r=185, g=190, b=195)


def _port_is_free(port: int) -> bool:
    """
    Return whether a TCP port can be bound right now.

    Deliberately without ``SO_REUSEADDR``: a port still in ``TIME_WAIT`` from
    a traffic manager that has just shut down should read as busy, because
    binding it again is exactly what fails.
    :param port: Port to test.
    :return: True if the port is free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True


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
    :param blueprint_id: Vehicle to drive. Defaults to a shuttle, because
        the only true bus blueprint is wider than Town01's lanes and wedges
        in its junctions — see ``FULL_SIZE_BUS_ID``.
    :param repath_after_s: Seconds of no movement before the destination is
        re-issued to the traffic manager. Cheap and often enough.
    :param replan_every_s: Simulated seconds between re-issuing the route
        *while the bus is moving*, or None to issue it once per leg as
        before. ``repath_after_s`` only fires on a bus that has stopped, so
        without this nothing corrects one that is driving briskly in the
        wrong direction — see ``_track_replan``. Pick a value longer than a
        healthy leg on the town in question, so it never disturbs a leg that
        is going well: 300 s was measured good on Town03 and Town05, where
        healthy legs run 120–350 s.
    :param follow_camera: Move the spectator camera to sit behind and above
        the bus on every tick, so a rendering server shows a chase view.
        Pointless with ``no_rendering=True``, where there is nothing to look
        at. Defaults to False.
    :param debug_draw: Draw the bus's plan into the world: the route it was
        told to drive, a labelled box over every stop, and a permanent marker
        wherever the traffic manager threw that route away. For watching one
        run, not for measuring: it costs a handful of calls a second and,
        like ``follow_camera``, shows nothing at all under
        ``no_rendering=True``. Defaults to False.
    :param viewer: Open a chase-camera window with a real 2D HUD, instead of
        squinting at counters drawn into the world. See ``carla_viewer``: it
        is a viewer only, never advances the world and cannot change a
        result, and closing it just stops the drawing. Needs a rendering
        server for the camera to produce frames, but not a *watchable* one --
        with this open the server can run ``-RenderOffScreen``. Defaults to
        False.
    :param viewer_size: Window size in pixels, when ``viewer`` is on.
    :param minimap: Road graph for the viewer's minimap, from
        ``carla_viewer.minimap_from(provider)``. Passed in already flattened
        to coordinates, so this class still needs no map-layer import.
        Defaults to None, which leaves the panel off.
    :param tm_port: Port for the traffic manager. Defaults to None, which
        finds a free one and shuts it down again on ``close``. Name a port
        only to share an existing traffic manager, in which case it is yours
        to manage — and see ``_acquire_traffic_manager`` for why sharing one
        is usually a mistake.
    :param reload_world: Load the town on ``start`` even when it is already
        loaded, so the trial begins in a world nothing has touched. Costs a
        map load — seconds — and is what a campaign wants: leaked actors are
        the classic cross-trial contaminant, and a trial that failed part-way
        may have left some behind. Defaults to False, which loads only on a
        town mismatch and suits a single interactive run.
    :raises ValueError: If ``fixed_delta_s`` exceeds the safe limit.
    """

    # One server, and trials would contend for it rather than run. The
    # campaign runner refuses to parallelise a world that says this.
    parallel_safe: bool = False

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
        replan_every_s: float | None = None,
        follow_camera: bool = False,
        debug_draw: bool = False,
        viewer: bool = False,
        viewer_size: tuple[int, int] = (1280, 720),
        minimap: object | None = None,
        tm_port: int | None = None,
        reload_world: bool = False,
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
        self._replan_every_s = replan_every_s
        self._follow_camera = follow_camera
        self._debug_draw = debug_draw
        self._viewer_wanted = viewer
        self._viewer_size = viewer_size
        self._minimap = minimap
        self._viewer: object | None = None
        self._tm_port = tm_port
        self._reload_world = reload_world
        self._owns_traffic_manager = False

        self._client: carla.Client | None = None
        self._world: carla.World | None = None
        self._traffic_manager: carla.TrafficManager | None = None
        self._bus_id: int | None = None
        self._npc_ids: list[int] = []
        self._original_settings: carla.WorldSettings | None = None

        self._destination: str | None = None
        self._leg_armed = False
        self._dwell_remaining = 0.0
        self._since_repath_s = 0.0
        self._since_replan_s = 0.0

        # The last turn list handed to the traffic manager, when, and from
        # where. Only diagnostics read these: a dropped route is about a
        # list that ran out or slipped, so its contents, its age and the
        # point it was planned from are the questions asked of it. The
        # origin matters because the route is planned from wherever the bus
        # actually is, not from the stop it nominally left — without it a
        # drop cannot be reconstructed after the fact.
        self._issued_turns: list[str] = []
        self._issued_at_s = 0.0
        self._issued_from: tuple[float, float] | None = None

        # The current leg's route, thinned to what the overlay draws, and
        # the clock that decides when to draw it again. Empty whenever there
        # is no route to show — including after an unroutable leg, so a stale
        # line never outlives the plan it described.
        self._debug_route: list["carla.Location"] = []
        # What the agent means to do after this leg. Display only: nothing
        # here steers, and the backend never learns what produced it.
        self._planned_route: list[str] = []
        # Live requests, as display rows. Rebuilt from the pool each sync,
        # and only when something is going to show them.
        self._request_rows: list[tuple[str, ...]] = []
        self._request_hidden = 0
        self._short_stops: dict[str, str] | None = None
        self._since_debug_draw_s = 0.0
        # When the current leg began, for the overlay's per-leg counter.
        self._leg_started_s = 0.0

        # Per stop: how many wait there to be collected, and how many riding
        # now are due to alight there. Refreshed from the pool in
        # ``sync_passengers`` rather than recomputed while drawing, because
        # the pool is only handed over on that call.
        self._stop_demand: dict[str, tuple[int, int]] = {}

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
            "replans": 0,
            "red_light_ticks": 0,
            "queued_ticks": 0,
            "unroutable_legs": 0,
            # Times the traffic manager reported it could not find the turn
            # it was told to make, and so threw the route away. See
            # ``carla_stdout``: the message itself carries no context, and
            # the tagged line on stdout is where the detail lives.
            "route_drops": 0,
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
        created, so the traffic manager caches the right map. See
        ``_acquire_traffic_manager`` — getting this wrong drives the bus into
        the scenery and is silent.
        """
        # Before anything touches CARLA: the traffic manager's diagnostics
        # go straight to fd 1 from C++, and are worthless untagged.
        carla_stdout.install()

        self._client = carla.Client(self._host, self._port)
        self._client.set_timeout(self._timeout_s)

        world = self._acquire_world()
        self._world = world
        self._original_settings = world.get_settings()

        self._traffic_manager = self._acquire_traffic_manager()
        self._traffic_manager.set_synchronous_mode(True)
        self._traffic_manager.set_random_device_seed(self._seed)

        settings = world.get_settings()
        settings.synchronous_mode = True
        settings.no_rendering_mode = self._no_rendering
        settings.fixed_delta_seconds = self._delta
        world.apply_settings(settings)

        self._spawn_npcs()
        self._spawn_bus()
        self._open_viewer()

    def _acquire_world(self) -> "carla.World":
        """
        Get the world to drive in, loading the town when that is needed.

        Loading is unconditional under ``reload_world`` and otherwise happens
        only when the wrong town is up. A campaign wants the unconditional
        form: `close` destroys the actors this backend created, but a trial
        that failed part-way may have left others, and a world carried from
        one trial into the next is the classic cross-trial contaminant.

        A single interactive run wants the opposite, which is why this is not
        simply always on — a map load costs seconds, and re-running a cell
        would pay it every time for nothing.

        Called before the traffic manager is created, and that ordering is
        load-bearing: a traffic manager caches the road network when it is
        built and never re-caches it. See ``_acquire_traffic_manager``.
        :return: The world, freshly loaded or the one already up.
        """
        world = self._client.get_world()
        if self._reload_world or self._town not in world.get_map().name:
            return self._client.load_world(self._town)
        return world

    def _acquire_traffic_manager(self) -> "carla.TrafficManager":
        """
        Get a traffic manager of our own, built against the loaded map.

        **A traffic manager caches the road network when it is created and
        never re-caches it.** One that predates the current map steers by the
        previous town's waypoints: measured, 60 of 60 of its chosen waypoints
        lay on the old map and 0 on the new one, and the bus reached 0 of 5
        stops while spending 93% of the run off any lane. Creating ours after
        the world load is what avoids that.

        **A traffic manager belongs to the process that created it**, not to
        the server — it is an RPC service bound to a port, and
        ``get_trafficmanager`` returns a remote proxy when somebody else owns
        that port. So we take a port of our own rather than the default,
        because the default is very often already taken by a Jupyter kernel
        that ran a trial earlier and has not been restarted. Sharing it is
        worse than it sounds: the proxy accepts vehicles and then never
        commands them, leaving the bus registered, stationary, and reporting
        zero throttle and zero brake for ever.

        Tearing down whoever holds the default port is not the answer either.
        It disconnects another client mid-session, and re-binding the same
        port immediately afterwards either fails outright with a bind error
        or hands back a traffic manager that drives nothing.
        :return: A traffic manager on a port nobody else is using.
        :raises RuntimeError: If no free port is found.
        """
        if self._tm_port is not None:
            # Explicitly chosen, so it is the caller's to manage; `close`
            # leaves it alone.
            return self._client.get_trafficmanager(self._tm_port)

        for offset in range(TM_PORT_SEARCH_LIMIT):
            port = DEFAULT_TM_PORT + offset
            if not _port_is_free(port):
                continue
            try:
                traffic_manager = self._client.get_trafficmanager(port)
            except RuntimeError:
                # Free to the OS but the server still has a registration for
                # it; the next port will do.
                continue
            self._owns_traffic_manager = True
            return traffic_manager

        raise RuntimeError(
            f"no free traffic manager port in "
            f"{DEFAULT_TM_PORT}..{DEFAULT_TM_PORT + TM_PORT_SEARCH_LIMIT - 1}"
        )

    def close(self) -> None:
        """
        Destroy every actor and restore the world's original settings.

        Called from the runner's ``finally``, so it runs even when a trial
        fails part-way. Leaked actors are the classic cross-trial
        contaminant, and a world left in synchronous mode looks frozen to
        every other client.

        Shuts down the traffic manager only if we created it, so that a
        caller who named a port keeps theirs and a notebook sharing the
        server keeps working.
        """
        # Whatever the traffic manager printed since the last tick still
        # belongs to this trial, so take it before handing fd 1 back.
        self._collect_messages()
        carla_stdout.restore()

        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None

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
            if self._owns_traffic_manager:
                # Releases the port, so the next trial in this process gets a
                # traffic manager freshly built against its own map.
                self._traffic_manager.shut_down()
                self._traffic_manager = None
                self._owns_traffic_manager = False
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
        self._since_replan_s = 0.0
        # Restarted here rather than on arrival, because the leg the overlay
        # counts is the one the bus is driving, and a bus that dwells at a
        # stop has not started the next leg until it is given one.
        self._leg_started_s = self._stats["ticks"] * self._delta
        self._stats["legs_started"] += 1

        bus = self._bus()
        if bus is not None and self._traffic_manager is not None:
            self._steer_towards(bus, stop_id)

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

    def set_planned_route(self, stop_ids: "Sequence[str]") -> None:
        """
        Record the plan, for the overlay and the viewer to show.

        Never steers: ``set_destination`` remains the whole instruction, and
        a plan arriving here changes nothing the bus does.
        :param stop_ids: Stops still to visit, next first.
        :return: None.
        """
        self._planned_route = list(stop_ids)

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
        self._note_stop_demand(pool)
        self._note_requests(pool)

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

    def _open_viewer(self) -> None:
        """
        Open the viewer window, once there is a bus for it to ride behind.

        Imported here rather than at module scope so that pygame is needed
        only by the people who ask for a window: a campaign, a test run and a
        headless machine never touch it.
        :return: None.
        """
        if not self._viewer_wanted or self._viewer is not None:
            return
        bus = self._bus()
        if bus is None:
            return
        from src.simulation.carla_viewer import CarlaViewer

        self._viewer = CarlaViewer(
            self._world, bus, size=self._viewer_size,
            title=f"autobus - {self._town} seed {self._seed}",
            minimap=self._minimap)

    def _pump_viewer(self) -> None:
        """
        Give the viewer its turn, and let go of it once it is closed.

        Dropped rather than kept on close, so that shutting the window costs
        one dictionary lookup a tick for the rest of the run instead of a
        window's worth of work. A viewer that fails is discarded outright:
        it is a window, and a window must never take a trial down with it.
        :return: None.
        """
        if self._viewer is None:
            return
        bus = self._bus()
        try:
            alive = self._viewer.update(
                self.hud_lines(), route=self._planned_route,
                bus=bus.get_location() if bus is not None else None,
                table=self.request_table(), hidden=self._request_hidden)
            if not alive:
                self._viewer.close()
                self._viewer = None
        except Exception:                       # noqa: BLE001
            self._viewer = None

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

    def _collect_messages(self) -> None:
        """Tag and count whatever the traffic manager has printed."""
        for name, hits in carla_stdout.drain(self._context).items():
            self._stats[name] = self._stats.get(name, 0) + hits
            if name == "route_drops" and self._debug_draw:
                self._mark_route_drop(hits)

    # ------------------------------------------------------------------
    # Debug overlay
    # ------------------------------------------------------------------
    #
    # Every call here is wrapped, for the reason ``carla_stdout._describe``
    # is: a diagnostic that can abort a trial is worse than no diagnostic,
    # and these touch live actors and the renderer.

    def _remember_debug_route(
        self, route: "list[carla.Waypoint] | None"
    ) -> None:
        """
        Cache the leg's route as the polyline the overlay will draw.

        Thinned to one point per ``DEBUG_ROUTE_STEP_M`` rather than kept
        whole: the route steps every 2 m, and redrawing hundreds of segments
        every couple of seconds costs far more than the picture is worth.
        :param route: Waypoints from the bus to its destination, or None
            when the leg could not be routed at all — which empties the
            cache, so no stale line survives the plan it described.
        :return: None.
        """
        if not self._debug_draw:
            return
        if not route:
            self._debug_route = []
            return

        def raised(location: "carla.Location") -> "carla.Location":
            """Lift a point clear of the road surface it would z-fight."""
            return carla.Location(x=location.x, y=location.y,
                                  z=location.z + DEBUG_ROUTE_HEIGHT_M)

        points: list[carla.Location] = []
        for waypoint in route:
            here = waypoint.transform.location
            if (not points
                    or here.distance(points[-1]) >= DEBUG_ROUTE_STEP_M):
                points.append(raised(here))
        # The true end, so the line reaches the stop rather than stopping at
        # whichever sample happened to fall last.
        final = raised(route[-1].transform.location)
        if not points or final.distance(points[-1]) > 0.5:
            points.append(final)
        self._debug_route = points[:DEBUG_MAX_SEGMENTS + 1]

    def _note_stop_demand(self, pool: RequestPool) -> None:
        """
        Record what each stop is owed, for the overlay to label it with.

        Counted here rather than while drawing because ``sync_passengers`` is
        the only place the pool is handed over, and because two passes over a
        few dozen requests is cheaper than reaching for them every redraw.
        :param pool: The shared request pool.
        :return: None.
        """
        if not self._debug_draw:
            return
        demand = {stop_id: [0, 0] for stop_id in self._stop_positions}
        for request in pool.get_pending():
            if request.pickup_stop_id in demand:
                demand[request.pickup_stop_id][0] += 1
        for request in pool.get_on_bus():
            if request.dropoff_stop_id in demand:
                demand[request.dropoff_stop_id][1] += 1
        self._stop_demand = {
            stop_id: (counts[0], counts[1])
            for stop_id, counts in demand.items()
        }

    def _draw_debug(self) -> None:
        """
        Redraw the route and the stop beacons, on a timer.

        Everything is world-space: see the note by the constants for why the
        ``draw_hud_*`` family cannot be used however well it would suit this.
        :return: None.
        """
        if not self._debug_draw or self._world is None:
            return
        try:
            debug = self._world.debug
            spectator = self._world.get_spectator()
            # The counters follow the spectator, so they are redrawn every
            # tick; the world overlay is nailed down and is not.
            self._draw_hud(debug, spectator)

            self._since_debug_draw_s += self._delta
            if self._since_debug_draw_s < DEBUG_REDRAW_S:
                return
            self._since_debug_draw_s = 0.0
            self._draw_route(debug)
            self._draw_stops(debug, self._overlay_eye(spectator))
        except Exception:                       # noqa: BLE001
            pass

    def _draw_route(self, debug: object) -> None:
        """
        Draw the cached route polyline.
        :param debug: The world's debug helper.
        :return: None.
        """
        for start, end in zip(self._debug_route, self._debug_route[1:]):
            debug.draw_line(start, end,
                                thickness=DEBUG_ROUTE_THICKNESS_M,
                                color=DEBUG_ROUTE_COLOR,
                                life_time=DEBUG_ROUTE_LIFETIME_S)

    def _stop_color(self, waiting: int, alighting: int) -> "carla.Color":
        """
        Pick the colour that says what a stop is owed.

        Waiting wins when both are true, because a passenger who has not
        been picked up yet is the one whose promise can still be broken.
        :param waiting: Passengers waiting there to be collected.
        :param alighting: Passengers aboard due to alight there.
        :return: The colour to draw that stop in.
        """
        if waiting:
            return DEBUG_STOP_WAITING_COLOR
        if alighting:
            return DEBUG_STOP_ALIGHTING_COLOR
        return DEBUG_STOP_IDLE_COLOR

    def _labelled_stops(self, eye: "carla.Location") -> list[str]:
        """
        Choose which stops are close enough to be worth lettering.

        Stroke-drawn text costs a call per stroke, so labelling fifteen
        stops every redraw would cost more than the rest of the overlay put
        together — and the far ones are unreadable anyway.
        :param eye: Where the camera is.
        :return: Stop IDs to label, nearest first.
        """
        near = [
            (self._location_of(stop_id).distance(eye), stop_id)
            for stop_id in self._stop_demand
        ]
        near = [(d, s) for d, s in near if d <= DEBUG_LABEL_RANGE_M]
        near.sort()
        return [stop_id for _, stop_id in near[:DEBUG_LABEL_MAX_STOPS]]

    def _overlay_eye(self, spectator: object) -> object:
        """
        Say whose view the stop labels are composed for.

        The spectator is the right answer only when the spectator is what
        anyone is looking through. With the viewer window open the camera
        rides the bus instead, and picking labels by distance to a spectator
        parked across town would letter the wrong stops and turn them to
        face nobody.
        :param spectator: The spectator actor.
        :return: The actor to treat as the eye, or None if there is none.
        """
        if self._viewer is not None:
            bus = self._bus()
            if bus is not None:
                return bus
        return spectator

    def _draw_stops(self, debug: object, eye: object) -> None:
        """
        Draw a box, a beacon and a label over the stops.

        Every stop gets the box and the beacon, which are a call each.
        Only the nearest few get lettering; see ``_labelled_stops``.
        :param debug: The world's debug helper.
        :param eye: The actor the labels are composed for -- see
            ``_overlay_eye``. None leaves them unturned rather than undrawn.
        :return: None.
        """
        here = (eye.get_transform().location if eye is not None else None)
        wanted = self._labelled_stops(here) if here is not None else []
        right, up = self._billboard_axes(eye)

        for stop_id, (waiting, alighting) in self._stop_demand.items():
            color = self._stop_color(waiting, alighting)
            base = self._location_of(stop_id)

            debug.draw_box(
                carla.BoundingBox(
                    carla.Location(x=base.x, y=base.y,
                                   z=base.z + DEBUG_STOP_BOX_M),
                    carla.Vector3D(x=DEBUG_STOP_BOX_M, y=DEBUG_STOP_BOX_M,
                                   z=DEBUG_STOP_BOX_M)),
                carla.Rotation(), thickness=DEBUG_STOP_BOX_THICKNESS_M,
                color=color, life_time=DEBUG_ROUTE_LIFETIME_S)

            debug.draw_line(
                carla.Location(x=base.x, y=base.y, z=base.z),
                carla.Location(x=base.x, y=base.y,
                               z=base.z + DEBUG_STOP_BEACON_M),
                thickness=DEBUG_STOP_THICKNESS_M, color=color,
                life_time=DEBUG_ROUTE_LIFETIME_S)

            if stop_id not in wanted:
                continue
            height = self._label_height(base.distance(here))
            spacing = height * DEBUG_LABEL_LINE_RATIO
            # Top line first, so the name sits highest and the counts read
            # down towards the beacon.
            lines = [stop_id, f"waiting {waiting}", f"alighting {alighting}"]
            for offset, text in enumerate(lines):
                lift = (DEBUG_LABEL_BASE_M
                        + (len(lines) - 1 - offset) * spacing)
                half = carla_debug_text.text_width(text, height) / 2
                start = carla.Location(x=base.x - right.x * half,
                                       y=base.y - right.y * half,
                                       z=base.z + lift)
                carla_debug_text.draw_text(
                    debug, text, start, right, up, height, color,
                    DEBUG_ROUTE_LIFETIME_S)

    @staticmethod
    def _label_height(distance: float) -> float:
        """
        Size a stop's label so it looks the same from anywhere.

        Text drawn at a fixed world height is either unreadable across the
        town or fills the screen at the stop the bus is standing at, and the
        chase camera spends its time at exactly that distance. Scaling with
        distance holds the apparent size instead, clamped so a stop far away
        does not become a billboard and one underfoot does not vanish.
        :param distance: Metres from the camera to the stop.
        :return: Cap height in metres.
        """
        wanted = distance * DEBUG_LABEL_ANGULAR
        return min(DEBUG_LABEL_MAX_M, max(DEBUG_LABEL_MIN_M, wanted))

    @staticmethod
    def _billboard_axes(eye: object) -> tuple[object, object]:
        """
        Return the axes that turn flat text to face the camera.

        Text is drawn upright rather than tilted with the camera's pitch: a
        label that rolls with a chase camera is harder to read than one that
        always stands up.
        :param eye: The actor being looked through, or None for world axes.
        :return: ``(right, up)`` unit vectors.
        """
        up = carla.Vector3D(x=0.0, y=0.0, z=1.0)
        if eye is None:
            return carla.Vector3D(x=1.0, y=0.0, z=0.0), up
        forward = eye.get_transform().get_forward_vector()
        flat = math.hypot(forward.x, forward.y) or 1.0
        # Perpendicular to the camera's heading, in the ground plane.
        return carla.Vector3D(x=-forward.y / flat, y=forward.x / flat,
                              z=0.0), up

    def _draw_hud(self, debug: object, spectator: object) -> None:
        """
        Draw the counters that follow the camera.

        CARLA has no screen-space drawing, so this is text pinned a few
        metres in front of the spectator and redrawn as it moves. It reads
        as a corner-of-screen HUD as long as the redraw keeps up, which is
        why it runs on its own much faster clock.
        :param debug: The world's debug helper.
        :param spectator: The spectator actor. None means no camera to pin
            anything to, so nothing is drawn.
        :return: None.
        """
        if spectator is None:
            return
        transform = spectator.get_transform()
        forward = transform.get_forward_vector()
        right = transform.get_right_vector()
        up = transform.get_up_vector()

        for index, text in enumerate(self.hud_lines()):
            drop = index * DEBUG_HUD_LINE_M
            origin = carla.Location(
                x=(transform.location.x + forward.x * DEBUG_HUD_AHEAD_M
                   - right.x * DEBUG_HUD_LEFT_M
                   + up.x * (DEBUG_HUD_UP_M - drop)),
                y=(transform.location.y + forward.y * DEBUG_HUD_AHEAD_M
                   - right.y * DEBUG_HUD_LEFT_M
                   + up.y * (DEBUG_HUD_UP_M - drop)),
                z=(transform.location.z + forward.z * DEBUG_HUD_AHEAD_M
                   - right.z * DEBUG_HUD_LEFT_M
                   + up.z * (DEBUG_HUD_UP_M - drop)))
            carla_debug_text.draw_text(
                debug, text, origin, right, up, DEBUG_HUD_HEIGHT_M,
                DEBUG_HUD_COLOR, self._delta * DEBUG_HUD_LIFETIME_TICKS)

    def _shorten(self, stop_id: str) -> str:
        """
        Give a stop a short name for the table, if one is unambiguous.

        Stop IDs run to ``n_s15_n_s13``, and two of those per row leaves no
        width for anything else. The ``sNN`` inside is what anyone reads
        anyway — but only if it still tells the stops apart, so the short
        form is abandoned wholesale the moment any two would collide.
        :param stop_id: The full TransitGraph node ID.
        :return: The short name, or the full ID when shortening is unsafe.
        """
        if self._short_stops is None:
            candidates = {}
            for full in self._stop_positions:
                match = re.search(r"s\d+", full)
                candidates[full] = match.group(0) if match else full
            unique = len(set(candidates.values())) == len(candidates)
            self._short_stops = candidates if unique else {}
        return self._short_stops.get(stop_id, stop_id)

    def _note_requests(self, pool: RequestPool) -> None:
        """
        Snapshot the requests still in play, as rows for the viewer's table.

        Waiting requests come first, longest wait at the top: that passenger
        is the closest to giving up, which is the one thing on this table
        that is about to change. Riders follow, longest ride first.

        ``WAIT`` keeps meaning "time spent waiting at the stop", so it stops
        counting at the moment of boarding rather than tracking the clock
        forever; ``RIDE`` starts there. Read together they are the two halves
        of the KPI the campaign reports.
        :param pool: The shared request pool, read-only from here.
        :return: None.
        """
        if not self._viewer_wanted:
            return
        now = self._stats["ticks"] * self._delta

        waiting = sorted(
            ((now - request.created_at, request)
             for request in pool.get_pending()),
            key=lambda pair: pair[0], reverse=True)
        riding = sorted(
            ((now - (request.boarded_at if request.boarded_at is not None
                     else now), request)
             for request in pool.get_on_bus()),
            key=lambda pair: pair[0], reverse=True)

        rows: list[tuple[str, ...]] = []
        for waited, request in waiting:
            rows.append((
                self._shorten(request.pickup_stop_id),
                self._shorten(request.dropoff_stop_id),
                self._clock(waited), "-", "WAITING"))
        for ridden, request in riding:
            waited = ((request.boarded_at - request.created_at)
                      if request.boarded_at is not None else 0.0)
            rows.append((
                self._shorten(request.pickup_stop_id),
                self._shorten(request.dropoff_stop_id),
                self._clock(waited), self._clock(ridden), "ON BUS"))

        self._request_hidden = max(0, len(rows) - HUD_MAX_REQUEST_ROWS)
        self._request_rows = rows[:HUD_MAX_REQUEST_ROWS]

    def request_table(self) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
        """
        Return the live-request table, for whatever is drawing it.

        :return: ``(header, rows)``. Rows are already trimmed to what will
            fit; ``hidden_requests`` says how many were left off.
        """
        return REQUEST_COLUMNS, list(self._request_rows)

    @property
    def hidden_requests(self) -> int:
        """How many live requests did not fit in the table."""
        return self._request_hidden

    def hud_lines(self) -> list[str]:
        """
        Build the counters, for whichever surface is showing them.

        Shared by the world-space overlay and the viewer window so the two
        can never drift apart, and public because the viewer is driven from
        outside when a caller brings its own.
        :return: One string per HUD line.
        """
        now = self._stats["ticks"] * self._delta
        waiting = sum(count for count, _ in self._stop_demand.values())
        aboard = sum(count for _, count in self._stop_demand.values())
        lines = [
            f"SIM  {self._clock(now)}",
            f"LEG  {self._clock(now - self._leg_started_s)}",
            f"NEXT {self._destination or '-'}",
        ]
        if self._stop_demand:
            lines.append(f"WAIT {waiting}   ABOARD {aboard}")
        if self._planned_route:
            shown = self._planned_route[:HUD_ROUTE_STOPS]
            more = len(self._planned_route) - len(shown)
            # Short names here too: at full length four stop IDs run clear
            # across the window and under the request table.
            lines.append("PLAN " + " > ".join(self._shorten(stop)
                                              for stop in shown)
                         + (f" (+{more})" if more else ""))
        drops = int(self._stats["route_drops"])
        unroutable = int(self._stats["unroutable_legs"])
        if drops or unroutable:
            lines.append(f"DROPS {drops}   UNROUTABLE {unroutable}")
        return lines

    @staticmethod
    def _clock(seconds: float) -> str:
        """
        Format a duration for the overlay.

        :param seconds: Duration in simulated seconds; negatives read as 0.
        :return: ``M:SS`` under an hour, ``H:MM:SS`` above it.
        """
        whole = max(0, int(seconds))
        hours, rest = divmod(whole, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _mark_route_drop(self, hits: int) -> None:
        """
        Leave a permanent marker where the traffic manager lost its route.

        The point is the pile-up. A run's markers are its dropped routes laid
        out on the map, so a junction that keeps doing it is visible as a
        cluster rather than inferred from a counter.
        :param hits: How many messages arrived in this drain, so a tick that
            produced several says so rather than looking like one.
        :return: None.
        """
        bus = self._bus()
        if bus is None or self._world is None:
            return
        try:
            here = bus.get_location()
            at = carla.Location(x=here.x, y=here.y,
                                z=here.z + DEBUG_MARK_HEIGHT_M)
            now = self._stats["ticks"] * self._delta
            debug = self._world.debug
            debug.draw_point(at, size=0.3, color=DEBUG_DROP_COLOR,
                             life_time=DEBUG_MARK_LIFETIME_S)
            debug.draw_string(
                at, f"drop t={now:.0f}s" + (f" x{hits}" if hits > 1 else ""),
                draw_shadow=True, color=DEBUG_DROP_COLOR,
                life_time=DEBUG_MARK_LIFETIME_S)
        except Exception:                       # noqa: BLE001
            pass

    def _context(self) -> str:
        """
        Describe where the bus is, to label a traffic manager message.

        Built only once a message has actually arrived: the waypoint lookup
        is a map query, and at twenty ticks a simulated second over a
        campaign it would cost far more than the diagnostic is worth.

        The road fields are the whole point. A message raised while the bus
        sits *inside* a junction says the turn list was re-issued from a
        place ``turns_along`` cannot describe, and the lane says whether the
        bus had drifted off the one its route was planned along.
        :return: One line of context, for the front of the message.
        """
        now = self._stats["ticks"] * self._delta
        parts = [
            self._town,
            f"seed={self._seed}",
            f"leg={self._position}->{self._destination}",
            # Without this a message raised while the bus idles between legs
            # reads as a leg that went wrong. It also makes the two look
            # alike in the tag, since ``_position`` becomes ``_destination``
            # on arrival.
            f"armed={self._leg_armed}",
            f"t={now:.2f}s",
            f"since_replan={self._since_replan_s:.1f}s",
            f"issued={len(self._issued_turns)}",
            f"issued_ago={now - self._issued_at_s:.1f}s",
            # The list itself and the point it was planned from, so a drop
            # can be reconstructed offline instead of guessed at. The route
            # is planned from where the bus stood, which is not the stop it
            # left, so the destination alone does not identify it.
            f"turns={','.join(self._issued_turns) or '-'}",
            f"planned_from={self._as_point(self._issued_from)}",
        ]
        bus = self._bus()
        if bus is not None:
            here = bus.get_location()
            waypoint = self._world.get_map().get_waypoint(
                here, lane_type=carla.LaneType.Driving
            )
            parts.append(f"junction={waypoint.is_junction}")
            parts.append(f"road={waypoint.road_id}")
            parts.append(f"lane={waypoint.lane_id}")
            parts.append(f"at=({here.x:.1f},{here.y:.1f})")
            parts += self._lookahead(bus, waypoint)
        return " ".join(parts)

    @staticmethod
    def _as_point(position: "tuple[float, float] | None") -> str:
        """
        Format a planning origin for the tag.

        :param position: The x and y it was planned from, or None if no
            route has been issued yet.
        :return: ``(x,y)``, or ``none``.
        """
        if position is None:
            return "none"
        return f"({position[0]:.1f},{position[1]:.1f})"

    def _lookahead(self, bus: "carla.Actor",
                   waypoint: "carla.Waypoint") -> list[str]:
        """
        Describe the road in front of the bus, not the road under it.

        This is the correction the first round of tagging asked for. The
        traffic manager validates the turn list against a buffer that runs
        *ahead* of the vehicle, so where the bus is says little about which
        junction the missing turn belonged to — measured 2026-08-14, 26 of
        30 messages fired with the bus on open road.
        :param bus: The bus actor.
        :param waypoint: The bus's own snapped waypoint.
        :return: Tag fields naming what the traffic manager intends to do
            next and where the next junction along the lane is.
        """
        action, target = self._traffic_manager.get_next_action(bus)
        gap = target.transform.location.distance(bus.get_location())
        parts = [
            f"tm_next={action}",
            f"tm_next_at={gap:.0f}m",
            f"tm_buffer={len(self._traffic_manager.get_all_actions(bus))}",
        ]

        ahead = self._junction_ahead(waypoint)
        if ahead is None:
            parts.append("next_junction=none")
        else:
            junction, distance = ahead
            parts.append(f"next_junction={junction.road_id}@{distance:.0f}m")
        return parts

    def _junction_ahead(
        self, waypoint: "carla.Waypoint", limit_m: float = 80.0
    ) -> "tuple[carla.Waypoint, float] | None":
        """
        Find the first junction along the lane in front of the bus.

        Follows the first continuation at each step rather than every
        branch. That is enough for the question being asked — which junction
        is coming — because every branch out of a lane enters the same
        junction; it would not be enough to plan with.
        :param waypoint: Where to start looking from.
        :param limit_m: How far ahead to bother looking.
        :return: The junction's first waypoint and its distance, or None if
            there is no junction within the limit.
        """
        step, travelled, current = 2.0, 0.0, waypoint
        while travelled < limit_m:
            following = current.next(step)
            if not following:
                return None
            current = following[0]
            travelled += step
            if current.is_junction:
                return current, travelled
        return None

    def _tick_once(self) -> None:
        """Advance the world one step and accumulate diagnostics."""
        self._world.tick()
        self._stats["ticks"] += 1
        self._collect_messages()

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
        self._draw_debug()
        self._pump_viewer()

        here = bus.get_location()
        if self._last_location is not None:
            step = here.distance(self._last_location)
            self._stats["distance_m"] += step
            self._track_stall(bus, step)
        self._last_location = here

        self._track_replan(bus)

    def _track_replan(self, bus: "carla.Actor") -> None:
        """
        Re-issue the route periodically, for a bus that is still moving.

        `set_destination` hands the traffic manager one turn list per leg and
        never refreshes it, and `_track_stall` only reacts to a bus that has
        *stopped*. Between them there is a gap: the turn list is consumed one
        entry per junction, and when it runs out or slips out of step,
        `ImportRoute` drops the route and the vehicle reverts to random
        navigation — driving briskly, so nothing notices. Measured on Town03,
        a 474 m leg lost its list at the fourth of four turns and then
        crossed 125 more junctions, covering 10 km without arriving.

        Re-issuing does not correct any individual turn. It re-establishes
        the alignment from wherever the bus actually is, so accumulated drift
        is discarded rather than compounding for the rest of the leg.
        Measured over full tours: Town03 −67% and Town05 −50% of simulated
        time, with Town05 going from 12/14 legs arriving to 14/14.

        The period wants to be **longer than a healthy leg**, so a leg that
        is going well finishes untouched — at 300 s on Town05 the four legs
        shorter than that came out bit-identical to leaving it alone, while
        30 s and 120 s made one of them three times slower.
        :param bus: The bus actor.
        """
        if self._replan_every_s is None or not self._leg_armed:
            return
        if self._destination is None or self._dwell_remaining > 0.0:
            return

        self._since_replan_s += self._delta
        if self._since_replan_s >= self._replan_every_s:
            self._steer_towards(bus, self._destination)
            self._stats["replans"] += 1
            self._since_replan_s = 0.0

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
        Notice a bus that has stopped making progress, and re-plan for it.

        Re-planning is the only response, and it is an honest one: it works
        out the route from where the bus actually is and re-issues the
        turns, moving nothing. With the default vehicle it has not fired at
        all in the tours measured so far, which is the point — the counter
        in ``stats()`` is how a run tells you it needed help.
        :param bus: The bus actor.
        :param step: Metres travelled in the tick just taken.
        """
        if self._dwell_remaining > 0.0:
            return
        if step > 0.05:
            self._since_repath_s = 0.0
            return

        if self._is_held_at_red(bus):
            # Obeying a traffic light is not stalling. Town01's 36 lights
            # cycle in groups of three, so an approach waits roughly 28 s of
            # red, and the clock below starts while the bus is still slowing
            # down. Without this, every long red looked like a stall:
            # measured over five tours, 121 of 124 re-plans fired on a bus
            # standing at a red light on a proper driving lane.
            #
            # Frozen rather than reset, so a bus genuinely jammed inside a
            # junction still carries its accumulated time into the green.
            self._stats["red_light_ticks"] += 1
            return

        if self._is_queued_behind_traffic(bus):
            # Waiting for the car in front is not stalling either, and the
            # red-light exemption above does not cover it: CARLA reports
            # ``is_at_traffic_light`` only inside the light's trigger volume,
            # which is to say at the *head* of the queue. Everyone behind the
            # first vehicle reads as a bus stopped on green for no reason.
            #
            # In an empty town the bus is always at the head, so this could
            # not arise and the gap went unnoticed until traffic was tested.
            self._stats["queued_ticks"] += 1
            return

        # Deliberately not gated on throttle. A bus the traffic manager has
        # given up on is commanded *zero* throttle, so gating on throttle
        # above zero was measured to never fire at all.
        self._since_repath_s += self._delta
        if self._since_repath_s >= self._repath_after_s:
            self._repath(bus)
            self._since_repath_s = 0.0

    def _is_queued_behind_traffic(self, bus: "carla.Actor") -> bool:
        """
        Return whether a stopped vehicle in the bus's own lane is the reason
        it is not moving.

        Only the traffic this backend spawned is considered, which makes the
        check **free in an empty town** — the common case, and the one every
        earlier measurement was taken in. It reads one world snapshot rather
        than querying each vehicle, so a stalled tick costs a single call.
        :param bus: The bus actor.
        :return: True if something stationary sits in the box directly ahead.
        """
        if not self._npc_ids:
            return False

        here = bus.get_location()
        forward = bus.get_transform().get_forward_vector()
        snapshot = self._world.get_snapshot()

        for npc_id in self._npc_ids:
            other = snapshot.find(npc_id)
            if other is None:
                continue
            offset = other.get_transform().location - here
            # Project into the bus's own frame: along its nose, and across it.
            ahead = offset.x * forward.x + offset.y * forward.y
            across = abs(offset.x * forward.y - offset.y * forward.x)
            if not 0.0 < ahead < QUEUE_AHEAD_M or across > QUEUE_LATERAL_M:
                continue
            if other.get_velocity().length() < QUEUE_STATIONARY_MS:
                return True
        return False

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

    def _steer_towards(self, bus: "carla.Actor", stop_id: str) -> None:
        """
        Tell the traffic manager how to reach a stop.

        Not *where* the stop is — which turns to take to get there.
        `set_path` with a single distant location reads like a destination
        but is a junction-decision API, and given one far-off point the
        traffic manager chooses greedily and can cycle: measured on Town01,
        a bus told only where its stop was drove a ten-road loop for ever
        and never entered the stop's road. Planning the route and handing
        over the turns took a five-tour campaign from 17/25 legs to 21/25.
        :param bus: The bus actor.
        :param stop_id: TransitGraph node ID of the stop to drive to.
        """
        origin = bus.get_location()
        self._issued_from = (origin.x, origin.y)
        route = plan_route(
            self._world.get_map(),
            origin,
            self._location_of(stop_id),
        )
        self._remember_debug_route(route)
        if route is None:
            # Nothing sensible to say, so fall back to the old behaviour of
            # simply aiming at the stop. It is poor, but an instruction the
            # traffic manager may mishandle still beats none at all.
            self._stats["unroutable_legs"] += 1
            self._traffic_manager.set_path(
                bus, [self._location_of(stop_id)]
            )
            return

        turns = turns_along(route)
        self._issued_turns = turns
        self._issued_at_s = self._stats["ticks"] * self._delta
        if turns:
            self._traffic_manager.set_route(bus, turns)
        # An empty list means the route crosses no junction where there is
        # anything to decide, and lane following already goes the right way.

    def _repath(self, bus: "carla.Actor") -> None:
        """Re-plan from where the bus is now and re-issue its turns."""
        if self._destination is None or self._traffic_manager is None:
            return
        self._steer_towards(bus, self._destination)
        self._stats["repaths"] += 1

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
