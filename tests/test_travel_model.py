"""Tests for TravelModel and GraphTravelModel."""

import pytest

from src.algorithm.TravelModel import GraphTravelModel, TravelModel
from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

_ORIGIN = "n4_n1"
_DESTINATION = "n1_n2"


@pytest.fixture
def graph() -> TransitGraph:
    """Transit graph built from the hand-authored test map."""
    return TransitGraph(_PROVIDER)


# ---------------------------------------------------------------------------
# Abstractness
# ---------------------------------------------------------------------------

def test_cannot_instantiate_abstract_class() -> None:
    with pytest.raises(TypeError):
        TravelModel()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# travel_time
# ---------------------------------------------------------------------------

def test_travel_time_matches_graph_cost(graph: TransitGraph) -> None:
    model = GraphTravelModel(graph)
    assert model.travel_time(_ORIGIN, _DESTINATION) == pytest.approx(
        graph.best_path_cost(_ORIGIN, _DESTINATION)
    )


def test_travel_time_unaffected_by_dwell(graph: TransitGraph) -> None:
    """Dwell is charged separately; it must not inflate driving time."""
    plain = GraphTravelModel(graph, dwell_s=0.0)
    dwelling = GraphTravelModel(graph, dwell_s=30.0)
    assert dwelling.travel_time(_ORIGIN, _DESTINATION) == pytest.approx(
        plain.travel_time(_ORIGIN, _DESTINATION)
    )


# ---------------------------------------------------------------------------
# dwell_time
# ---------------------------------------------------------------------------

def test_dwell_defaults_to_zero(graph: TransitGraph) -> None:
    """The default must reproduce the original dwell-free behaviour."""
    model = GraphTravelModel(graph)
    assert model.dwell_time(_ORIGIN, 0, 0) == pytest.approx(0.0)


def test_dwell_returns_the_constant(graph: TransitGraph) -> None:
    model = GraphTravelModel(graph, dwell_s=12.5)
    assert model.dwell_time(_ORIGIN, 0, 0) == pytest.approx(12.5)


def test_dwell_ignores_passenger_counts(graph: TransitGraph) -> None:
    """Constant dwell: the counts are accepted but not yet used."""
    model = GraphTravelModel(graph, dwell_s=12.5)
    assert model.dwell_time(_ORIGIN, 7, 3) == pytest.approx(12.5)


def test_dwell_is_same_for_every_stop(graph: TransitGraph) -> None:
    model = GraphTravelModel(graph, dwell_s=12.5)
    assert model.dwell_time(_ORIGIN, 0, 0) == pytest.approx(
        model.dwell_time(_DESTINATION, 0, 0)
    )


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

def test_graph_property_returns_the_injected_graph(
    graph: TransitGraph,
) -> None:
    """MockBackend reuses this rather than building a second graph."""
    assert GraphTravelModel(graph).graph is graph


def test_dwell_s_property_exposes_the_constant(graph: TransitGraph) -> None:
    assert GraphTravelModel(graph, dwell_s=4.0).dwell_s == pytest.approx(4.0)
