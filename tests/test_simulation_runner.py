"""Tests for SimulationRunner: the tick order, the clock, and mediation."""

import pytest
from unittest.mock import MagicMock

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.metrics.MetricsTracker import KPISnapshot, MetricsTracker
from src.simulation.AnimationRecorder import AnimationRecorder
from src.simulation.DemandGenerator import DemandGenerator
from src.simulation.ScriptedBackend import ScriptedBackend
from src.simulation.SimulationBackend import AdvanceResult
from src.simulation.SimulationRunner import SimulationRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEG = 10.0

# run() formats whatever snapshot() hands back, so a mocked tracker still
# needs a real one to print.
_EMPTY_KPI = KPISnapshot(
    fulfilled_count=0,
    cancelled_count=0,
    mean_wait_time=None,
    mean_ride_time=None,
    mean_journey_time=None,
    max_wait_time=None,
    p90_wait_time=None,
    fulfillment_rate=None,
    served_fraction=None,
    throughput=None,
)


class _RecordingBackend(ScriptedBackend):
    """ScriptedBackend that logs the lifecycle calls made against it."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.events: list[str] = []
        self.synced_pools: list[RequestPool] = []

    def start(self) -> None:
        """Log the call."""
        self.events.append("start")

    def close(self) -> None:
        """Log the call."""
        self.events.append("close")

    def sync_passengers(self, pool: RequestPool) -> None:
        """Log the call and the pool it was handed."""
        self.events.append("sync")
        self.synced_pools.append(pool)


def make_runner(
    next_stop: str | None = "stop_B",
    leg_durations: tuple[float, ...] = (_LEG,),
    max_wait_s: float | None = None,
    recorder: AnimationRecorder | None = None,
    pool: RequestPool | MagicMock | None = None,
    backend: ScriptedBackend | None = None,
    dt: float = 1.0,
    max_time: float = 1000.0,
) -> tuple[SimulationRunner, MagicMock, MagicMock, MagicMock, object]:
    """
    Build a SimulationRunner over a real ScriptedBackend and mocked parts.

    The backend is real rather than mocked so the tests exercise an actual
    implementation of the advance() contract.
    :param next_stop: Stop the mocked agent reports as next.
    :param leg_durations: Timetable handed to the backend.
    :param max_wait_s: Abandonment threshold, or None to disable it.
    :param recorder: Optional animation recorder.
    :param pool: Request pool to use; a permissive mock when None.
    :param backend: Backend to use; a fresh ScriptedBackend when None.
    :param dt: Tick size.
    :param max_time: Safety ceiling.
    :return: (runner, agent, demand_gen, metrics, pool)
    """
    agent = MagicMock(spec=BusAgent)
    agent.next_stop.return_value = next_stop
    agent.position = "stop_A"
    if pool is None:
        pool = MagicMock(spec=RequestPool)
        pool.get_pending.return_value = []
        pool.get_on_bus.return_value = []
    demand_gen = MagicMock(spec=DemandGenerator)
    metrics = MagicMock(spec=MetricsTracker)
    metrics.snapshot.return_value = _EMPTY_KPI
    if backend is None:
        backend = ScriptedBackend("stop_A", leg_durations)
    runner = SimulationRunner(
        backend=backend,
        agent=agent,
        pool=pool,
        demand_generator=demand_gen,
        metrics_tracker=metrics,
        dt=dt,
        max_time=max_time,
        recorder=recorder,
        max_wait_s=max_wait_s,
    )
    return runner, agent, demand_gen, metrics, pool


def _pending(request_id: str, created_at: float) -> Request:
    """Build a PENDING request created at the given time."""
    return Request(
        id=request_id,
        pickup_stop_id="stop_A",
        dropoff_stop_id="stop_B",
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# step — call ordering
# ---------------------------------------------------------------------------

def test_step_calls_demand_before_metrics() -> None:
    runner, agent, demand_gen, metrics, _ = make_runner(next_stop=None)
    manager = MagicMock()
    manager.attach_mock(demand_gen, "demand")
    manager.attach_mock(metrics, "metrics")
    runner.step(5.0)
    call_names = [c[0] for c in manager.mock_calls]
    assert call_names.index("demand.update") < call_names.index(
        "metrics.record_tick"
    )


def test_step_passes_updated_sim_time_to_demand() -> None:
    runner, _, demand_gen, _, _ = make_runner(next_stop=None)
    runner.step(7.0)
    demand_gen.update.assert_called_once_with(pytest.approx(7.0))


def test_step_passes_sim_time_to_metrics() -> None:
    runner, _, _, metrics, _ = make_runner(next_stop=None)
    runner.step(7.0)
    metrics.record_tick.assert_called_once_with(pytest.approx(7.0))


def test_demand_is_generated_before_movement() -> None:
    """
    Passengers created this tick must be boardable at stops reached in it.

    The ordering is load-bearing rather than incidental: reversing it would
    delay every same-tick boarding by one tick.
    """
    runner, agent, demand_gen, _, _ = make_runner()
    order: list[str] = []
    demand_gen.update.side_effect = lambda t: order.append("demand")
    agent.on_arrival.side_effect = lambda s, t: order.append("arrival")
    runner.step(_LEG)
    assert order == ["demand", "arrival"]


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def test_sim_time_starts_at_zero() -> None:
    runner, *_ = make_runner()
    assert runner.sim_time == pytest.approx(0.0)


def test_sim_time_advances_by_dt() -> None:
    runner, *_ = make_runner(next_stop=None)
    runner.step(5.0)
    assert runner.sim_time == pytest.approx(5.0)


def test_sim_time_accumulates_across_steps() -> None:
    runner, *_ = make_runner(next_stop=None)
    runner.step(5.0)
    runner.step(3.0)
    assert runner.sim_time == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Movement — no arrival
# ---------------------------------------------------------------------------

def test_no_arrival_when_partial_progress() -> None:
    runner, agent, *_ = make_runner()
    runner.step(5.0)
    agent.on_arrival.assert_not_called()


def test_partial_progress_accumulates_and_fires() -> None:
    runner, agent, *_ = make_runner()
    runner.step(5.0)
    agent.on_arrival.assert_not_called()
    runner.step(5.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_empty_route_never_commands_the_backend() -> None:
    backend = ScriptedBackend("stop_A")
    runner, agent, *_ = make_runner(next_stop=None, backend=backend)
    runner.step(100.0)
    agent.on_arrival.assert_not_called()
    assert backend.current_location() == "stop_A"


# ---------------------------------------------------------------------------
# Movement — arrivals
# ---------------------------------------------------------------------------

def test_arrival_fires_when_dt_equals_leg() -> None:
    runner, agent, *_ = make_runner()
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(_LEG)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_arrival_fires_at_the_exact_moment_not_the_tick_boundary() -> None:
    """dt=15 over a 10 s leg arrives at t=10, not at the tick boundary t=15."""
    runner, agent, *_ = make_runner()
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(15.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(10.0))


def test_two_arrivals_in_one_step() -> None:
    """Legs of 10 s over a 25 s tick: arrivals at t=10 and t=20."""
    runner, agent, *_ = make_runner()
    agent.next_stop.side_effect = ["stop_B", "stop_C", "stop_D"]
    runner.step(25.0)
    assert agent.on_arrival.call_count == 2
    agent.on_arrival.assert_any_call("stop_B", pytest.approx(10.0))
    agent.on_arrival.assert_any_call("stop_C", pytest.approx(20.0))


def test_second_arrival_uses_the_stop_chosen_after_the_first() -> None:
    """
    The agent re-plans on each arrival, so the leg after one is only known
    once it has been reported. A backend reporting a batch of arrivals would
    be answering for a route the agent had not chosen yet.
    """
    runner, agent, *_ = make_runner()
    chosen: list[str] = ["stop_B"]
    agent.next_stop.side_effect = lambda: chosen[-1]
    agent.on_arrival.side_effect = lambda s, t: chosen.append("stop_Z")
    runner.step(25.0)
    agent.on_arrival.assert_any_call("stop_B", pytest.approx(10.0))
    agent.on_arrival.assert_any_call("stop_Z", pytest.approx(20.0))


def test_route_running_out_mid_tick_stops_movement() -> None:
    runner, agent, *_ = make_runner()
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(100.0)
    assert agent.on_arrival.call_count == 1


# ---------------------------------------------------------------------------
# Animation recorder
# ---------------------------------------------------------------------------

def test_recorder_captures_each_arrival() -> None:
    recorder = MagicMock(spec=AnimationRecorder)
    runner, agent, *_ = make_runner(recorder=recorder)
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(_LEG)
    recorder.record_arrival.assert_called_once_with(
        "stop_B", pytest.approx(10.0)
    )


def test_recorder_runs_after_the_agent_has_boarded() -> None:
    """Frames must capture post-boarding state, so the order is not free."""
    recorder = MagicMock(spec=AnimationRecorder)
    runner, agent, *_ = make_runner(recorder=recorder)
    agent.next_stop.side_effect = ["stop_B", None]
    order: list[str] = []
    agent.on_arrival.side_effect = lambda s, t: order.append("agent")
    recorder.record_arrival.side_effect = lambda s, t: order.append("recorder")
    runner.step(_LEG)
    assert order == ["agent", "recorder"]


def test_no_recorder_is_fine() -> None:
    runner, agent, *_ = make_runner(recorder=None)
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(_LEG)
    agent.on_arrival.assert_called_once()


# ---------------------------------------------------------------------------
# Abandonment
# ---------------------------------------------------------------------------

def test_pending_request_is_cancelled_at_the_threshold() -> None:
    pool = RequestPool()
    pool.add(_pending("r1", created_at=0.0))
    runner, *_ = make_runner(next_stop=None, max_wait_s=100.0, pool=pool)
    runner.step(100.0)
    assert pool.get("r1").state is RequestState.CANCELLED


def test_pending_request_survives_just_under_the_threshold() -> None:
    pool = RequestPool()
    pool.add(_pending("r1", created_at=0.0))
    runner, *_ = make_runner(next_stop=None, max_wait_s=100.0, pool=pool)
    runner.step(99.0)
    assert pool.get("r1").state is RequestState.PENDING


def test_abandonment_measures_from_creation_not_from_zero() -> None:
    pool = RequestPool()
    pool.add(_pending("r1", created_at=50.0))
    runner, *_ = make_runner(
        next_stop=None, max_wait_s=100.0, pool=pool, dt=10.0
    )
    for _ in range(10):
        runner.step(10.0)
    assert pool.get("r1").state is RequestState.PENDING
    runner.step(50.0)
    assert pool.get("r1").state is RequestState.CANCELLED


def test_onboard_requests_are_never_abandoned() -> None:
    """A committed passenger cannot give up; only a waiting one can."""
    pool = RequestPool()
    pool.add(_pending("r1", created_at=0.0))
    pool.update_state("r1", RequestState.ON_BUS)
    runner, *_ = make_runner(next_stop=None, max_wait_s=1.0, pool=pool)
    runner.step(1000.0)
    assert pool.get("r1").state is RequestState.ON_BUS


def test_no_abandonment_when_threshold_is_none() -> None:
    pool = RequestPool()
    pool.add(_pending("r1", created_at=0.0))
    runner, *_ = make_runner(next_stop=None, max_wait_s=None, pool=pool)
    runner.step(100000.0)
    assert pool.get("r1").state is RequestState.PENDING


def test_expiry_runs_before_movement() -> None:
    """
    A passenger who has just run out of patience must not be picked up by a
    bus that reaches their stop later in the same tick.
    """
    runner, agent, _, _, pool = make_runner(max_wait_s=5.0)
    order: list[str] = []
    pool.get_pending.side_effect = lambda: (order.append("expiry-scan") or [])
    agent.on_arrival.side_effect = lambda s, t: order.append("arrival")
    runner.step(_LEG)
    assert order.index("expiry-scan") < order.index("arrival")


# ---------------------------------------------------------------------------
# Lifecycle and passenger sync
# ---------------------------------------------------------------------------

def test_start_is_called_before_the_first_tick() -> None:
    backend = _RecordingBackend("stop_A")
    runner, agent, _, _, pool = make_runner(
        next_stop=None, backend=backend, max_time=5.0
    )
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    runner.run()
    assert backend.events[0] == "start"


def test_close_is_called_after_the_last_tick() -> None:
    backend = _RecordingBackend("stop_A")
    runner, agent, _, _, pool = make_runner(
        next_stop=None, backend=backend, max_time=5.0
    )
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    runner.run()
    assert backend.events[-1] == "close"


def test_close_is_called_even_when_a_tick_raises() -> None:
    """Leaked actors are the classic cross-trial contaminant."""
    backend = _RecordingBackend("stop_A")
    runner, agent, demand_gen, _, pool = make_runner(
        next_stop=None, backend=backend
    )
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    demand_gen.update.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        runner.run()
    assert "close" in backend.events


def test_sync_passengers_is_called_once_per_tick() -> None:
    backend = _RecordingBackend("stop_A")
    runner, *_ = make_runner(next_stop=None, backend=backend)
    runner.step(1.0)
    runner.step(1.0)
    assert backend.events.count("sync") == 2


def test_sync_passengers_receives_the_shared_pool() -> None:
    backend = _RecordingBackend("stop_A")
    pool = RequestPool()
    runner, *_ = make_runner(next_stop=None, backend=backend, pool=pool)
    runner.step(1.0)
    assert backend.synced_pools == [pool]


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------

def test_is_done_true_when_pool_empty() -> None:
    runner, _, _, _, pool = make_runner()
    pool.get_pending.return_value = []
    pool.get_on_bus.return_value = []
    assert runner.is_done() is True


def test_is_done_false_when_pending_requests() -> None:
    runner, _, _, _, pool = make_runner()
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    pool.get_on_bus.return_value = []
    assert runner.is_done() is False


def test_is_done_false_when_on_bus_requests() -> None:
    runner, _, _, _, pool = make_runner()
    pool.get_pending.return_value = []
    pool.get_on_bus.return_value = [MagicMock(spec=Request)]
    assert runner.is_done() is False


def test_is_done_false_when_both_active() -> None:
    runner, _, _, _, pool = make_runner()
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    pool.get_on_bus.return_value = [MagicMock(spec=Request)]
    assert runner.is_done() is False


def test_run_stops_at_max_time() -> None:
    runner, _, _, _, pool = make_runner(
        next_stop=None, dt=10.0, max_time=100.0
    )
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    runner.run()
    assert runner.sim_time == pytest.approx(100.0)


def test_run_returns_the_final_snapshot() -> None:
    runner, _, _, metrics, _ = make_runner(next_stop=None)
    result = runner.run()
    assert result is _EMPTY_KPI


def test_snapshot_is_taken_at_the_final_sim_time() -> None:
    runner, _, _, metrics, pool = make_runner(
        next_stop=None, dt=10.0, max_time=30.0
    )
    pool.get_pending.return_value = [MagicMock(spec=Request)]
    runner.run()
    metrics.snapshot.assert_called_once_with(pytest.approx(30.0))


def test_metrics_property_returns_tracker() -> None:
    runner, _, _, metrics, _ = make_runner()
    assert runner.metrics is metrics


# ---------------------------------------------------------------------------
# The advance() contract, from the runner's side
# ---------------------------------------------------------------------------

def test_runner_credits_the_time_the_backend_reports() -> None:
    """
    The arrival timestamp is derived from the backend's ``consumed``, so a
    backend that under-reports would shift every stamp. Pinned with a
    deliberately odd leg length.
    """
    runner, agent, *_ = make_runner(leg_durations=(7.5,))
    agent.next_stop.side_effect = ["stop_B", None]
    runner.step(20.0)
    agent.on_arrival.assert_called_once_with("stop_B", pytest.approx(7.5))


def test_backend_consuming_the_whole_budget_ends_the_tick() -> None:
    """A backend that never arrives must not spin the movement loop."""

    class _NeverArrives(ScriptedBackend):
        def advance(self, dt: float) -> AdvanceResult:
            return AdvanceResult(None, dt)

    backend = _NeverArrives("stop_A")
    runner, agent, *_ = make_runner(backend=backend)
    runner.step(1000.0)
    agent.on_arrival.assert_not_called()
