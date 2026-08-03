"""Tests for TravelModel, GraphTravelModel and NoisyTravelModel."""

import random
import statistics

import pytest

from src.algorithm.TravelModel import (
    GraphTravelModel,
    NoisyTravelModel,
    TravelModel,
)
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


# ---------------------------------------------------------------------------
# NoisyTravelModel
# ---------------------------------------------------------------------------

def _draws(model: NoisyTravelModel, n: int) -> list[float]:
    """Query the same leg n times and collect the perturbed times."""
    return [model.travel_time(_ORIGIN, _DESTINATION) for _ in range(n)]


def test_zero_sigma_reproduces_the_plain_model(graph: TransitGraph) -> None:
    """The default must leave every existing result untouched."""
    plain = GraphTravelModel(graph)
    noisy = NoisyTravelModel(graph, sigma=0.0, seed=1)
    assert _draws(noisy, 5) == [plain.travel_time(_ORIGIN, _DESTINATION)] * 5


def test_same_seed_gives_the_same_sequence(graph: TransitGraph) -> None:
    assert _draws(NoisyTravelModel(graph, sigma=0.2, seed=42), 10) == _draws(
        NoisyTravelModel(graph, sigma=0.2, seed=42), 10
    )


def test_different_seeds_give_different_sequences(
    graph: TransitGraph,
) -> None:
    assert _draws(NoisyTravelModel(graph, sigma=0.2, seed=1), 10) != _draws(
        NoisyTravelModel(graph, sigma=0.2, seed=2), 10
    )


def test_does_not_consume_the_global_generator(graph: TransitGraph) -> None:
    """
    The decisive isolation test.

    run_trial seeds the global generator for demand. If noise drew from it
    too, adding noise would shift the demand stream and a noisy run could
    never be compared against the baseline it is meant to be measured
    against — the experiment would silently answer a different question.
    """
    random.seed(1234)
    expected = [random.random() for _ in range(3)]

    random.seed(1234)
    _draws(NoisyTravelModel(graph, sigma=0.3, seed=7), 20)
    assert [random.random() for _ in range(3)] == expected


def test_perturbation_actually_varies(graph: TransitGraph) -> None:
    assert len(set(_draws(NoisyTravelModel(graph, sigma=0.2, seed=3), 20))) > 1


def test_times_stay_positive(graph: TransitGraph) -> None:
    """A lognormal factor cannot go negative, however wide the spread."""
    assert all(t > 0.0 for t in _draws(
        NoisyTravelModel(graph, sigma=1.5, seed=5), 200
    ))


def test_median_tracks_the_unperturbed_time(graph: TransitGraph) -> None:
    """Median 1.0 is what makes sigma readable as a spread, not a slowdown."""
    base = GraphTravelModel(graph).travel_time(_ORIGIN, _DESTINATION)
    sample = _draws(NoisyTravelModel(graph, sigma=0.25, seed=11), 4000)
    assert statistics.median(sample) == pytest.approx(base, rel=0.05)


def test_mean_exceeds_the_unperturbed_time(graph: TransitGraph) -> None:
    """
    Delay must accumulate rather than cancel out.

    A right-skewed factor is the point of choosing lognormal over a symmetric
    one: a bus loses more to congestion than it recovers by running early, so
    a long route drifts late instead of averaging back to schedule.
    """
    base = GraphTravelModel(graph).travel_time(_ORIGIN, _DESTINATION)
    sample = _draws(NoisyTravelModel(graph, sigma=0.25, seed=11), 4000)
    assert statistics.fmean(sample) > base


def test_dwell_is_not_perturbed(graph: TransitGraph) -> None:
    """Only driving is unpredictable here; dwell stays the constant."""
    noisy = NoisyTravelModel(graph, dwell_s=9.0, sigma=0.5, seed=1)
    assert {noisy.dwell_time(_ORIGIN, 0, 0) for _ in range(10)} == {9.0}


def test_negative_sigma_is_rejected(graph: TransitGraph) -> None:
    with pytest.raises(ValueError):
        NoisyTravelModel(graph, sigma=-0.1)


def test_inherits_the_accessors_mock_backend_needs(
    graph: TransitGraph,
) -> None:
    """MockBackend reads .graph and .dwell_s off whatever model it is given."""
    noisy = NoisyTravelModel(graph, dwell_s=6.0, sigma=0.2, seed=1)
    assert noisy.graph is graph
    assert noisy.dwell_s == pytest.approx(6.0)
    assert noisy.sigma == pytest.approx(0.2)
