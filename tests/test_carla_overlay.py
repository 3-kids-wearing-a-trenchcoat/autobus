"""Tests for the world-space overlay CarlaBackend draws into CARLA.

The route line, the stop beacons and their names, and the counters pinned in
front of whatever the run is being watched from. Split out of
`test_carla_backend.py`, which was doing five jobs at 1,281 lines.

Everything here is world-space, drawn through `DebugHelper` with `draw_line`,
`draw_box` and `draw_point`. Nothing uses CARLA's `draw_hud_*` family: it is
world-space too despite the name, takes Location arguments in metres, has no
string variant, and on 0.9.16 renders nothing at all. Text is therefore drawn
as a stroke font by `carla_debug_text`, one `draw_line` per stroke.

No server and no pygame: a fake DebugHelper records what it was asked to
draw, and the tests read that back.
"""

import pytest
import carla
from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.simulation import carla_debug_text
from src.simulation.CarlaBackend import (
    DEBUG_LABEL_MAX_M,
    DEBUG_LABEL_MAX_STOPS,
    DEBUG_LABEL_MIN_M,
    DEBUG_MAX_SEGMENTS,
    DEBUG_REDRAW_S,
    DEBUG_ROUTE_STEP_M,
    DEBUG_ROUTE_THICKNESS_M,
    DEBUG_STOP_BOX_M,
    DEBUG_STOP_IDLE_COLOR,
    DEBUG_STOP_WAITING_COLOR,
    CarlaBackend,
)


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
        self.lifetimes: list[float] = []
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
        self.lifetimes.append(kwargs.get("life_time"))

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
    """A bus actor that only knows where it stands, and which way."""

    def __init__(self, x: float, y: float) -> None:
        self._location = carla.Location(x=x, y=y, z=0.0)

    def get_location(self) -> "carla.Location":
        return self._location

    def get_transform(self) -> "carla.Transform":
        return carla.Transform(self._location, carla.Rotation())


class _OpenViewer:
    """
    The least a viewer can be for the overlay's purposes: something that is
    open, and stops being open after ``alive`` updates. The recording fake
    lives in ``test_carla_viewer.py``, which is where the viewer's own
    contract is tested; the overlay only needs to know whether a window is
    there, because that is what decides which actor the labels face.
    """

    def __init__(self, alive: int = 99) -> None:
        self._alive, self._updates = alive, 0

    def update(self, lines, route=(), bus=None, table=None,
               hidden=0) -> bool:
        """Report whether the window is still open."""
        self._updates += 1
        return self._updates < self._alive

    def close(self) -> None:
        """Nothing to take down."""


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


def test_a_stop_is_a_box_and_its_name_and_nothing_else() -> None:
    """
    The counts that used to hang over a stop are on the request table now,
    where they read per passenger and cover no road.
    """
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool(
        waiting=("n0001_n_s01",), aboard=("n0002_n_s02",)))

    backend._draw_stops(debug, _FakeSpectator())

    assert len(debug.boxes) == 2                    # one box per stop
    # Only the names are lettered -- no beacon, no counts.
    strokes = sum(carla_debug_text.stroke_count(text)
                  for text in ("n0001_n_s01", "n0002_n_s02"))
    assert len(debug.lines) == strokes
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


