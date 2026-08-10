"""
Tests for the campaign runner.

The behaviour that matters is what it refuses to do twice. A campaign is a set
of (parameters, seed) runs, and the store already knows which exist; running
one again would count a single run twice into its own sample.
"""

import pytest

from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.experiment.campaign import (
    _effective_trial_params,
    run_campaign,
)
from src.experiment.store import load_runs
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph
from src.simulation.ScriptedBackend import ScriptedBackend

_TRIAL = dict(max_requests=6, n_stops=10, max_time=20000.0)
_WORLD = {"map": "planar", "map_seed": 3, "backend": "MockBackend"}


@pytest.fixture(scope="module")
def street_graph() -> PlanarStreetGraph:
    """A small planar map, built once so the suite stays fast."""
    return PlanarStreetGraph.generate(
        n_seeds=12, bounds=(0.0, 0.0, 1000.0, 1000.0), seed=3
    )


def _policy(graph: TransitGraph) -> WindowInsertionPolicy:
    """Build the L0 policy with a window wide enough to accept most demand."""
    return WindowInsertionPolicy(graph, max_wait_s=900.0)


def _campaign(tmp_path, street_graph, **overrides):
    """Run a small two-seed campaign, letting a test vary one thing."""
    arguments = dict(
        policies=[("Window", _policy)],
        seeds=[0, 1],
        world=_WORLD,
        directory=tmp_path,
        graph=street_graph,
        progress=False,
        **_TRIAL,
    )
    arguments.update(overrides)
    return run_campaign(**arguments)


# ---------------------------------------------------------------------------
# Running, and not running twice
# ---------------------------------------------------------------------------

def test_a_campaign_files_one_run_per_seed(tmp_path, street_graph) -> None:
    result = _campaign(tmp_path, street_graph)

    assert result.ran == 2
    assert result.skipped == 0
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_re_running_a_campaign_runs_nothing(tmp_path, street_graph) -> None:
    """
    The whole point. Everything is already recorded, so a second pass costs
    nothing and adds nothing — which is also what makes a crashed campaign
    cheap to restart.
    """
    _campaign(tmp_path, street_graph)
    second = _campaign(tmp_path, street_graph)

    assert second.ran == 0
    assert second.skipped == 2
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_extending_the_seeds_runs_only_the_new_ones(
    tmp_path, street_graph
) -> None:
    """Seeds rather than a trial count is what makes this work."""
    _campaign(tmp_path, street_graph, seeds=[0, 1])
    extended = _campaign(tmp_path, street_graph, seeds=[0, 1, 2, 3])

    assert extended.ran == 2
    assert extended.skipped == 2
    assert len(extended.records) == 4


def test_overwrite_re_runs_everything(tmp_path, street_graph) -> None:
    _campaign(tmp_path, street_graph)
    again = _campaign(tmp_path, street_graph, overwrite=True)

    assert again.ran == 2
    assert again.skipped == 0
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_a_changed_parameter_is_a_different_campaign(
    tmp_path, street_graph
) -> None:
    """
    Changing the simulation must not silently reuse the old runs — that is
    the mixed-configuration result set the identity exists to prevent.
    """
    _campaign(tmp_path, street_graph)
    changed = _campaign(tmp_path, street_graph, max_requests=8)

    assert changed.ran == 2
    assert len(list(tmp_path.glob("*.json"))) == 4


def test_two_policies_share_each_seed(tmp_path, street_graph) -> None:
    result = _campaign(
        tmp_path, street_graph,
        policies=[("A", _policy), ("B", _policy)],
    )
    seeds = {
        (r.identity.policy, r.identity.seed) for r in result.records
    }
    assert seeds == {("A", 0), ("A", 1), ("B", 0), ("B", 1)}


# ---------------------------------------------------------------------------
# What gets recorded
# ---------------------------------------------------------------------------

def test_the_identity_records_the_policy_and_its_settings(
    tmp_path, street_graph
) -> None:
    _campaign(tmp_path, street_graph)
    (record, _) = load_runs(tmp_path)

    assert record.identity.policy == "Window"
    assert record.identity.policy_class == "WindowInsertionPolicy"
    assert record.identity.policy_params["max_wait_s"] == 900.0
    assert record.identity.world == _WORLD


def test_defaults_are_resolved_into_the_identity() -> None:
    """
    Naming capacity=16 and leaving it to the default are the same
    simulation. Recording only what was passed would file them separately
    and they would never dedupe against each other.
    """
    passed = _effective_trial_params({"max_requests": 6, "capacity": 16})
    omitted = _effective_trial_params({"max_requests": 6})
    assert passed == omitted
    assert omitted["capacity"] == 16


def test_reporting_arguments_stay_out_of_the_identity() -> None:
    """silent and keep_tick_history cannot change a KPI."""
    params = _effective_trial_params({"silent": False})
    assert "silent" not in params
    assert "keep_tick_history" not in params


def test_provenance_is_recorded_but_not_identifying(
    tmp_path, street_graph
) -> None:
    """
    A run is the same run whoever ran it; folding the clock into the
    identity would make every record unique and dedup impossible.
    """
    _campaign(tmp_path, street_graph)
    (record, _) = load_runs(tmp_path)

    assert record.provenance["wall_seconds"] >= 0.0
    assert record.provenance["finished_at"].startswith("20")
    assert "finished_at" not in record.identity.as_dict()


def test_the_trial_diagnostics_reach_the_store(
    tmp_path, street_graph
) -> None:
    """Step 1's counters are what make a stored run auditable."""
    _campaign(tmp_path, street_graph)
    (record, _) = load_runs(tmp_path)
    assert isinstance(record.backend_stats, dict)
    assert isinstance(record.policy_stats, dict)


