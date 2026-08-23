"""Tests for the viewer's map maths, which needs no window and no server.

Everything drawn is geometry: what the minimap projects, which road segment
it calls "here", and what it builds from a provider. The pygame surface work
is left to pygame.
"""

import math

import carla
import pytest

from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.simulation.CarlaBackend import (
    HUD_MAX_REQUEST_ROWS,
    REQUEST_COLUMNS,
    CarlaBackend,
)
from src.simulation import carla_viewer
from src.simulation.carla_viewer import (
    MAP_MARGIN,
    MAP_SIZE,
    TABLE_GAP,
    VIEW_GRAPH,
    VIEW_MAP,
    CarlaViewer,
    MiniMap,
    format_table,
    minimap_from,
    schematic_from,
)


class _Node:
    def __init__(self, node_id: str, x: float, y: float) -> None:
        self.id, self.x, self.y = node_id, x, y


class _Edge:
    def __init__(self, edge_id: str, a: str, b: str) -> None:
        self.id, self.from_node_id, self.to_node_id = edge_id, a, b


class _Stop:
    def __init__(self, edge_id: str, x: float, y: float,
                 name: str | None = None) -> None:
        self.edge, self.x, self.y = _Edge(edge_id, "", ""), x, y
        self.name = name


class _Provider:
    """The smallest thing that looks like a MapProvider."""

    def __init__(self, nodes, edges, stops) -> None:
        self._nodes, self._edges, self._stops = nodes, edges, stops

    def get_road_nodes(self):
        return self._nodes

    def get_road_edges(self):
        return self._edges

    def get_stops(self):
        return self._stops


SQUARE = MiniMap(
    edges=((0.0, 0.0, 100.0, 0.0), (100.0, 0.0, 100.0, 100.0),
           (100.0, 100.0, 0.0, 100.0), (0.0, 100.0, 0.0, 0.0)),
    stops=(("s_a", 0.0, 0.0), ("s_b", 100.0, 100.0)),
)


def _fitted(minimap: MiniMap = SQUARE) -> CarlaViewer:
    """A viewer with only its map maths set up — no window, no camera."""
    viewer = CarlaViewer.__new__(CarlaViewer)
    viewer._minimap = minimap
    viewer._map_rect = CarlaViewer._fit_minimap(minimap, (1280, 720))
    return viewer


# ---------------------------------------------------------------------------
# Building it from a provider
# ---------------------------------------------------------------------------

def test_a_provider_becomes_plain_coordinates() -> None:
    """The simulation layer must not learn any map-layer type."""
    provider = _Provider(
        nodes=[_Node("n1", 0.0, 0.0), _Node("n2", 10.0, 5.0)],
        edges=[_Edge("e1", "n1", "n2")],
        stops=[_Stop("e1", 3.0, 4.0)])

    built = minimap_from(provider)

    assert built.edges == ((0.0, 0.0, 10.0, 5.0),)
    assert built.stops == (("e1", 3.0, 4.0),)


def test_an_edge_naming_a_missing_node_is_skipped() -> None:
    """Town03 reports dangling nodes; the panel must not die of one."""
    provider = _Provider(
        nodes=[_Node("n1", 0.0, 0.0)],
        edges=[_Edge("e1", "n1", "gone")],
        stops=[])

    assert minimap_from(provider).edges == ()


# ---------------------------------------------------------------------------
# Projecting it into the panel
# ---------------------------------------------------------------------------

def test_an_empty_graph_gets_no_panel() -> None:
    assert CarlaViewer._fit_minimap(MiniMap((), ()), (1280, 720)) is None
    assert CarlaViewer._fit_minimap(None, (1280, 720)) is None


