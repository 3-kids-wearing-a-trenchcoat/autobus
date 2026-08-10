"""
Tests for the results store.

Two things carry the weight here. Identity, because two runs sharing one is
data corruption and two identities for one run is wasted CARLA time; and the
skip-or-overwrite rule, which is the only thing stopping a lucky run being
counted twice into the sample it belongs to.
"""

import json

import pytest

from src.experiment.store import (
    RunIdentity,
    RunRecord,
    is_recorded,
    load_frame,
    load_runs,
    run_id_for,
    save_run,
    to_results,
)
from src.metrics.MetricsTracker import KPISnapshot


def _kpi(mean_wait: float = 30.0) -> KPISnapshot:
    """Build a KPISnapshot with everything but the wait time canned."""
    return KPISnapshot(
        fulfilled_count=10,
        cancelled_count=2,
        mean_wait_time=mean_wait,
        mean_ride_time=20.0,
        mean_journey_time=None,
        max_wait_time=None,
        p90_wait_time=50.0,
        fulfillment_rate=0.8,
        served_fraction=0.9,
        throughput=1.5,
    )


def _identity(policy: str = "Window", seed: int = 0, **trial) -> RunIdentity:
    """Build an identity, varying only what a test cares about."""
    return RunIdentity(
        policy=policy,
        policy_class="WindowInsertionPolicy",
        policy_params={"max_wait_s": 750.0},
        world={"provider": "CarlaMapProvider", "town": "Town01"},
        trial={"max_requests": 40, "dt": 1.0, **trial},
        seed=seed,
    )


