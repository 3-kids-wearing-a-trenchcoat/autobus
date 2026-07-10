"""Single-run experiment wrapper."""

import contextlib
import io
import random
from dataclasses import dataclass, field
from typing import Callable

from src.algorithm.BusAgent import BusAgent
from src.algorithm.DispatchPolicy import DispatchPolicy
from src.algorithm.RequestPool import RequestPool
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph
from src.metrics.MetricsTracker import (
    KPISnapshot,
    MetricsTracker,
    TickSnapshot,
)
from src.simulation.AnimationRecorder import AnimationFrame, AnimationRecorder
from src.simulation.DemandGenerator import DemandGenerator, PeriodicPolicy
from src.simulation.MockBackend import MockBackend
from src.simulation.SimulationRunner import SimulationRunner


@dataclass
class TrialResult:
    """
    Results from a single simulation run.

    :param kpi: Aggregate KPIs from the completed run.
    :param tick_count: Total number of per-tick snapshots recorded during
        the run. Always populated, even when tick_history is dropped.
    :param tick_history: Per-tick snapshots recorded during the run. Empty
        unless run_trial was called with keep_tick_history=True.
    :param wait_times: Wait time (s) for each fulfilled request.
    :param ride_times: Ride time (s) for each fulfilled request.
    :param animation_frames: Per-stop-arrival snapshots for animation;
        empty unless run_trial was called with record_animation=True.
    :param transit_graph: The TransitGraph used during the run; None unless
        record_animation=True.
    """

    kpi: KPISnapshot
    tick_count: int
    tick_history: list[TickSnapshot]
    wait_times: list[float]
    ride_times: list[float]
    animation_frames: list[AnimationFrame] = field(default_factory=list)
    transit_graph: TransitGraph | None = field(default=None)


def run_trial(
    policy_factory: Callable[[TransitGraph], DispatchPolicy],
    graph: PlanarStreetGraph,
    seed: int | None = None,
    demand_interval_s: float = 60.0,
    max_requests: int = 100,
    capacity: int = 16,
    n_stops: int = 20,
    commitment: int = 2,
    dt: float = 1.0,
    max_time: float = 36000.0,
    silent: bool = False,
    record_animation: bool = False,
    keep_tick_history: bool = False,
    max_wait_s: float | None = None,
) -> TrialResult:
    """
    Run one simulation trial and return the results.

    Builds the complete simulation stack from the given map and policy,
    runs to completion, and returns aggregated KPIs and per-tick data.

    :param policy_factory: Callable that accepts a TransitGraph and
        returns a DispatchPolicy instance.
    :param graph: Pre-built street graph to simulate on.
    :param seed: Random seed for demand generation.
        None = non-deterministic.
    :param demand_interval_s: Seconds between successive demand
        emissions.
    :param max_requests: Total requests to emit before demand stops.
    :param capacity: Maximum simultaneous passengers on the bus.
    :param n_stops: Number of stops to plan ahead each re-plan cycle.
    :param commitment: Number of leading stops locked in during
        re-planning.
    :param dt: Simulation time step in seconds.
    :param max_time: Safety ceiling in seconds; run ends if sim_time
        reaches this before all requests are served.
    :param silent: If True, suppress the summary printed by
        SimulationRunner.
    :param record_animation: If True, capture per-stop-arrival frames
        and store them in TrialResult.animation_frames. Also exposes
        the TransitGraph via TrialResult.transit_graph. Defaults to
        False to avoid overhead in comparison runs.
    :param keep_tick_history: If True, retain the per-tick snapshots in
        TrialResult.tick_history (needed by plot_occupancy). Defaults to
        False so bulk comparison runs don't accumulate one snapshot per
        tick per trial; TrialResult.tick_count is always populated.
    :param max_wait_s: Maximum time a request may stay PENDING before the
        passenger abandons it (the request is cancelled). None disables
        abandonment, so unserved requests wait indefinitely and the run
        continues until max_time. Defaults to None.
    :return: TrialResult with KPIs, tick history, and timing
        distributions.
    """
    if seed is not None:
        random.seed(seed)

    provider = PlanarMapProvider(graph)
    transit_graph = TransitGraph(provider)
    stops = provider.get_stops()

    pool = RequestPool()
    recorder: AnimationRecorder | None = (
        AnimationRecorder(pool) if record_animation else None
    )
    gen_policy = PeriodicPolicy(
        interval_s=demand_interval_s,
        max_requests=max_requests,
    )
    demand_gen = DemandGenerator(pool, gen_policy, stops)
    demand_gen.update(0.0)

    policy = policy_factory(transit_graph)
    agent = BusAgent(
        pool=pool,
        policy=policy,
        capacity=capacity,
        position=stops[0].id,
        n_stops=n_stops,
        commitment=commitment,
    )

    metrics = MetricsTracker(pool)
    backend = MockBackend(
        agent=agent,
        demand_generator=demand_gen,
        metrics_tracker=metrics,
        pool=pool,
        provider=provider,
        recorder=recorder,
        max_wait_s=max_wait_s,
    )
    runner = SimulationRunner(backend=backend, dt=dt, max_time=max_time)

    if silent:
        with contextlib.redirect_stdout(io.StringIO()):
            kpi = runner.run()
    else:
        kpi = runner.run()

    fulfilled = pool.get_fulfilled()
    wait_times = [r.boarded_at - r.created_at for r in fulfilled]
    ride_times = [r.fulfilled_at - r.boarded_at for r in fulfilled]

    return TrialResult(
        kpi=kpi,
        tick_count=metrics.tick_count,
        tick_history=metrics.tick_history if keep_tick_history else [],
        wait_times=wait_times,
        ride_times=ride_times,
        animation_frames=recorder.frames if recorder is not None else [],
        transit_graph=(
            backend._transit_graph if record_animation else None
        ),
    )