def test_the_graph_is_centred_in_the_panel() -> None:
    viewer = _fitted()
    centre = viewer._to_panel(50.0, 50.0)

    left = 1280 - MAP_SIZE[0] - MAP_MARGIN
    top = 720 - MAP_SIZE[1] - MAP_MARGIN
    assert centre == (left + MAP_SIZE[0] // 2, top + MAP_SIZE[1] // 2)


def test_the_projection_keeps_the_towns_shape() -> None:
    """One scale for both axes, or a long town comes out square."""
    wide = MiniMap(edges=((0.0, 0.0, 200.0, 0.0), (0.0, 0.0, 0.0, 50.0)),
                   stops=())
    viewer = _fitted(wide)

    across = viewer._to_panel(200.0, 0.0)[0] - viewer._to_panel(0.0, 0.0)[0]
    down = viewer._to_panel(0.0, 50.0)[1] - viewer._to_panel(0.0, 0.0)[1]
    assert across / down == pytest.approx(200.0 / 50.0, rel=0.05)


def test_the_graph_stays_inside_its_panel() -> None:
    viewer = _fitted()
    left = 1280 - MAP_SIZE[0] - MAP_MARGIN
    top = 720 - MAP_SIZE[1] - MAP_MARGIN

    for x, y in ((0.0, 0.0), (100.0, 100.0), (0.0, 100.0), (100.0, 0.0)):
        px, py = viewer._to_panel(x, y)
        assert left <= px <= left + MAP_SIZE[0]
        assert top <= py <= top + MAP_SIZE[1]


# ---------------------------------------------------------------------------
# Which edge the bus is on
# ---------------------------------------------------------------------------

def test_the_bus_is_placed_on_the_nearest_road() -> None:
    viewer = _fitted()

    here = viewer._nearest_edge(carla.Location(x=50.0, y=4.0, z=0.0))

    assert here == (0.0, 0.0, 100.0, 0.0)


def test_nearest_measures_to_the_segment_not_its_ends() -> None:
    """
    Mid-way along a long road both endpoints are far off. Measuring to them
    would hand the bus a short road it is nowhere near.
    """
    minimap = MiniMap(
        edges=((0.0, 0.0, 1000.0, 0.0),          # the long road it is on
               (500.0, 60.0, 520.0, 60.0)),      # a short one, ends nearer
        stops=())
    viewer = _fitted(minimap)

    here = viewer._nearest_edge(carla.Location(x=505.0, y=5.0, z=0.0))

    assert here == (0.0, 0.0, 1000.0, 0.0)


def test_a_zero_length_edge_does_not_divide_by_zero() -> None:
    viewer = _fitted(MiniMap(edges=((7.0, 7.0, 7.0, 7.0),), stops=()))

    assert viewer._nearest_edge(carla.Location(x=0.0, y=0.0, z=0.0)) \
        == (7.0, 7.0, 7.0, 7.0)


def test_an_empty_graph_has_no_nearest_edge() -> None:
    viewer = _fitted(MiniMap(edges=(), stops=()))
    viewer._minimap = MiniMap(edges=(), stops=())

    assert viewer._nearest_edge(carla.Location(x=0.0, y=0.0, z=0.0)) is None


# ---------------------------------------------------------------------------
# Placing stop names
# ---------------------------------------------------------------------------

def _panel_edges() -> tuple[int, int]:
    """Return the panel's left and top, for the default window."""
    return (1280 - MAP_SIZE[0] - MAP_MARGIN, 720 - MAP_SIZE[1] - MAP_MARGIN)


def test_a_name_sits_to_the_right_of_its_stop() -> None:
    viewer = _fitted()
    left, top = _panel_edges()

    at = viewer._label_at((left + 40, top + 40), width=60, radius=3)

    assert at[0] > left + 40


def test_a_name_flips_left_rather_than_overflow() -> None:
    """A clipped id reads as a different stop, which is worse than none."""
    viewer = _fitted()
    left, top = _panel_edges()
    anchor = (left + MAP_SIZE[0] - 10, top + 40)

    at = viewer._label_at(anchor, width=90, radius=3)

    assert at[0] < anchor[0]
    assert at[0] + 90 <= left + MAP_SIZE[0]


def test_a_name_stays_inside_the_panel_vertically() -> None:
    viewer = _fitted()
    left, top = _panel_edges()

    high = viewer._label_at((left + 40, top), width=40, radius=3)
    low = viewer._label_at((left + 40, top + MAP_SIZE[1]), width=40, radius=3)

    assert high[1] >= top
    assert low[1] <= top + MAP_SIZE[1]


# ---------------------------------------------------------------------------
# Laying out the request table
# ---------------------------------------------------------------------------

def test_columns_are_padded_to_a_common_width() -> None:
    lines = format_table(("FROM", "TO"), [("s1", "s22"), ("s333", "s4")])

    # "FROM" and "s333" are the widest first cells, so the second column
    # starts at 4 + the gap on every line.
    start = 4 + len(TABLE_GAP)
    assert [line[start:] for line in lines] == ["TO", "s22", "s4"]


def test_a_long_cell_widens_its_column_rather_than_breaking_the_row() -> None:
    lines = format_table(("FROM", "TO"),
                         [("a_very_long_stop_id", "s2"), ("s1", "s3")])

    start = len("a_very_long_stop_id") + len(TABLE_GAP)
    assert [line[start:] for line in lines] == ["TO", "s2", "s3"]


def test_the_header_comes_first() -> None:
    lines = format_table(("FROM", "TO"), [("s1", "s2")])
    assert lines[0].startswith("FROM")
    assert len(lines) == 2


def test_no_header_means_no_table() -> None:
    assert format_table((), [("s1", "s2")]) == []


def test_a_header_with_no_rows_is_still_a_header() -> None:
    """The viewer decides whether an empty table is worth drawing."""
    assert format_table(("FROM", "TO"), []) == ["FROM  TO"]


def test_rows_longer_than_the_header_are_clipped() -> None:
    """A stray column would push every later row out of alignment."""
    lines = format_table(("FROM",), [("s1", "extra")])
    assert lines == ["FROM", "s1"]


# ---------------------------------------------------------------------------
# Opening and closing the window
#
# Real pygame against SDL's dummy driver, so there is a display to take down
# without anything appearing on screen. CARLA is stubbed: none of this is
# about the camera, only about what is left behind.
# ---------------------------------------------------------------------------

class _StubCamera:
    def __init__(self) -> None:
        self.stopped = False
        self.destroyed = False

    def listen(self, callback) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def destroy(self) -> None:
        self.destroyed = True


class _StubBlueprint:
    def set_attribute(self, *args) -> None:
        pass


class _StubWorld:
    def __init__(self) -> None:
        self.cameras: list[_StubCamera] = []

    def get_blueprint_library(self):
        class _Library:
            def find(self, name):
                return _StubBlueprint()
        return _Library()

    def spawn_actor(self, *args, **kwargs) -> _StubCamera:
        camera = _StubCamera()
        self.cameras.append(camera)
        return camera


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch):
    """Give SDL a driver that needs no screen, and start from no viewer."""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setattr(carla_viewer, "_ACTIVE", None)
    yield
    if carla_viewer._ACTIVE is not None:
        carla_viewer._ACTIVE.close()


