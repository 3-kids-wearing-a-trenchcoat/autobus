"""Tests for CarlaBackend that need no server.

Everything here is construction and contract checking. The driving itself
cannot be unit tested — it needs a live CARLA — so it is exercised by the
walking-skeleton scripts instead, and their measured results are recorded in
`roadmap.md`.
"""

import pytest

from src.map.CarlaMapProvider import CarlaMapProvider
from src.simulation.CarlaBackend import (
    DEFAULT_BUS_ID,
    FULL_SIZE_BUS_ID,
    MAX_SAFE_DELTA_S,
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
