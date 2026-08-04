"""Tests for run_trial's wiring: execution noise, and the backend seam.

Two claims that only assembling the stack can establish. First, that the
policy plans against the clean graph while the backend drives a perturbed
one — a unit test of NoisyTravelModel cannot show that. Second, that the
harness does not care which world it is given.
"""

import pytest

from src.algorithm.TravelModel import GraphTravelModel, NoisyTravelModel
from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.experiment.trial import _execution_model, run_trial
from src.map.CarlaMapProvider import CarlaMapProvider
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph
from src.simulation.MockBackend import MockBackend
from src.simulation.ScriptedBackend import ScriptedBackend
from src.simulation.SimulationBackend import SimulationBackend

# Small map and light demand: this file is about wiring, not about routing
# quality, so the runs only need to be long enough to differ.
_TRIAL = dict(
    seed=7, max_requests=12, n_stops=10, silent=True, max_time=20000.0
)


@pytest.fixture(scope="module")
def street_graph() -> PlanarStreetGraph:
    """A small planar map, built once so the suite stays fast."""
    return PlanarStreetGraph.generate(
        n_seeds=12, bounds=(0.0, 0.0, 1000.0, 1000.0), seed=3
    )


@pytest.fixture
def transit_graph(street_graph: PlanarStreetGraph) -> TransitGraph:
    """The TransitGraph built from that map, as run_trial builds it."""
    return TransitGraph(PlanarMapProvider(street_graph))


def _policy(graph: TransitGraph) -> WindowInsertionPolicy:
    """Build the L0 policy with a window wide enough to accept most demand."""
    return WindowInsertionPolicy(graph, max_wait_s=900.0)


# ---------------------------------------------------------------------------
# _execution_model
# ---------------------------------------------------------------------------

def test_no_noise_shares_the_planning_model(
    transit_graph: TransitGraph,
) -> None:
    """
    Identity, not merely equality.

    Sharing the object is what guarantees estimate and ground truth cannot
    drift apart by accident while noise is off.
    """
    planning = GraphTravelModel(transit_graph)
    assert _execution_model(planning, 0.0, None, 1) is planning


def test_noise_produces_a_separate_perturbed_model(
    transit_graph: TransitGraph,
) -> None:
    planning = GraphTravelModel(transit_graph)
    execution = _execution_model(planning, 0.2, None, 1)
    assert execution is not planning
    assert isinstance(execution, NoisyTravelModel)
    assert execution.sigma == pytest.approx(0.2)


def test_noise_seed_falls_back_to_the_trial_seed(
    transit_graph: TransitGraph,
) -> None:
    """Leaving noise_seed unset must still give a reproducible trial."""
    planning = GraphTravelModel(transit_graph)
    stops = transit_graph.get_stops()
    origin, destination = stops[0].id, stops[1].id

    from_trial = _execution_model(planning, 0.2, None, 99)
    explicit = _execution_model(planning, 0.2, 99, None)
    assert [from_trial.travel_time(origin, destination) for _ in range(5)] == [
        explicit.travel_time(origin, destination) for _ in range(5)
    ]


def test_noise_carries_dwell_across(transit_graph: TransitGraph) -> None:
    planning = GraphTravelModel(transit_graph, dwell_s=15.0)
    assert _execution_model(planning, 0.2, None, 1).dwell_s == 15.0


# ---------------------------------------------------------------------------
# run_trial
# ---------------------------------------------------------------------------

def test_zero_noise_reproduces_the_noiseless_run(
    street_graph: PlanarStreetGraph,
) -> None:
    """The default must be bit-identical to the behaviour it replaced."""
    without = run_trial(_policy, street_graph, **_TRIAL)
    with_zero = run_trial(_policy, street_graph, travel_noise=0.0, **_TRIAL)
    assert with_zero.wait_times == without.wait_times
    assert with_zero.ride_times == without.ride_times


def test_noise_changes_the_outcome(street_graph: PlanarStreetGraph) -> None:
    clean = run_trial(_policy, street_graph, **_TRIAL)
    noisy = run_trial(_policy, street_graph, travel_noise=0.3, **_TRIAL)
    assert noisy.ride_times != clean.ride_times


def test_noisy_runs_are_reproducible(
    street_graph: PlanarStreetGraph,
) -> None:
    """Noise must not cost reproducibility; the generator is seeded."""
    first = run_trial(_policy, street_graph, travel_noise=0.3, **_TRIAL)
    second = run_trial(_policy, street_graph, travel_noise=0.3, **_TRIAL)
    assert first.wait_times == second.wait_times
    assert first.ride_times == second.ride_times


