"""Tests for SimulationBackend abstract base class."""

import pytest
from unittest.mock import MagicMock, call

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import Request, RequestPool
from src.metrics.MetricsTracker import MetricsTracker
from src.simulation.DemandGenerator import DemandGenerator
from src.simulation.SimulationBackend import SimulationBackend


# ---------------------------------------------------------------------------
# Concrete subclass and helpers
# ---------------------------------------------------------------------------

_FIXED_TRAVEL_TIME = 10.0


class _ConcreteBackend(SimulationBackend):
    """Minimal subclass with fixed travel time for testing."""

    def _travel_time(self, from_id: str, to_id: str) -> float:
        """Always return a fixed travel time."""
        return _FIXED_TRAVEL_TIME


def make_backend(
    next_stop: str | None = "stop_B",
    dwell_s: float = 0.0,
) -> tuple[_ConcreteBackend, MagicMock, MagicMock, MagicMock, MagicMock]:
    """
    Build a _ConcreteBackend with all components mocked.
    :param next_stop: Stop the mocked agent reports as next.
    :param dwell_s: Seconds spent stopped at each stop.
    :return: (backend, agent, demand_gen, metrics, pool)
    """
    agent = MagicMock(spec=BusAgent)
    agent.next_stop.return_value = next_stop
    demand_gen = MagicMock(spec=DemandGenerator)
    metrics = MagicMock(spec=MetricsTracker)
    pool = MagicMock(spec=RequestPool)
    pool.get_pending.return_value = []
    pool.get_on_bus.return_value = []
    backend = _ConcreteBackend(
        agent=agent,
        demand_generator=demand_gen,
        metrics_tracker=metrics,
        pool=pool,
        dwell_s=dwell_s,
    )
    return backend, agent, demand_gen, metrics, pool


# ---------------------------------------------------------------------------
# Abstractness
# ---------------------------------------------------------------------------

def test_cannot_instantiate_abstract_class() -> None:
    with pytest.raises(TypeError):
        SimulationBackend(  # type: ignore[abstract]
            agent=MagicMock(spec=BusAgent),
            demand_generator=MagicMock(spec=DemandGenerator),
            metrics_tracker=MagicMock(spec=MetricsTracker),
            pool=MagicMock(spec=RequestPool),
        )


# ---------------------------------------------------------------------------
# step — call ordering
# ---------------------------------------------------------------------------

def test_step_calls_demand_before_metrics() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend(next_stop=None)
    manager = MagicMock()
    manager.attach_mock(demand_gen, "demand")
    manager.attach_mock(metrics, "metrics")
    backend.step(5.0)
    call_names = [c[0] for c in manager.mock_calls]
    assert call_names.index("demand.update") < call_names.index(
        "metrics.record_tick"
    )


def test_step_passes_updated_sim_time_to_demand() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend(next_stop=None)
    backend.step(7.0)
    demand_gen.update.assert_called_once_with(pytest.approx(7.0))


def test_step_passes_sim_time_to_metrics() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend(next_stop=None)
    backend.step(7.0)
    metrics.record_tick.assert_called_once_with(pytest.approx(7.0))


# ---------------------------------------------------------------------------
# sim_time accumulation
# ---------------------------------------------------------------------------

def test_sim_time_starts_at_zero() -> None:
    backend, *_ = make_backend()
    assert backend.sim_time == pytest.approx(0.0)


def test_sim_time_advances_by_dt() -> None:
    backend, *_ = make_backend(next_stop=None)
    backend.step(5.0)
    assert backend.sim_time == pytest.approx(5.0)


def test_sim_time_accumulates_across_steps() -> None:
    backend, *_ = make_backend(next_stop=None)
    backend.step(5.0)
    backend.step(3.0)
    assert backend.sim_time == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# No arrival when dt < travel_time
# ---------------------------------------------------------------------------

def test_no_arrival_when_partial_progress() -> None:
    backend, agent, *_ = make_backend(next_stop="stop_B")
    backend.step(5.0)
    agent.on_arrival.assert_not_called()


def test_partial_progress_accumulates_and_fires() -> None:
    backend, agent, *_ = make_backend(next_stop="stop_B")
    agent.next_stop.side_effect = ["stop_B", "stop_B", None]
    backend.step(5.0)
    agent.on_arrival.assert_not_called()
    backend.step(5.0)
    agent.on_arrival.assert_called_once()


# ---------------------------------------------------------------------------
# Single arrival
# ---------------------------------------------------------------------------