def test_the_name_floats_clear_above_the_box() -> None:
    """Overlapping its own box would make the name harder to read, not less."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug)
    backend._note_stop_demand(_demand_pool(waiting=("n0001_n_s01",)))

    backend._draw_stops(debug, _FakeSpectator())

    lowest = min(point.z for pair in debug.lines for point in pair)
    assert lowest >= DEBUG_STOP_BOX_M * 2


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
    # Colour still carries the demand, which the table cannot show at a
    # glance; it is the box's colour now rather than a beacon's.
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

    backend._draw_hud(debug, _FakeSpectator())

    assert debug.lines, "nothing drawn"
    # The camera looks up +x from the origin, so the text must sit ahead.
    assert all(point.x > 0 for pair in debug.lines for point in pair)


def test_the_counters_say_the_time_the_leg_and_the_next_stop() -> None:
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    backend._destination = "n0002_n_s02"
    backend._stats["ticks"] = 1200                  # 60 s

    backend._draw_hud(debug, _FakeSpectator())

    expected = sum(carla_debug_text.stroke_count(text) for text in (
        "SIM 1:00", "LEG 1:00", "NEXT n0002_n_s02"))
    assert len(debug.lines) == expected


def test_the_counters_are_redrawn_every_tick() -> None:
    """
    They follow the camera, so any cadence slower than the tick shows as
    lag. Measured: redrawing every tick costs the same as four times a
    second, so there is nothing to buy by waiting.
    """
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)
    spectator = _FakeSpectator()

    backend._draw_hud(debug, spectator)
    after_one = len(debug.lines)
    assert after_one > 0

    backend._draw_hud(debug, spectator)
    assert len(debug.lines) == after_one * 2


def test_the_counters_outlive_a_tick_but_not_much() -> None:
    """Long enough not to flicker, short enough not to ghost."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)

    backend._draw_hud(debug, _FakeSpectator())

    assert debug.lifetimes[0] == pytest.approx(0.05 * 2.0)


def test_no_camera_means_no_counters() -> None:
    """There is nothing to pin them to, and no screen-space API to use."""
    debug = _FakeDebug()
    backend = _drawing_backend(debug, fixed_delta_s=0.05)

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


def test_the_counters_say_the_same_thing_on_both_surfaces() -> None:
    """
    The world overlay and the window read from one place, so they cannot
    drift apart and disagree about the clock.
    """
    backend = _backend(debug_draw=True, fixed_delta_s=0.05)
    backend._stats["ticks"] = 1200                  # 60 s
    backend.set_destination("n0002_n_s02")
    backend._stats["ticks"] = 2400                  # 120 s, 60 s into the leg

    lines = backend.hud_lines()

    assert lines[0] == "SIM  2:00"
    assert lines[1] == "LEG  1:00"
    assert lines[2] == "NEXT n0002_n_s02"


def test_the_counters_report_demand_once_there_is_any() -> None:
    backend = _backend(debug_draw=True)
    backend._note_stop_demand(_demand_pool(
        waiting=("n0001_n_s01", "n0002_n_s02"), aboard=("n0001_n_s01",)))

    assert "WAIT 2   ABOARD 1" in backend.hud_lines()


def test_the_counters_stay_quiet_about_faults_until_there_are_some() -> None:
    """A line that always reads zero is a line nobody keeps reading."""
    backend = _backend(debug_draw=True)
    assert not any("DROPS" in line for line in backend.hud_lines())

    backend._stats["route_drops"] = 3
    assert "DROPS 3   UNROUTABLE 0" in backend.hud_lines()


def test_the_labels_follow_the_spectator_with_no_viewer() -> None:
    spectator = _FakeSpectator()
    backend = _backend(debug_draw=True)

    assert backend._overlay_eye(spectator) is spectator


def test_the_labels_follow_the_bus_once_the_viewer_is_open() -> None:
    """
    The viewer's camera rides the bus. Choosing labels by distance to a
    spectator parked across town would letter stops nobody is looking at.
    """
    debug = _FakeDebug()
    backend = _drawing_backend(debug, viewer=True)
    backend._viewer = _OpenViewer()

    assert backend._overlay_eye(_FakeSpectator()) is backend._bus()


def test_a_closed_viewer_hands_the_labels_back_to_the_spectator() -> None:
    spectator = _FakeSpectator()
    backend = _drawing_backend(_FakeDebug(), viewer=True)
    backend._viewer = _OpenViewer(alive=1)

    backend._pump_viewer()

    assert backend._overlay_eye(spectator) is spectator
