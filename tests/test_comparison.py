"""
Tests for compare_policies: its seed strategy, and what it forwards.

The forwarding half matters more than it looks. ``provider`` and
``backend_factory`` reached run_trial through ``**trial_kwargs`` long before
they were named parameters, so the capability existed but nothing pinned it —
one refactor of the keyword forwarding would have removed it silently. These
tests are what turn an accident into a contract.
"""

import pytest

from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.experiment.comparison import compare_policies
from src.experiment.trial import TrialResult
from src.map.CarlaMapProvider import CarlaMapProvider
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph
from src.metrics.MetricsTracker import KPISnapshot
from src.simulation.ScriptedBackend import ScriptedBackend
from src.simulation.SimulationBackend import SimulationBackend

# Light demand: these tests are about the harness, not routing quality.
_TRIAL = dict(max_requests=6, n_stops=10, silent=True, max_time=20000.0)

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


@pytest.fixture(scope="module")
def street_graph() -> PlanarStreetGraph:
    """A small planar map, built once so the suite stays fast."""
    return PlanarStreetGraph.generate(
        n_seeds=12, bounds=(0.0, 0.0, 1000.0, 1000.0), seed=3
    )


def _policy(graph: TransitGraph) -> WindowInsertionPolicy:
    """Build the L0 policy with a window wide enough to accept most demand."""
    return WindowInsertionPolicy(graph, max_wait_s=900.0)


class _Recorder:
    """Stands in for run_trial, recording how each trial was asked for."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> TrialResult:
        """
        Record the arguments and hand back an empty result.
        :param kwargs: Whatever compare_policies passed along.
        :return: A result with no content; these tests read the calls.
        """
        self.calls.append(kwargs)
        return TrialResult(
            kpi=_EMPTY_KPI,
            tick_count=0,
            tick_history=[],
            wait_times=[],
            ride_times=[],
        )


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace run_trial with a recorder, so forwarding can be inspected."""
    stand_in = _Recorder()
    monkeypatch.setattr(
        "src.experiment.comparison.run_trial", stand_in
    )
    return stand_in


# ---------------------------------------------------------------------------
# Seed strategy — what every paired comparison depends on
# ---------------------------------------------------------------------------

def test_every_policy_sees_the_same_seed_at_a_trial_index(
    recorder: _Recorder,
) -> None:
    """
    Fair comparison means identical demand within a trial index.

    If this ever stopped holding, every paired statistic in the project would
    still compute happily and mean nothing.
    """
    compare_policies(
        [("A", _policy), ("B", _policy)],
        graph=object(),
        n_trials=3,
        base_seed=100,
        progress=False,
    )
    by_trial: dict[int, list[int]] = {}
    for index, call in enumerate(recorder.calls):
        by_trial.setdefault(index // 2, []).append(call["seed"])

    assert by_trial[0] == [100, 100]
    assert by_trial[1] == [101, 101]
    assert by_trial[2] == [102, 102]


def test_results_are_ordered_by_trial_index(recorder: _Recorder) -> None:
    """
    ``results[label][i]`` must be the trial seeded ``base_seed + i``.

    Every paired analysis indexes the two lists together, so a reordering
    would misalign the pairs and produce wrong p-values with no error.
    """
    results = compare_policies(
        [("A", _policy)],
        graph=object(),
        n_trials=4,
        base_seed=7,
        progress=False,
    )
    seeds = [call["seed"] for call in recorder.calls]
    assert seeds == [7, 8, 9, 10]
    assert len(results["A"]) == 4


def test_no_base_seed_leaves_every_trial_unseeded(
    recorder: _Recorder,
) -> None:
    """base_seed=None must not silently become 0."""
    compare_policies(
        [("A", _policy)], graph=object(), n_trials=2, progress=False
    )
    assert [call["seed"] for call in recorder.calls] == [None, None]


# ---------------------------------------------------------------------------
# Forwarding — the capability this file exists to pin
# ---------------------------------------------------------------------------

def test_the_provider_reaches_every_trial(recorder: _Recorder) -> None:
    provider = object()
    compare_policies(
        [("A", _policy)],
        n_trials=2,
        provider=provider,
        progress=False,
    )
    assert all(call["provider"] is provider for call in recorder.calls)


def test_the_backend_factory_reaches_every_trial(
    recorder: _Recorder,
) -> None:
    def factory(graph: TransitGraph, start: str) -> SimulationBackend:
        """A factory that is never called; identity is what is asserted."""
        return ScriptedBackend(start)

    compare_policies(
        [("A", _policy)],
        graph=object(),
        n_trials=2,
        backend_factory=factory,
        progress=False,
    )
    assert all(call["backend_factory"] is factory for call in recorder.calls)


def test_the_graph_still_reaches_every_trial(recorder: _Recorder) -> None:
    """The default path must not regress now that graph is optional."""
    graph = object()
    compare_policies(
        [("A", _policy)], graph=graph, n_trials=2, progress=False
    )
    assert all(call["graph"] is graph for call in recorder.calls)


def test_extra_keywords_still_reach_run_trial(recorder: _Recorder) -> None:
    """Naming two parameters must not break the general forwarding."""
    compare_policies(
        [("A", _policy)],
        graph=object(),
        n_trials=1,
        progress=False,
        max_requests=42,
    )
    assert recorder.calls[0]["max_requests"] == 42


# ---------------------------------------------------------------------------
# End to end — the forwarded arguments really do change the run
# ---------------------------------------------------------------------------

def test_a_comparison_runs_on_a_carla_town() -> None:
    """
    A real CARLA town, several trials, and no server anywhere.

    ``graph`` is not passed at all, which is the part that needed the
    signature change: it used to be a required positional.
    """
    results = compare_policies(
        [("Window", _policy)],
        n_trials=2,
        base_seed=0,
        progress=False,
        provider=CarlaMapProvider("Town01"),
        **_TRIAL,
    )
    assert len(results["Window"]) == 2
    assert all(r.kpi.fulfilled_count > 0 for r in results["Window"])


def test_a_provider_matches_the_graph_it_wraps(
    street_graph: PlanarStreetGraph,
) -> None:
    """Passing the provider compare_policies would have built must match."""
    from_graph = compare_policies(
        [("W", _policy)], street_graph, n_trials=2, base_seed=5,
        progress=False, **_TRIAL,
    )
    from_provider = compare_policies(
        [("W", _policy)], n_trials=2, base_seed=5, progress=False,
        provider=PlanarMapProvider(street_graph), **_TRIAL,
    )
    assert [r.wait_times for r in from_provider["W"]] == [
        r.wait_times for r in from_graph["W"]
    ]


def test_the_backend_factory_changes_the_world(
    street_graph: PlanarStreetGraph,
) -> None:
    """A flat 90 s per leg cannot coincide with shortest-path driving."""
    on_the_map = compare_policies(
        [("W", _policy)], street_graph, n_trials=1, base_seed=5,
        progress=False, **_TRIAL,
    )
    off_the_map = compare_policies(
        [("W", _policy)], street_graph, n_trials=1, base_seed=5,
        progress=False,
        backend_factory=lambda graph, start: ScriptedBackend(
            start, leg_durations=(90.0,)
        ),
        **_TRIAL,
    )
    assert (
        off_the_map["W"][0].ride_times != on_the_map["W"][0].ride_times
    )


def test_neither_graph_nor_provider_is_an_error() -> None:
    """The rule lives in run_trial; compare_policies must not swallow it."""
    with pytest.raises(ValueError, match="graph or a provider"):
        compare_policies([("W", _policy)], n_trials=1, progress=False)