def test_arrival_fires_when_dt_equals_travel_time() -> None:
    backend, agent, *_ = make_backend(next_stop="stop_B")
    agent.next_stop.side_effect = ["stop_B", None]
    backend.step(10.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_arrival_fires_with_exact_arrival_time() -> None:
    # dt=15, travel=10 → arrival at t=10, not at tick boundary t=15.
    # side_effect sequence after __init__: loop-top "stop_B", post-arrival
    # recompute None, second loop-top None (remaining=5 still > 0).
    backend, agent, *_ = make_backend(next_stop="stop_B")
    agent.next_stop.side_effect = ["stop_B", None, None]
    backend.step(15.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_arrival_fires_at_boundary_sim_time() -> None:
    # Arrival exactly at tick boundary: remaining - time_left == 0
    backend, agent, *_ = make_backend(next_stop="stop_B")
    agent.next_stop.side_effect = ["stop_B", None]
    backend.step(10.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


# ---------------------------------------------------------------------------
# Multiple arrivals in one step
# ---------------------------------------------------------------------------

def test_two_arrivals_in_one_step() -> None:
    # travel=10, dt=25 → arrivals at t=10 and t=20; 5s partial toward third
    backend, agent, *_ = make_backend(next_stop="stop_B")

    stops = ["stop_B", "stop_C", None]
    idx = [0]

    def next_stop_fn() -> str | None:
        return stops[min(idx[0], len(stops) - 1)]

    def on_arrival_fn(stop_id: str, sim_time: float) -> None:
        idx[0] += 1

    agent.next_stop.side_effect = next_stop_fn
    agent.on_arrival.side_effect = on_arrival_fn

    backend.step(25.0)

    assert agent.on_arrival.call_count == 2
    agent.on_arrival.assert_any_call("stop_B", pytest.approx(10.0))
    agent.on_arrival.assert_any_call("stop_C", pytest.approx(20.0))


# ---------------------------------------------------------------------------
# Dwell time
# ---------------------------------------------------------------------------

def test_zero_dwell_reproduces_original_timing() -> None:
    """The default must leave existing baselines byte-comparable."""
    backend, agent, *_ = make_backend(next_stop="stop_B", dwell_s=0.0)
    agent.next_stop.side_effect = ["stop_B", None]
    backend.step(10.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_dwell_delays_arrival() -> None:
    # dwell=5 + travel=10 → the bus reaches stop_B at t=15, not t=10.
    backend, agent, *_ = make_backend(next_stop="stop_B", dwell_s=5.0)
    agent.next_stop.side_effect = ["stop_B", None]
    backend.step(15.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(15.0))


def test_no_arrival_before_dwell_has_elapsed() -> None:
    """At t=10 the bus is still pulled over; without dwell it would arrive."""
    backend, agent, *_ = make_backend(next_stop="stop_B", dwell_s=5.0)
    agent.next_stop.side_effect = ["stop_B", None]
    backend.step(10.0)
    agent.on_arrival.assert_not_called()


def test_dwell_charged_on_every_leg() -> None:
    # Two legs of dwell(5) + travel(10) → arrivals at t=15 and t=30.
    backend, agent, *_ = make_backend(next_stop="stop_B", dwell_s=5.0)

    stops = ["stop_B", "stop_C", None]
    idx = [0]

    def next_stop_fn() -> str | None:
        return stops[min(idx[0], len(stops) - 1)]

    def on_arrival_fn(stop_id: str, sim_time: float) -> None:
        idx[0] += 1

    agent.next_stop.side_effect = next_stop_fn
    agent.on_arrival.side_effect = on_arrival_fn

    backend.step(30.0)

    assert agent.on_arrival.call_count == 2
    agent.on_arrival.assert_any_call("stop_B", pytest.approx(15.0))
    agent.on_arrival.assert_any_call("stop_C", pytest.approx(30.0))


# ---------------------------------------------------------------------------
# is_done
# ---------------------------------------------------------------------------

def test_is_done_true_when_pool_empty() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend()
    pool.get_pending.return_value = []
    pool.get_on_bus.return_value = []
    assert backend.is_done() is True


def test_is_done_false_when_pending_requests() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend()
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    pool.get_on_bus.return_value = []
    assert backend.is_done() is False


def test_is_done_false_when_on_bus_requests() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend()
    pool.get_pending.return_value = []
    pool.get_on_bus.return_value = [MagicMock(spec=Request)]
    assert backend.is_done() is False


def test_is_done_false_when_both_active() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend()
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    pool.get_on_bus.return_value = [MagicMock(spec=Request)]
    assert backend.is_done() is False


# ---------------------------------------------------------------------------
# metrics property
# ---------------------------------------------------------------------------

def test_metrics_property_returns_tracker() -> None:
    backend, agent, demand_gen, metrics, pool = make_backend()
    assert backend.metrics is metrics
