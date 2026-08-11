"""Single-run experiment wrapper."""

import contextlib
import io
import os
import random
from dataclasses import dataclass, field
from typing import Callable

from src.algorithm.BusAgent import BusAgent
from src.algorithm.DispatchPolicy import DispatchPolicy
from src.algorithm.RequestPool import RequestPool
from src.algorithm.TravelModel import GraphTravelModel, NoisyTravelModel
from src.map.MapProvider import MapProvider
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
from src.simulation.SimulationBackend import SimulationBackend
from src.simulation.SimulationRunner import SimulationRunner

# Mirrors policy_factory: the harness supplies the graph and where the bus
# starts, and the caller decides what world answers for them.
BackendFactory = Callable[[TransitGraph, str], SimulationBackend]


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
    :param backend_stats: Diagnostic counters reported by the backend, empty
        when it publishes none. For CarlaBackend these say what the bus
        actually did — distance driven, re-routes, walker spawn failures —
        which is how a campaign tells a healthy trial from a salvaged one.
    :param policy_stats: Diagnostic counters reported by the policy, empty
        when it publishes none. ReorderingInsertionPolicy uses these to
        report its relocation attempts and acceptances.
    """

    kpi: KPISnapshot
    tick_count: int
    tick_history: list[TickSnapshot]
    wait_times: list[float]
    ride_times: list[float]
    animation_frames: list[AnimationFrame] = field(default_factory=list)
    transit_graph: TransitGraph | None = field(default=None)
    backend_stats: dict[str, float] = field(default_factory=dict)
    policy_stats: dict[str, float] = field(default_factory=dict)


def _stats_of(component: object) -> dict[str, float]:
    """
    Copy a component's diagnostic counters, if it publishes any.

    Duck-typed rather than declared on a base class: most backends and
    policies have nothing to report, and the two that do already report this
    way. Copied rather than returned live, so a caller reading a result
    cannot corrupt the component it came from.
    :param component: A backend or policy that may expose ``stats()``.
    :return: The counters, or an empty dict when the component has none.
    """
    reader = getattr(component, "stats", None)
    if not callable(reader):
        return {}
    return dict(reader())


def _execution_model(
    planning_model: GraphTravelModel,
    travel_noise: float,
    noise_seed: int | None,
    seed: int | None,
) -> GraphTravelModel:
    """
    Return the travel model the simulation executes against.

    Without noise this is the planning model itself rather than a copy of it,
    so estimate and ground truth stay literally the same object and every
    existing result is reproduced bit for bit. With noise the backend gets a
    perturbed model of its own while the policy keeps the clean one, and that
    asymmetry is the whole point: the schedule is still planned against a
    world the bus no longer quite lives in.
    :param planning_model: The model the policy and agent plan against.
    :param travel_noise: Sigma of the lognormal factor; 0.0 means no noise.
    :param noise_seed: Explicit seed for the noise generator, or None to
        reuse the trial seed.
    :param seed: The trial seed, used when noise_seed is None.
    :return: The travel model to hand to the backend.
    """
    if travel_noise == 0.0:
        return planning_model
    return NoisyTravelModel(
        planning_model.graph,
        dwell_s=planning_model.dwell_s,
        sigma=travel_noise,
        seed=seed if noise_seed is None else noise_seed,
    )


# Escape hatch for throwaway exploration. Deliberately an environment
# variable and not an argument: a run that opts out should be awkward to
# produce and impossible to file in the store by accident.
ALLOW_UNPINNED_HASH = "AUTOBUS_ALLOW_UNPINNED_HASH"


def require_pinned_hash_seed(what: str) -> None:
    """
    Refuse work that claims to be seeded but cannot reproduce.

    A seed alone does not make a trial reproducible across processes. Several
    policies break ties with ``min()`` over a ``set[str]``, and set iteration
    order follows Python's per-process string hash randomisation — so
    FixedRoutePolicy builds a literally different cycle from the same graph
    in a different process, which means the baseline comparator drives a
    different route depending on how it was launched. ``run.sh`` and
    ``jupyter-run.sh`` pin ``PYTHONHASHSEED`` for exactly this reason; this
    turns bypassing them from a silent wrong answer into an error.
    :param what: The call being refused, quoted at the head of the message.
    :return: None.
    :raises RuntimeError: If PYTHONHASHSEED is unset or "random", unless
        ALLOW_UNPINNED_HASH is set in the environment.
    """
    pinned = os.environ.get("PYTHONHASHSEED")
    # Any pinned value reproduces; the launchers let you choose your own, and
    # the store records which one a run used in its provenance.
    if pinned is not None and pinned != "random":
        return
    if os.environ.get(ALLOW_UNPINNED_HASH):
        return
    raise RuntimeError(
        f"{what} cannot reproduce: PYTHONHASHSEED is "
        f"{'unset' if pinned is None else 'random'}. Several policies break "
        "ties with min() over a set[str], so a seed alone does not fix "
        "their output across processes.\n"
        "Launch via ./run.sh or ./jupyter-run.sh, or export "
        "PYTHONHASHSEED=0 yourself.\n"
        f"Set {ALLOW_UNPINNED_HASH}=1 to run anyway."
    )


def run_trial(
    policy_factory: Callable[[TransitGraph], DispatchPolicy],
    graph: PlanarStreetGraph | None = None,
    seed: int | None = None,
    demand_interval_s: float = 60.0,
    max_requests: int = 100,
    capacity: int = 16,
    n_stops: int = 20,
    commitment: int = 0,
    dt: float = 1.0,
    max_time: float = 36000.0,
    silent: bool = False,
    record_animation: bool = False,
    keep_tick_history: bool = False,
    max_wait_s: float | None = None,
    dwell_s: float = 0.0,
    travel_noise: float = 0.0,
    noise_seed: int | None = None,
    backend_factory: BackendFactory | None = None,
    provider: MapProvider | None = None,
) -> TrialResult:
    """
    Run one simulation trial and return the results.

    Builds the complete simulation stack from the given map and policy,
    runs to completion, and returns aggregated KPIs and per-tick data.

    :param policy_factory: Callable that accepts a TransitGraph and
        returns a DispatchPolicy instance.
    :param graph: Pre-built planar street graph to simulate on. Ignored when
        ``provider`` is given; required otherwise.
    :param seed: Random seed for demand generation.
        None = non-deterministic.
    :param demand_interval_s: Seconds between successive demand
        emissions.
    :param max_requests: Total requests to emit before demand stops.
    :param capacity: Maximum simultaneous passengers on the bus.
    :param n_stops: Number of stops to plan ahead each re-plan cycle.
    :param commitment: Number of leading stops locked in during
        re-planning. Defaults to 0, which measured best on the campus map
        for every policy tried and harmed none of them — locking a prefix
        makes the policy plan from a projected future while deadlines are
        still measured from now, and past roughly
        ``window / median_leg`` stops it collapses outright. Raise it only
        if re-planning ever moves off stop arrivals: today the bus is
        stationary whenever it plans, so it can freely change its next
        destination. See the Bus Agent concept doc for the measurements.
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
    :param dwell_s: Seconds the bus spends stopped at each stop it visits.
        Defaults to 0.0, which reproduces the original dwell-free timing
        exactly — existing baselines stay comparable.
    :param travel_noise: Spread of the random factor applied to the
        *executed* travel time, as the sigma of a lognormal with median 1.0.
        The policy keeps planning against the unperturbed graph, so this
        opens a gap between what a schedule promises and what the bus
        delivers — the gap CARLA will open for real. Defaults to 0.0, which
        reproduces existing results exactly.
    :param noise_seed: Seed for the travel-noise generator, which is private
        and never touches the global one used for demand. Defaults to None,
        which reuses ``seed`` so a trial stays reproducible as a whole; pass
        a different value to resample execution luck against fixed demand.
    :param backend_factory: Callable that accepts the TransitGraph and the
        bus's starting stop ID and returns the world to simulate in. Defaults
        to None, which builds a MockBackend driving against this trial's own
        travel model. Supply one to run somewhere else — CARLA, or a
        ScriptedBackend with no map at all. ``travel_noise``, ``noise_seed``
        and ``dwell_s`` describe the *default* world only; a caller who
        brings their own backend owns its physics.
    :param provider: Map source to simulate on. Defaults to None, which wraps
        ``graph`` in a PlanarMapProvider — the synthetic map every experiment
        so far has used. Pass a CarlaMapProvider to run the same trial on a
        real CARLA town; nothing above this line knows the difference.
    :return: TrialResult with KPIs, tick history, and timing
        distributions.
    :raises ValueError: If neither ``graph`` nor ``provider`` is given.
    :raises RuntimeError: If a seed is given while PYTHONHASHSEED is not
        pinned, so the run could not be reproduced in another process.
    """
    if seed is not None:
        # Only a seeded trial claims reproducibility, so only a seeded trial
        # is held to it.
        require_pinned_hash_seed(f"run_trial(seed={seed})")
        random.seed(seed)

    if provider is None:
        if graph is None:
            raise ValueError("run_trial needs either a graph or a provider")
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

    # What the policy and the agent believe will happen.
    travel_model = GraphTravelModel(transit_graph, dwell_s=dwell_s)
    # What the simulation actually does. Without noise this is the very same
    # object, so estimate and ground truth coincide and a projected schedule
    # is exactly what gets executed.
    execution_model = _execution_model(
        travel_model, travel_noise, noise_seed, seed
    )

    policy = policy_factory(transit_graph)
    agent = BusAgent(
        pool=pool,
        policy=policy,
        travel_model=travel_model,
        capacity=capacity,
        position=stops[0].id,
        n_stops=n_stops,
        commitment=commitment,
    )

    if backend_factory is None:
        # Closed over execution_model rather than rebuilt from the graph, so
        # that with travel_noise=0.0 the backend drives against the very
        # object the policy plans with.
        def backend_factory(
            _graph: TransitGraph, start_stop_id: str
        ) -> SimulationBackend:
            """Build the default dead-reckoning backend."""
            return MockBackend(
                position=start_stop_id, travel_model=execution_model
            )

    metrics = MetricsTracker(pool)
    backend = backend_factory(transit_graph, stops[0].id)
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

    if silent:
        with contextlib.redirect_stdout(io.StringIO()):
            kpi = runner.run()
    else:
        kpi = runner.run()

    fulfilled = pool.get_fulfilled()
    wait_times = [r.boarded_at - r.created_at for r in fulfilled]
    ride_times = [r.fulfilled_at - r.boarded_at for r in fulfilled]

    # Read after the run, and therefore after the runner's `finally` has
    # already closed the backend. Closing must not reset these counters or a
    # campaign would record zeros for every trial; test_trial pins that.
    return TrialResult(
        kpi=kpi,
        tick_count=metrics.tick_count,
        tick_history=metrics.tick_history if keep_tick_history else [],
        wait_times=wait_times,
        ride_times=ride_times,
        animation_frames=recorder.frames if recorder is not None else [],
        transit_graph=transit_graph if record_animation else None,
        backend_stats=_stats_of(backend),
        policy_stats=_stats_of(policy),
    )