def _opened(world: _StubWorld) -> CarlaViewer:
    """Open a viewer over a stubbed world."""
    return CarlaViewer(world, follow=object(), size=(320, 200))


def test_closing_takes_the_camera_down_with_the_window(headless) -> None:
    """A leaked sensor keeps rendering for a trial that has finished."""
    world = _StubWorld()

    _opened(world).close()

    assert world.cameras[0].stopped
    assert world.cameras[0].destroyed


def test_closing_leaves_the_rest_of_pygame_alone(headless) -> None:
    """
    ``pygame.quit()`` would take font and every other module with it, which
    is not this object's to take away — a later trial in the same process
    would find them gone.
    """
    import pygame

    _opened(_StubWorld()).close()

    assert not pygame.display.get_init()
    assert pygame.font.get_init()


def test_a_second_window_is_refused_rather_than_fought_over(
    headless,
) -> None:
    """
    SDL gives a process one window: a second ``set_mode`` resizes the first.
    Two viewers would draw into the same surface, which looks like flicker
    rather than an error.
    """
    world = _StubWorld()
    first = _opened(world)

    with pytest.raises(RuntimeError, match="already open"):
        _opened(world)

    first.close()


def test_a_later_trial_can_open_its_own_window(headless) -> None:
    """Refusing two at once must not refuse one after another."""
    world = _StubWorld()
    _opened(world).close()

    second = _opened(world)

    assert not second.closed
    second.close()


def test_closing_twice_is_harmless(headless) -> None:
    """Teardown runs from a finally, and may run after the user closed it."""
    viewer = _opened(_StubWorld())
    viewer.close()
    viewer.close()