# ---------------------------------------------------------------------------
# The world it runs in
# ---------------------------------------------------------------------------

def test_a_seeded_backend_factory_is_used(tmp_path, street_graph) -> None:
    """
    The reason this takes a factory-per-seed rather than one factory: a
    CARLA campaign must vary its world across the sample.
    """
    seen: list[int] = []

    def factory_for(seed: int):
        """Record the seed and build a world that ignores the map."""
        seen.append(seed)
        return lambda graph, start: ScriptedBackend(
            start, leg_durations=(90.0,)
        )

    _campaign(tmp_path, street_graph, backend_factory_for=factory_for)
    assert seen == [0, 1]


def test_the_scripted_world_really_was_used(tmp_path, street_graph) -> None:
    """A flat 90 s per leg cannot coincide with shortest-path driving."""
    on_map = _campaign(tmp_path / "a", street_graph)
    off_map = _campaign(
        tmp_path / "b", street_graph,
        backend_factory_for=lambda seed: (
            lambda graph, start: ScriptedBackend(start, leg_durations=(90.0,))
        ),
    )
    assert (off_map.records[0].ride_times
            != on_map.records[0].ride_times)


def test_neither_graph_nor_provider_is_an_error(tmp_path) -> None:
    with pytest.raises(ValueError, match="graph or a provider"):
        run_campaign(
            policies=[("W", _policy)], seeds=[0], world=_WORLD,
            directory=tmp_path, progress=False,
        )


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------

def test_results_feed_the_existing_helpers(tmp_path, street_graph) -> None:
    """
    The bridge that makes the store worth having: stored runs go straight
    into the summary table and the plot helpers.
    """
    from src.experiment.summary import _format_summary_table

    result = _campaign(
        tmp_path, street_graph,
        policies=[("A", _policy), ("B", _policy)],
    )
    table = _format_summary_table(result.results(), 0.95)

    assert "| Property | A | B |" in table
    assert "Mean wait time" in table


# ---------------------------------------------------------------------------
# n_jobs — a scheduling choice, and nothing more
# ---------------------------------------------------------------------------

class _UnsharedBackend(ScriptedBackend):
    """A world backed by something only one process may hold, as CARLA is."""

    parallel_safe = False


def _comparable(record) -> dict:
    """
    Strip a record down to what a run *produced*.

    Provenance is excluded on purpose: wall time and the finishing timestamp
    differ between any two executions, in parallel or not, and neither is a
    result.
    :param record: A RunRecord.
    :return: The parts two runs of the same identity must agree on.
    """
    data = record.as_dict()
    data.pop("provenance")
    return data


def test_parallel_and_sequential_agree_exactly(
    tmp_path, street_graph
) -> None:
    """
    The test that makes n_jobs believable.

    Each trial is a pure function of its identity, and ordering is rebuilt
    from the store by seed rather than from completion order, so running on
    four cores must give the same records as running on one. If this ever
    fails, every parallel result is suspect.
    """
    one = _campaign(
        tmp_path / "seq", street_graph, seeds=[0, 1, 2, 3],
        policies=[("A", _policy), ("B", _policy)],
    )
    many = _campaign(
        tmp_path / "par", street_graph, seeds=[0, 1, 2, 3],
        policies=[("A", _policy), ("B", _policy)], n_jobs=4,
    )

    assert one.ran == many.ran == 8
    assert ([_comparable(r) for r in one.records]
            == [_comparable(r) for r in many.records])


def test_parallel_skips_what_is_already_recorded(
    tmp_path, street_graph
) -> None:
    """Resume has to survive the pool, since that is when it matters most."""
    _campaign(tmp_path, street_graph, seeds=[0, 1], n_jobs=2)
    again = _campaign(tmp_path, street_graph, seeds=[0, 1, 2], n_jobs=2)

    assert again.ran == 1
    assert again.skipped == 2
    assert len(again.records) == 3


def test_a_world_that_cannot_be_shared_refuses_to_parallelise(
    tmp_path, street_graph
) -> None:
    """
    CARLA is one server, so a campaign per core would contend rather than
    run. The refusal is on the backend, checked by building a probe — which
    is free, because a backend's constructor must touch nothing.
    """
    with pytest.raises(ValueError, match="parallel_safe"):
        _campaign(
            tmp_path, street_graph, n_jobs=2,
            backend_factory_for=lambda seed: (
                lambda graph, start: _UnsharedBackend(start)
            ),
        )


def test_an_unsafe_world_still_runs_sequentially(
    tmp_path, street_graph
) -> None:
    """The guard must gate parallelism, not the backend itself."""
    result = _campaign(
        tmp_path, street_graph,
        backend_factory_for=lambda seed: (
            lambda graph, start: _UnsharedBackend(start)
        ),
    )
    assert result.ran == 2


def test_a_carla_backend_declares_itself_unshareable() -> None:
    """The flag has to be on the real class, not only on a test double."""
    from src.simulation.CarlaBackend import CarlaBackend
    from src.simulation.MockBackend import MockBackend

    assert CarlaBackend.parallel_safe is False
    assert MockBackend.parallel_safe is True
    assert ScriptedBackend.parallel_safe is True


def test_a_nonsense_worker_count_is_rejected(tmp_path, street_graph) -> None:
    with pytest.raises(ValueError, match="n_jobs"):
        _campaign(tmp_path, street_graph, n_jobs=0)