def _record(identity: RunIdentity, mean_wait: float = 30.0) -> RunRecord:
    """Build a record around an identity."""
    return RunRecord(
        identity=identity,
        kpi=_kpi(mean_wait),
        tick_count=1640,
        wait_times=[10.0, 20.0],
        ride_times=[5.0, 15.0],
        backend_stats={"distance_m": 6280.0},
        policy_stats={"repair_calls": 12.0},
        provenance={"wall_seconds": 41.2},
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_the_same_identity_hashes_the_same() -> None:
    assert _identity().run_id == _identity().run_id


def test_a_different_seed_is_a_different_run() -> None:
    """Seeds are how a sample is built, so they must not collapse."""
    assert _identity(seed=0).run_id != _identity(seed=1).run_id


def test_a_different_parameter_is_a_different_run() -> None:
    assert _identity().run_id != _identity(max_requests=41).run_id


def test_a_different_policy_class_is_a_different_run() -> None:
    """
    The guard against a subclass that forgot to extend params(): even with
    identical declared parameters it cannot share its parent's identity.
    """
    base = _identity()
    subclass = RunIdentity(
        policy=base.policy,
        policy_class="ReorderingInsertionPolicy",
        policy_params=base.policy_params,
        world=base.world,
        trial=base.trial,
        seed=base.seed,
    )
    assert base.run_id != subclass.run_id


def test_int_and_float_are_the_same_configuration() -> None:
    """
    dt=1 and dt=1.0 are one configuration. Hashing them apart would split a
    sweep into two half-populated copies that never dedupe against each other.
    """
    assert _identity(dt=1).run_id == _identity(dt=1.0).run_id


def test_key_order_does_not_change_the_identity() -> None:
    """A dict written in another order is the same configuration."""
    first = run_id_for({"trial": {"a": 1, "b": 2}, "seed": 0})
    second = run_id_for({"seed": 0, "trial": {"b": 2, "a": 1}})
    assert first == second


def test_booleans_stay_distinct_from_numbers() -> None:
    """bool is a subclass of int; folding True into 1.0 would surprise."""
    assert (run_id_for({"retry": True})
            != run_id_for({"retry": 1.0}))


# ---------------------------------------------------------------------------
# Skip or overwrite — the rule that keeps the sample honest
# ---------------------------------------------------------------------------

def test_a_new_run_is_written(tmp_path) -> None:
    assert save_run(tmp_path, _record(_identity())) is True
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_repeat_is_skipped_by_default(tmp_path) -> None:
    """
    The point of the rule: re-running a seed is not a new observation, and
    appending it would count one lucky run twice.
    """
    save_run(tmp_path, _record(_identity(), mean_wait=30.0))
    written = save_run(tmp_path, _record(_identity(), mean_wait=99.0))

    assert written is False
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert load_runs(tmp_path)[0].kpi.mean_wait_time == 30.0


def test_overwrite_replaces_the_earlier_run(tmp_path) -> None:
    save_run(tmp_path, _record(_identity(), mean_wait=30.0))
    written = save_run(
        tmp_path, _record(_identity(), mean_wait=99.0), overwrite=True
    )

    assert written is True
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert load_runs(tmp_path)[0].kpi.mean_wait_time == 99.0


def test_is_recorded_matches_what_save_did(tmp_path) -> None:
    identity = _identity()
    assert is_recorded(tmp_path, identity) is False
    save_run(tmp_path, _record(identity))
    assert is_recorded(tmp_path, identity) is True


def test_the_file_is_named_for_the_identity(tmp_path) -> None:
    record = _record(_identity())
    save_run(tmp_path, record)
    assert (tmp_path / f"{record.run_id}.json").exists()


def test_a_missing_directory_is_created(tmp_path) -> None:
    nested = tmp_path / "results" / "runs"
    assert save_run(nested, _record(_identity())) is True
    assert nested.is_dir()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_a_record_survives_the_round_trip(tmp_path) -> None:
    original = _record(_identity())
    save_run(tmp_path, original)
    (reloaded,) = load_runs(tmp_path)

    assert reloaded.identity == original.identity
    assert reloaded.kpi == original.kpi
    assert reloaded.wait_times == original.wait_times
    assert reloaded.backend_stats == original.backend_stats
    assert reloaded.policy_stats == original.policy_stats
    assert reloaded.run_id == original.run_id


def test_the_written_file_carries_its_schema(tmp_path) -> None:
    """Old files have to be recognisable once the shape changes."""
    save_run(tmp_path, _record(_identity()))
    data = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert data["schema"] == 1


def test_loading_an_empty_directory_is_not_an_error(tmp_path) -> None:
    assert load_runs(tmp_path) == []
    assert load_frame(tmp_path).empty


def test_where_filters_on_a_dotted_path(tmp_path) -> None:
    save_run(tmp_path, _record(_identity(policy="Window")))
    save_run(tmp_path, _record(_identity(policy="Fixed")))

    kept = load_runs(tmp_path, where={"identity.policy": "Fixed"})
    assert [r.identity.policy for r in kept] == ["Fixed"]


# ---------------------------------------------------------------------------
# Reading it back for graphs and tables
# ---------------------------------------------------------------------------

def test_the_frame_has_a_column_per_nested_key(tmp_path) -> None:
    """Dotted columns are what grouping and plotting want."""
    save_run(tmp_path, _record(_identity()))
    frame = load_frame(tmp_path)

    assert len(frame) == 1
    assert frame["identity.trial.max_requests"].iloc[0] == 40
    assert frame["kpi.mean_wait_time"].iloc[0] == 30.0
    assert frame["identity.seed"].iloc[0] == 0


def test_to_results_groups_by_policy_and_orders_by_seed(tmp_path) -> None:
    for seed in (2, 0, 1):
        save_run(tmp_path, _record(_identity(seed=seed), mean_wait=seed))

    results = to_results(load_runs(tmp_path))
    assert list(results) == ["Window"]
    assert [r.kpi.mean_wait_time for r in results["Window"]] == [0.0, 1.0, 2.0]


def test_to_results_drops_unpaired_seeds_by_default(tmp_path) -> None:
    """
    A campaign interrupted part-way leaves policies with different seed sets.
    Indexing those together compares unrelated runs and yields wrong p-values
    with no error, so the shared seeds are all that survive.
    """
    for seed in (0, 1, 2):
        save_run(tmp_path, _record(_identity(policy="Window", seed=seed)))
    for seed in (0, 1):
        save_run(tmp_path, _record(_identity(policy="Fixed", seed=seed)))

    results = to_results(load_runs(tmp_path))
    assert len(results["Window"]) == 2
    assert len(results["Fixed"]) == 2


def test_unpaired_keeps_every_run(tmp_path) -> None:
    """A sweep is not a paired comparison and should lose nothing."""
    for seed in (0, 1, 2):
        save_run(tmp_path, _record(_identity(policy="Window", seed=seed)))
    for seed in (0, 1):
        save_run(tmp_path, _record(_identity(policy="Fixed", seed=seed)))

    results = to_results(load_runs(tmp_path), paired=False)
    assert len(results["Window"]) == 3
    assert len(results["Fixed"]) == 2


def test_a_rebuilt_trial_result_carries_the_diagnostics(tmp_path) -> None:
    save_run(tmp_path, _record(_identity()))
    (result,) = to_results(load_runs(tmp_path))["Window"]

    assert result.backend_stats == {"distance_m": 6280.0}
    assert result.policy_stats == {"repair_calls": 12.0}
    assert result.tick_history == []


def test_a_written_run_is_readable_by_more_than_its_author(
    tmp_path,
) -> None:
    """
    These files are committed and synchronised. mkstemp defaults to 0600,
    which is right for a secret and a nuisance for a result.
    """
    save_run(tmp_path, _record(_identity()))
    path = next(tmp_path.glob("*.json"))
    assert path.stat().st_mode & 0o077 == 0o044


def test_no_temporary_files_are_left_behind(tmp_path) -> None:
    save_run(tmp_path, _record(_identity()))
    assert list(tmp_path.glob("*.tmp")) == []