def test_the_safety_net_closes_a_viewer_nobody_closed(headless) -> None:
    """
    A run that dies part-way must not leave a window behind. Registered with
    atexit for the same reason ``carla_stdout.restore`` is.
    """
    viewer = _opened(_StubWorld())

    carla_viewer.close_active()

    assert viewer.closed
    assert carla_viewer._ACTIVE is None


def test_the_safety_net_is_harmless_with_no_viewer(headless) -> None:
    carla_viewer.close_active()


def test_the_minimap_fits_whatever_window_it_is_given() -> None:
    """
    Layout follows the surface SDL hands back, not the size asked for, so a
    window that came back smaller still keeps its panels on screen.
    """
    for size in ((1280, 720), (1920, 1080), (2560, 1440)):
        viewer = CarlaViewer.__new__(CarlaViewer)
        viewer._minimap = SQUARE
        viewer._map_rect = CarlaViewer._fit_minimap(SQUARE, size)

        for x, y in ((0.0, 0.0), (100.0, 100.0), (0.0, 100.0)):
            px, py = viewer._to_panel(x, y)
            assert 0 <= px <= size[0], size
            assert 0 <= py <= size[1], size


def test_a_resized_window_moves_the_panels_with_it(headless) -> None:
    """A layout fixed at construction strands the panels off the edge."""
    viewer = CarlaViewer(_StubWorld(), follow=object(), size=(320, 200),
                         minimap=SQUARE)
    was = viewer._map_rect
    assert was is not None

    viewer._screen = viewer._pygame.display.set_mode((640, 480))
    viewer._relayout()

    assert viewer._size == (640, 480)
    assert viewer._map_rect != was
    viewer.close()


# ---------------------------------------------------------------------------
# The schematic, and the tabs that reach it
# ---------------------------------------------------------------------------

class _CostGraph:
    """A stand-in for TransitGraph: costs by pair, nothing else."""

    def __init__(self, costs: dict[tuple[str, str], float]) -> None:
        self._costs = costs

    def best_path_cost(self, origin: str, destination: str):
        return self._costs.get((origin, destination))


THREE = (("a", 0.0, 0.0), ("b", 10.0, 0.0), ("c", 0.0, 10.0))


def _line_costs(far: float = 100.0) -> _CostGraph:
    """a-b cheap, a-c dear, so the layout has something to say."""
    return _CostGraph({
        ("a", "b"): 10.0, ("b", "a"): 10.0,
        ("a", "c"): far, ("c", "a"): far,
        ("b", "c"): far, ("c", "b"): far,
    })


def test_the_schematic_keeps_every_stop() -> None:
    built = schematic_from(_line_costs(), THREE)

    assert [node[0] for node in built.nodes] == ["a", "b", "c"]


def test_schematic_positions_are_inside_the_unit_square() -> None:
    built = schematic_from(_line_costs(), THREE)

    for _, x, y in built.nodes:
        assert 0.0 <= x <= 1.0
        assert 0.0 <= y <= 1.0


def test_a_costly_pair_is_laid_out_further_apart() -> None:
    """
    The whole point of the view: distance is drive time, so a stop that is
    a street away but a long drive lands far off.
    """
    built = schematic_from(_line_costs(far=400.0), THREE)
    at = {stop: (x, y) for stop, x, y in built.nodes}

    cheap = math.dist(at["a"], at["b"])
    dear = math.dist(at["a"], at["c"])
    assert dear > cheap


def test_the_layout_is_deterministic() -> None:
    """A view that rearranges itself between runs cannot be compared."""
    assert schematic_from(_line_costs(), THREE).nodes == \
        schematic_from(_line_costs(), THREE).nodes


def test_a_stop_nothing_can_reach_is_flagged() -> None:
    """Graph pathology no picture of a town can show."""
    graph = _CostGraph({("a", "b"): 10.0, ("b", "a"): 10.0})

    built = schematic_from(graph, THREE)

    assert built.stranded == (2,)          # "c" reaches nothing


def test_only_the_cheapest_links_are_drawn() -> None:
    """Every pair is reachable; drawing all of them says nothing."""
    built = schematic_from(_line_costs(), THREE, neighbours=1)

    assert len(built.links) <= 3
    assert all(a < b for a, b in built.links)


