"""Tests for MockBackend: dead reckoning over a TransitGraph."""

import pytest

from src.algorithm.TravelModel import GraphTravelModel
from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.TransitGraph import TransitGraph
from src.map.map_data import RoadNodeRecord
from src.simulation.MockBackend import MockBackend

# Four nodes in a line, 1300 m apart. At DEFAULT_SPEED_LIMIT (130 km/h) each
# segment takes exactly 36 s, so distances translate into round numbers.
#
#   n1 ──1300m──► n2 ──1300m──► n3 ──1300m──► n4
#
# TransitGraph nodes are the road edges: "n1_n2", "n2_n3", "n3_n4".
_SEGMENT_S = 36.0

_PROVIDER = StaticMapProvider(
    stops=[
        StopInput("s1", "n1", "n2", 0.5),
        StopInput("s2", "n2", "n3", 0.5),
        StopInput("s3", "n3", "n4", 0.5),
    ],
    nodes=[
        RoadNodeRecord("n1", x=0.0, y=0.0),
        RoadNodeRecord("n2", x=1300.0, y=0.0),
        RoadNodeRecord("n3", x=2600.0, y=0.0),
        RoadNodeRecord("n4", x=3900.0, y=0.0),
    ],
    edges=[("n1", "n2"), ("n2", "n3"), ("n3", "n4")],
)


def make_backend(
    position: str = "n1_n2",
    dwell_s: float = 0.0,
) -> MockBackend:
    """
    Build a MockBackend over the line map.
    :param position: Starting TransitGraph node ID.
    :param dwell_s: Seconds held at each stop.
    :return: A ready backend.
    """
    return MockBackend(
        position=position, provider=_PROVIDER, dwell_s=dwell_s
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_starts_at_the_given_position() -> None:
    assert make_backend("n2_n3").current_location() == "n2_n3"


def test_needs_a_provider_or_a_travel_model() -> None:
    with pytest.raises(ValueError):
        MockBackend(position="n1_n2")


def test_accepts_a_travel_model_instead_of_a_provider() -> None:
    model = GraphTravelModel(TransitGraph(_PROVIDER))
    backend = MockBackend(position="n1_n2", travel_model=model)
    backend.set_destination("n2_n3")
    assert backend.advance(_SEGMENT_S).arrived_at == "n2_n3"


def test_a_supplied_model_carries_its_own_dwell() -> None:
    """dwell_s is ignored when a model is given — the model already has one."""
    model = GraphTravelModel(TransitGraph(_PROVIDER), dwell_s=5.0)
    backend = MockBackend(
        position="n1_n2", travel_model=model, dwell_s=999.0
    )
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(
        5.0 + _SEGMENT_S
    )


def test_construction_computes_no_leg() -> None:
    """
    Nothing is known about movement until a destination is commanded — CARLA
    has no vehicle at construction time, so this must not be where legs are
    worked out.
    """
    backend = make_backend()
    result = backend.advance(1000.0)
    assert (result.arrived_at, result.consumed) == (None, 1000.0)


# ---------------------------------------------------------------------------
# Leg duration
# ---------------------------------------------------------------------------

def test_leg_takes_the_shortest_path_time() -> None:
    """
    "n1_n2" to "n3_n4" has no direct TransitGraph edge — Dijkstra must route
    through "n2_n3", so the leg is two segments, not one.
    """
    backend = make_backend()
    backend.set_destination("n3_n4")
    result = backend.advance(1000.0)
    assert result.arrived_at == "n3_n4"
    assert result.consumed == pytest.approx(2 * _SEGMENT_S)


def test_dwell_is_added_to_the_leading_edge_of_the_leg() -> None:
    backend = make_backend(dwell_s=5.0)
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(
        5.0 + _SEGMENT_S
    )


def test_zero_dwell_reproduces_the_original_timing() -> None:
    """The default must leave existing baselines comparable."""
    backend = make_backend(dwell_s=0.0)
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(_SEGMENT_S)


def test_dwell_is_charged_on_every_leg() -> None:
    backend = make_backend(dwell_s=5.0)
    for destination in ("n2_n3", "n3_n4"):
        backend.set_destination(destination)
        assert backend.advance(1000.0).consumed == pytest.approx(
            5.0 + _SEGMENT_S
        )


# ---------------------------------------------------------------------------
# Advancing
# ---------------------------------------------------------------------------

def test_partial_advance_reports_no_arrival() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    result = backend.advance(10.0)
    assert (result.arrived_at, result.consumed) == (None, 10.0)


def test_partial_progress_accumulates_across_calls() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(10.0)
    backend.advance(10.0)
    result = backend.advance(1000.0)
    assert result.arrived_at == "n2_n3"
    assert result.consumed == pytest.approx(_SEGMENT_S - 20.0)


def test_arrival_consumes_only_what_the_leg_needed() -> None:
    """
    The runner stamps arrivals from this, so overshoot must not be reported
    as spent.
    """
    backend = make_backend()
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(_SEGMENT_S)


def test_exact_budget_arrives() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    assert backend.advance(_SEGMENT_S).arrived_at == "n2_n3"


def test_one_second_short_does_not_arrive() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    assert backend.advance(_SEGMENT_S - 1.0).arrived_at is None


def test_advance_without_a_destination_consumes_the_budget() -> None:
    result = make_backend().advance(42.0)
    assert (result.arrived_at, result.consumed) == (None, 42.0)


def test_position_follows_arrivals() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(1000.0)
    assert backend.current_location() == "n2_n3"


def test_position_does_not_move_mid_leg() -> None:
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(10.0)
    assert backend.current_location() == "n1_n2"


def test_next_leg_departs_from_the_stop_just_reached() -> None:
    """A leg is measured from where the bus is, not from where it started."""
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(1000.0)
    backend.set_destination("n3_n4")
    assert backend.advance(1000.0).consumed == pytest.approx(_SEGMENT_S)


# ---------------------------------------------------------------------------
# set_destination is idempotent, but re-arms after an arrival
# ---------------------------------------------------------------------------

def test_recommanding_the_same_stop_keeps_progress() -> None:
    """
    The runner re-issues the destination on every pass of its movement loop,
    so treating that as a new leg would stall the bus forever.
    """
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(20.0)
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(
        _SEGMENT_S - 20.0
    )


def test_changing_destination_discards_progress() -> None:
    backend = make_backend()
    backend.set_destination("n3_n4")
    backend.advance(20.0)
    backend.set_destination("n2_n3")
    assert backend.advance(1000.0).consumed == pytest.approx(_SEGMENT_S)


def test_the_same_stop_after_an_arrival_starts_a_fresh_leg() -> None:
    """
    A policy may legitimately re-issue the stop the bus is standing on. That
    must be a new (zero-length) leg rather than an unchanged destination the
    backend ignores.
    """
    backend = make_backend()
    backend.set_destination("n2_n3")
    backend.advance(1000.0)
    backend.set_destination("n2_n3")
    result = backend.advance(1000.0)
    assert result.arrived_at == "n2_n3"
    assert result.consumed == pytest.approx(0.0)