def test_noise_seed_resamples_execution_luck(
    street_graph: PlanarStreetGraph,
) -> None:
    """
    Changing only noise_seed must change how the run executes.

    The trial seed is untouched, so demand is identical and the difference is
    execution luck alone — which is what lets a paired comparison hold demand
    fixed while varying the driving. That the two generators really are
    independent is established by the isolation test in test_travel_model.
    """
    first = run_trial(
        _policy, street_graph, travel_noise=0.3, noise_seed=1, **_TRIAL
    )
    second = run_trial(
        _policy, street_graph, travel_noise=0.3, noise_seed=2, **_TRIAL
    )
    assert first.ride_times != second.ride_times


# ---------------------------------------------------------------------------
# backend_factory — the harness does not care where the world comes from
# ---------------------------------------------------------------------------

def test_default_factory_reproduces_the_hardwired_backend(
    street_graph: PlanarStreetGraph,
) -> None:
    """
    Passing an explicit mock factory must match the default exactly.

    The default closes over the trial's own travel model, so this also pins
    that the two build the same world rather than merely similar ones.
    """
    default = run_trial(_policy, street_graph, **_TRIAL)
    explicit = run_trial(
        _policy,
        street_graph,
        backend_factory=lambda graph, start: MockBackend(
            position=start, travel_model=GraphTravelModel(graph)
        ),
        **_TRIAL,
    )
    assert explicit.wait_times == default.wait_times
    assert explicit.ride_times == default.ride_times


def test_a_trial_runs_against_a_backend_with_no_map(
    street_graph: PlanarStreetGraph,
) -> None:
    """
    The modularity claim, stated as a test.

    ScriptedBackend has no graph, no travel model and no geometry — every leg
    takes ninety seconds regardless of where the bus is going. If a full
    trial still completes and serves passengers, then nothing above the
    backend depends on where the world comes from, which is the whole point
    of the seam.
    """
    result = run_trial(
        _policy,
        street_graph,
        backend_factory=lambda graph, start: ScriptedBackend(
            start, leg_durations=(90.0,)
        ),
        **_TRIAL,
    )
    assert result.kpi.fulfilled_count > 0
    assert len(result.wait_times) == result.kpi.fulfilled_count


def test_the_scripted_world_really_is_a_different_world(
    street_graph: PlanarStreetGraph,
) -> None:
    """A flat 90 s per leg cannot coincide with shortest-path driving."""
    on_the_map = run_trial(_policy, street_graph, **_TRIAL)
    off_the_map = run_trial(
        _policy,
        street_graph,
        backend_factory=lambda graph, start: ScriptedBackend(
            start, leg_durations=(90.0,)
        ),
        **_TRIAL,
    )
    assert off_the_map.ride_times != on_the_map.ride_times


def test_the_factory_is_given_the_graph_and_the_starting_stop(
    street_graph: PlanarStreetGraph,
) -> None:
    seen: list[tuple[TransitGraph, str]] = []

    def factory(graph: TransitGraph, start: str) -> SimulationBackend:
        """Record the arguments, then build an ordinary mock."""
        seen.append((graph, start))
        return MockBackend(
            position=start, travel_model=GraphTravelModel(graph)
        )

    run_trial(_policy, street_graph, backend_factory=factory, **_TRIAL)

    graph, start = seen[0]
    assert len(seen) == 1
    assert start in {stop.id for stop in graph.get_stops()}


# ---------------------------------------------------------------------------
# provider — the harness does not care which map it is given
# ---------------------------------------------------------------------------

def test_a_provider_replaces_the_planar_graph(
    street_graph: PlanarStreetGraph,
) -> None:
    """Passing the provider run_trial would have built itself must match."""
    from_graph = run_trial(_policy, street_graph, **_TRIAL)
    from_provider = run_trial(
        _policy, provider=PlanarMapProvider(street_graph), **_TRIAL
    )
    assert from_provider.wait_times == from_graph.wait_times
    assert from_provider.ride_times == from_graph.ride_times


def test_a_trial_runs_on_a_carla_town(
    street_graph: PlanarStreetGraph,
) -> None:
    """
    The payoff of the whole map layer: a real CARLA town, routed with the
    synthetic backend and no server anywhere. If this passes but step 4
    fails, the problem is the driving rather than the map.
    """
    result = run_trial(
        _policy, provider=CarlaMapProvider("Town01"),
        seed=7, max_requests=8, n_stops=10, silent=True, max_time=20000.0,
    )
    assert result.kpi.fulfilled_count > 0
    assert len(result.wait_times) == result.kpi.fulfilled_count


def test_neither_graph_nor_provider_is_an_error() -> None:
    with pytest.raises(ValueError, match="graph or a provider"):
        run_trial(_policy, silent=True)
