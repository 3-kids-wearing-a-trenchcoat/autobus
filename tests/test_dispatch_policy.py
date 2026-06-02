"""Tests for BusState and DispatchPolicy interface."""

import pytest
from unittest.mock import MagicMock

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import Request, RequestState
from src.map.TransitGraph import TransitGraph


class _ConcretePolicy(DispatchPolicy):
    """Minimal concrete subclass for interface testing."""

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """Return an empty route."""
        return []


def make_request(id: str = "r1") -> Request:
    """Helper: build a minimal PENDING request."""
    return Request(id=id, pickup_stop_id="A", dropoff_stop_id="B")


# ---------------------------------------------------------------------------
# BusState
# ---------------------------------------------------------------------------

def test_bus_state_fields_accessible() -> None:
    r = make_request()
    state = BusState(
        position="edge-1",
        onboard=[r],
        pending_requests=[],
        remaining_capacity=3,
    )
    assert state.position == "edge-1"
    assert state.onboard == [r]
    assert state.pending_requests == []
    assert state.remaining_capacity == 3


def test_bus_state_empty_lists() -> None:
    state = BusState(
        position="edge-1",
        onboard=[],
        pending_requests=[],
        remaining_capacity=5,
    )
    assert state.onboard == []
    assert state.pending_requests == []


# ---------------------------------------------------------------------------
# DispatchPolicy
# ---------------------------------------------------------------------------

def test_dispatch_policy_cannot_be_instantiated() -> None:
    graph = MagicMock(spec=TransitGraph)
    with pytest.raises(TypeError):
        DispatchPolicy(graph)  # type: ignore[abstract]


def test_concrete_policy_can_be_instantiated() -> None:
    graph = MagicMock(spec=TransitGraph)
    policy = _ConcretePolicy(graph)
    assert policy._graph is graph


def test_plan_returns_list() -> None:
    graph = MagicMock(spec=TransitGraph)
    policy = _ConcretePolicy(graph)
    state = BusState(
        position="edge-1",
        onboard=[],
        pending_requests=[make_request()],
        remaining_capacity=4,
    )
    result = policy.plan(state, n_stops=3)
    assert isinstance(result, list)
