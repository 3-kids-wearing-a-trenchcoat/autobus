"""Tests for the viewer's map maths, which needs no window and no server.

Everything drawn is geometry: what the minimap projects, which road segment
it calls "here", and what it builds from a provider. The pygame surface work
is left to pygame.
"""

import carla
import pytest

from src.simulation.carla_viewer import (
    MAP_MARGIN,
    MAP_SIZE,
    CarlaViewer,
    MiniMap,
    minimap_from,
)


class _Node:
    def __init__(self, node_id: str, x: float, y: float) -> None:
        self.id, self.x, self.y = node_id, x, y


class _Edge:
    def __init__(self, edge_id: str, a: str, b: str) -> None:
        self.id, self.from_node_id, self.to_node_id = edge_id, a, b


class _Stop:
    def __init__(self, edge_id: str, x: float, y: float) -> None:
        self.edge, self.x, self.y = _Edge(edge_id, "", ""), x, y


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
