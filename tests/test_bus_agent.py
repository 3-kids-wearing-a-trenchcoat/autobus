"""Tests for BusAgent's projected planning clock."""

import pytest

from src.algorithm.BusAgent import BusAgent
from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import RequestPool
from src.algorithm.TravelModel import TravelModel

_TRAVEL_S = 10.0


class _FixedTravelModel(TravelModel):
    """Travel model with constant travel and dwell, for exact arithmetic."""

    def __init__(self, travel_s: float = _TRAVEL_S, dwell_s: float = 0.0):
        self._travel_s = travel_s
        self._dwell_s = dwell_s

    def travel_time(self, from_id: str, to_id: str) -> float:
        """Return the constant travel time."""
        return self._travel_s

    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """Return the constant dwell time."""
        return self._dwell_s


class _RecordingPolicy(DispatchPolicy):
    """Returns a canned route and records every BusState it is handed."""

    def __init__(self, route: list[str]) -> None:
        super().__init__(graph=None)  # type: ignore[arg-type]
        self._canned = route
        self.states: list[BusState] = []

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """Record the state and return the canned route."""
        self.states.append(state)
        return list(self._canned)[:n_stops]


def make_agent(
    commitment: int = 0,
    dwell_s: float = 0.0,
    route: list[str] | None = None,
) -> tuple[BusAgent, _RecordingPolicy]:
    """
    Build a BusAgent over an empty pool with a recording policy.
    :param commitment: Leading stops locked during re-planning.
    :param dwell_s: Constant dwell time per stop visit.
    :param route: Route the policy returns on every call.
    :return: (agent, policy)
    """
    policy = _RecordingPolicy(route if route is not None else ["A", "B", "C"])
    agent = BusAgent(
        pool=RequestPool(),
        policy=policy,
        travel_model=_FixedTravelModel(dwell_s=dwell_s),
        capacity=4,
        position="start",
        n_stops=5,
        commitment=commitment,
    )
    return agent, policy


# ---------------------------------------------------------------------------
# Initial plan
# ---------------------------------------------------------------------------

def test_initial_plan_happens_at_time_zero() -> None:
    _, policy = make_agent()
    assert policy.states[0].now == pytest.approx(0.0)


def test_initial_plan_includes_dwell_at_start() -> None:
    """`now` is a departure time, so the start dwell is already spent."""
    _, policy = make_agent(dwell_s=5.0)
    assert policy.states[0].now == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Anchoring on the real arrival time
# ---------------------------------------------------------------------------

def test_now_anchors_on_arrival_time_without_commitment() -> None:
    agent, policy = make_agent(commitment=0)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].now == pytest.approx(100.0)


def test_now_adds_dwell_to_arrival_time() -> None:
    agent, policy = make_agent(commitment=0, dwell_s=5.0)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].now == pytest.approx(105.0)


def test_position_is_arrival_stop_without_commitment() -> None:
    agent, policy = make_agent(commitment=0)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].position == "A"


# ---------------------------------------------------------------------------
# Projection across the committed prefix
# ---------------------------------------------------------------------------

def test_now_projects_across_committed_prefix() -> None:
    # After arriving at A at t=100 the route tail is [B, C], both committed.
    # Legs: dwell(0) + travel(10) to B, then dwell(0) + travel(10) to C.
    agent, policy = make_agent(commitment=2)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].now == pytest.approx(120.0)


def test_projected_position_is_last_committed_stop() -> None:
    agent, policy = make_agent(commitment=2)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].position == "C"


def test_projection_charges_dwell_at_every_committed_stop() -> None:
    # dwell(5) at A, drive 10 to B, dwell(5) at B, drive 10 to C, dwell(5) at
    # C. This must match the backend, which charges dwell + travel per leg:
    # arrive B at 115, arrive C at 130, depart C at 135.
    agent, policy = make_agent(commitment=2, dwell_s=5.0)
    agent.on_arrival("A", 100.0)
    assert policy.states[-1].now == pytest.approx(135.0)


def test_longer_commitment_projects_further() -> None:
    agent, policy = make_agent(commitment=1)
    agent.on_arrival("A", 100.0)
    # Only B is committed: depart A, drive to B, depart B.
    assert policy.states[-1].now == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# Default keeps time-blind policies working
# ---------------------------------------------------------------------------

def test_bus_state_now_defaults_to_zero() -> None:
    """Policies that ignore time must still be constructible by hand."""
    state = BusState(
        position="A", onboard=[], pending_requests=[], remaining_capacity=4
    )
    assert state.now == pytest.approx(0.0)