def test_a_single_stop_does_not_divide_by_zero() -> None:
    built = schematic_from(_CostGraph({}), (("only", 5.0, 5.0),))

    assert built.nodes == (("only", 0.5, 0.5),)


def test_the_tabs_sit_above_the_panel(headless) -> None:
    viewer = CarlaViewer(_StubWorld(), follow=object(), size=(1280, 720),
                         minimap=SQUARE,
                         schematic=schematic_from(_line_costs(), THREE))
    _, _, _, (left, top) = viewer._map_rect

    tabs = viewer._tab_rects()

    assert [view for view, _ in tabs] == [VIEW_MAP, VIEW_GRAPH]
    for _, (x, y, _, height) in tabs:
        assert y + height <= top
        assert x >= left
    viewer.close()


def test_there_is_no_second_tab_without_a_schematic(headless) -> None:
    """A tab that opens an empty view is worse than no tab."""
    viewer = CarlaViewer(_StubWorld(), follow=object(), size=(1280, 720),
                         minimap=SQUARE)

    assert [view for view, _ in viewer._tab_rects()] == [VIEW_MAP]
    viewer.close()


def test_clicking_a_tab_switches_the_view(headless) -> None:
    viewer = CarlaViewer(_StubWorld(), follow=object(), size=(1280, 720),
                         minimap=SQUARE,
                         schematic=schematic_from(_line_costs(), THREE))
    assert viewer._view == VIEW_MAP
    _, (x, y, width, height) = viewer._tab_rects()[1]

    assert viewer._click_tab((x + width // 2, y + height // 2))

    assert viewer._view == VIEW_GRAPH
    viewer.close()


def test_a_click_elsewhere_changes_nothing(headless) -> None:
    viewer = CarlaViewer(_StubWorld(), follow=object(), size=(1280, 720),
                         minimap=SQUARE,
                         schematic=schematic_from(_line_costs(), THREE))

    assert not viewer._click_tab((5, 5))

    assert viewer._view == VIEW_MAP
    viewer.close()


def test_the_layout_fills_both_axes() -> None:
    """
    Unclamped forces fold the arrangement onto one axis: measured on Town03,
    a 0.91 x 1.00 spread settled to 1.00 x 0.04 and drew as a line across
    the top of the panel.
    """
    # A 3x4 grid whose costs are its real distances, so the arrangement it
    # should recover is genuinely two-dimensional.
    grid = {f"s{n}": (n % 4, n // 4) for n in range(12)}
    stops = tuple((stop, float(col) * 30.0, float(row) * 30.0)
                  for stop, (col, row) in grid.items())
    costs = {(a, b): 60.0 * math.dist(grid[a], grid[b])
             for a in grid for b in grid if a != b}

    built = schematic_from(_CostGraph(costs), stops)

    xs = [x for _, x, _ in built.nodes]
    ys = [y for _, _, y in built.nodes]
    assert max(xs) - min(xs) > 0.5
    assert max(ys) - min(ys) > 0.5


# ---------------------------------------------------------------------------
# The backend's side of the viewer
#
# No pygame and no server here: the backend's contract is that it builds the
# HUD text, pumps whatever window it was given, and never lets that window
# take a trial down. Moved from `test_carla_backend.py`.
# ---------------------------------------------------------------------------

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
# The viewer window
#
# No pygame and no server here: the backend's side of the contract is that it
# builds the HUD text, pumps whatever it was given, and never lets a window
# take a trial down.
# ---------------------------------------------------------------------------

class _FakeViewer:
    """Stands in for the pygame window."""

    def __init__(self, alive: int = 99, explode: bool = False) -> None:
        self.seen: list[list[str]] = []
        self.routes: list[list[str]] = []
        self.tables: list[object] = []
        self.closed = False
        self._alive = alive
        self._explode = explode

    def update(self, lines, route=(), bus=None, table=None,
               hidden=0) -> bool:
        if self._explode:
            raise RuntimeError("display gone")
        self.seen.append(list(lines))
        self.routes.append(list(route))
        self.tables.append(table)
        return len(self.seen) < self._alive

    def close(self) -> None:
        self.closed = True


def test_the_viewer_is_off_by_default() -> None:
    """A campaign must never open a window."""
    assert _backend()._viewer_wanted is False
    assert _backend()._viewer is None


def test_the_viewer_is_given_the_counters_every_tick() -> None:
    backend = _backend(viewer=True)
    backend._viewer = _FakeViewer()

    backend._pump_viewer()
    backend._pump_viewer()

    assert len(backend._viewer.seen) == 2
    assert backend._viewer.seen[0][0].startswith("SIM")


def test_the_viewer_is_given_the_plan_for_its_minimap() -> None:
    backend = _backend(viewer=True)
    backend._viewer = _FakeViewer()
    backend.set_planned_route(["n0002_n_s02", "n0001_n_s01"])

    backend._pump_viewer()

    assert backend._viewer.routes[0] == ["n0002_n_s02", "n0001_n_s01"]


def test_closing_the_window_drops_it_for_the_rest_of_the_run() -> None:
    """The trial carries on; it just stops paying for a window nobody has."""
    backend = _backend(viewer=True)
    viewer = _FakeViewer(alive=1)
    backend._viewer = viewer

    backend._pump_viewer()

    assert backend._viewer is None
    assert viewer.closed


def test_a_viewer_that_fails_never_takes_the_trial_down() -> None:
    backend = _backend(viewer=True)
    backend._viewer = _FakeViewer(explode=True)

    backend._pump_viewer()

    assert backend._viewer is None


def test_pumping_without_a_viewer_is_harmless() -> None:
    """Every tick calls this, and most runs have no window at all."""
    _backend()._pump_viewer()


# ---------------------------------------------------------------------------
# The live-request table
# ---------------------------------------------------------------------------

def _pool_with(*requests: Request) -> RequestPool:
    """Build a pool holding exactly these requests."""
    pool = RequestPool()
    for request in requests:
        pool.add(request)
    return pool


def _waiting(request_id: str, pickup: str, dropoff: str,
             created_at: float) -> Request:
    """A passenger still at the stop."""
    return Request(id=request_id, pickup_stop_id=pickup,
                   dropoff_stop_id=dropoff, created_at=created_at)


def _aboard(request_id: str, pickup: str, dropoff: str, created_at: float,
            boarded_at: float) -> Request:
    """A passenger already riding."""
    return Request(id=request_id, pickup_stop_id=pickup,
                   dropoff_stop_id=dropoff, created_at=created_at,
                   state=RequestState.ON_BUS, boarded_at=boarded_at)


def _table_backend(ticks: int = 0) -> CarlaBackend:
    """A backend that will build the table, with its clock set."""
    backend = _backend(viewer=True, fixed_delta_s=0.05)
    backend._stats["ticks"] = ticks
    return backend


def test_the_table_is_not_built_without_something_to_show_it() -> None:
    """It costs two passes over the pool; a campaign should not pay them."""
    backend = _backend(viewer=False)

    backend._note_requests(_pool_with(
        _waiting("r1", "n0001_n_s01", "n0002_n_s02", 0.0)))

    assert backend.request_table()[1] == []


def test_a_waiting_passenger_shows_where_and_how_long() -> None:
    backend = _table_backend(ticks=1200)          # 60 s

    backend._note_requests(_pool_with(
        _waiting("r1", "n0001_n_s01", "n0002_n_s02", 15.0)))

    header, rows = backend.request_table()
    assert header == REQUEST_COLUMNS
    assert rows == [("s01", "s02", "0:45", "-", "WAITING")]


def test_a_rider_shows_the_wait_it_finished_and_the_ride_so_far() -> None:
    """
    WAIT keeps meaning time spent at the stop, so it stops at boarding --
    the two columns are the two halves of the KPI the campaign reports.
    """
    backend = _table_backend(ticks=2400)          # 120 s

    backend._note_requests(_pool_with(
        _aboard("r1", "n0001_n_s01", "n0002_n_s02", 10.0, 40.0)))

    assert backend.request_table()[1] == [
        ("s01", "s02", "0:30", "1:20", "ON BUS")]


def test_finished_and_abandoned_requests_are_not_listed() -> None:
    """The table is about work outstanding."""
    backend = _table_backend(ticks=2000)
    pool = _pool_with(
        _waiting("r1", "n0001_n_s01", "n0002_n_s02", 0.0),
        _waiting("r2", "n0002_n_s02", "n0001_n_s01", 0.0),
        _waiting("r3", "n0001_n_s01", "n0002_n_s02", 0.0))
    pool.update_state("r2", RequestState.ON_BUS)
    pool.update_state("r2", RequestState.FULFILLED)
    pool.cancel("r3")

    backend._note_requests(pool)

    assert len(backend.request_table()[1]) == 1


def test_the_longest_wait_is_at_the_top() -> None:
    """That passenger is the closest to giving up, which is the news."""
    backend = _table_backend(ticks=4000)          # 200 s
    backend._note_requests(_pool_with(
        _waiting("new", "n0001_n_s01", "n0002_n_s02", 150.0),
        _waiting("old", "n0002_n_s02", "n0001_n_s01", 10.0)))

    rows = backend.request_table()[1]

    assert rows[0][2] == "3:10"                   # the older wait
    assert rows[1][2] == "0:50"


def test_waiting_passengers_come_before_riders() -> None:
    backend = _table_backend(ticks=4000)

    backend._note_requests(_pool_with(
        _aboard("r1", "n0001_n_s01", "n0002_n_s02", 0.0, 10.0),
        _waiting("r2", "n0002_n_s02", "n0001_n_s01", 100.0)))

    states = [row[4] for row in backend.request_table()[1]]
    assert states == ["WAITING", "ON BUS"]


def test_a_long_table_is_trimmed_and_says_so() -> None:
    backend = _table_backend(ticks=2000)
    many = [_waiting(f"r{n}", "n0001_n_s01", "n0002_n_s02", float(n))
            for n in range(HUD_MAX_REQUEST_ROWS + 5)]

    backend._note_requests(_pool_with(*many))

    assert len(backend.request_table()[1]) == HUD_MAX_REQUEST_ROWS
    assert backend.hidden_requests == 5


def test_stop_names_are_shortened_when_that_stays_unambiguous() -> None:
    backend = _table_backend()
    assert backend._shorten("n0001_n_s01") == "s01"


def test_colliding_short_names_fall_back_to_the_full_id() -> None:
    """A short name that names two stops is worse than a long one."""
    backend = _backend(viewer=True, stop_positions={
        "a_s01_x": (0.0, 0.0, 0.0), "b_s01_y": (1.0, 1.0, 0.0)},
        position="a_s01_x")

    assert backend._shorten("a_s01_x") == "a_s01_x"


def test_a_stop_with_no_number_keeps_its_name() -> None:
    backend = _backend(viewer=True, stop_positions={
        "depot": (0.0, 0.0, 0.0), "yard": (1.0, 1.0, 0.0)},
        position="depot")

    assert backend._shorten("depot") == "depot"


def test_the_plan_line_uses_the_short_stop_names() -> None:
    """At full length four stop IDs run under the request table."""
    backend = _backend(viewer=True)
    backend.set_planned_route(["n0002_n_s02", "n0001_n_s01"])

    assert "PLAN s02 > s01" in backend.hud_lines()


def test_the_minimap_labels_stops_by_their_authored_name() -> None:
    """
    The panels draw "s04"; the node ID stays the key, because the map
    matches it against the planned route and the schematic prices it
    through the graph.
    """
    provider = _Provider(
        nodes=[_Node("n1", 0.0, 0.0), _Node("n2", 10.0, 5.0)],
        edges=[_Edge("n_s04_n0272", "n1", "n2")],
        stops=[_Stop("n_s04_n0272", 1.0, 2.0, name="s04")])

    minimap = minimap_from(provider)

    assert minimap.stops == (("n_s04_n0272", 1.0, 2.0),)
    assert minimap.labels == {"n_s04_n0272": "s04"}


def test_a_provider_that_names_nothing_labels_nothing() -> None:
    """A map layer with no authored names must still draw a minimap."""
    provider = _Provider(
        nodes=[_Node("n1", 0.0, 0.0), _Node("n2", 10.0, 5.0)],
        edges=[_Edge("e1", "n1", "n2")],
        stops=[_Stop("e1", 1.0, 2.0)])

    assert minimap_from(provider).labels == {}


def test_an_unlabelled_stop_is_drawn_under_its_id() -> None:
    """The ID is the fallback, so a panel never draws a blank next to a
    stop the map layer did not name."""
    viewer = _fitted()

    assert viewer._stop_label("s_a") == "s_a"
