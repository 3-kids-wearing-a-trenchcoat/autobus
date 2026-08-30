"""
A store of completed runs, keyed by what produced them.

One **write-once** JSON file per run, named for the hash of its identity.
Nothing is ever appended to or rewritten, which is what makes the store safe
here: this directory is synchronised live, and a conflict on a file that is
only ever written once costs a single run and leaves a visible
``.sync-conflict-*`` beside it. A shared file — one growing log, or a database
— would put every run behind one writer. It is also the only shape that stays
correct if trials are ever run in parallel, since separate processes write
separate files and need no locking.

**A run is one (parameters, seed) pair.** Repeating a seed against the same
parameters is not a new observation, so re-running one is skipped or
overwritten, never appended: nothing else stops a single lucky run being
counted twice and biasing the sample it belongs to.

Retrieval goes two ways. ``load_frame`` flattens everything into a DataFrame
for grouping and plotting; ``to_results`` rebuilds the
``dict[str, list[TrialResult]]`` that every existing plot helper and the
comparison table already take, so stored runs feed them unchanged.
"""

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from src.experiment.trial import TrialResult
from src.metrics.MetricsTracker import KPISnapshot

# Where runs live unless a caller says otherwise. These are the measured
# evidence behind the project's success criteria, and hours of CARLA to
# regenerate.
#
# Anchored to this file rather than to the working directory, because
# JupyterLab starts a kernel in the *notebook's* directory: a relative path
# would quietly file half the campaign under `notebooks/results/runs` and the
# other half under the real one, and the two would never dedupe against each
# other. `src/map/carla_stops.py` anchors itself for the same reason.
DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "results" / "runs"

# 64 bits of identity. Thousands of runs put the collision probability many
# orders of magnitude below the chance of losing a file.
_ID_LENGTH = 16

# Decimal places kept when hashing a number. Enough to separate any parameter
# anyone would set by hand, coarse enough that arithmetic noise cannot split
# one configuration into two.
_HASH_PRECISION = 9

# Results are shared, committed and synchronised, so they are readable by
# more than their author.
_FILE_MODE = 0o644

# Decimal places kept for the per-passenger arrays. These dominate a record:
# 200 requests is 400 floats, and a full-precision float serialises to about
# 18 characters, so they are roughly three quarters of the file. Two decimals
# is a hundredth of a second on quantities the simulation reports in whole
# seconds, and cuts the store by about 3x — which is the difference between a
# multi-map sweep being a few tens of MB and being a few hundred.
#
# Applied when writing, never when hashing: the identity does not contain
# these, so rounding cannot merge two runs.
_SAMPLE_PRECISION = 2

SCHEMA = 1


def _normalise(value: object) -> object:
    """
    Reduce a value to the form the identity hash is taken over.

    Numbers are the whole reason this exists: ``dt=1`` and ``dt=1.0`` are the
    same configuration, and hashing them apart would split one sweep into two
    half-populated copies that never dedupe against each other. Booleans are
    left alone — ``bool`` is a subclass of ``int``, and folding ``True`` into
    ``1.0`` would be a surprise for no gain.

    Applied only when hashing. The record keeps the values as they were given,
    so a stored run still reads the way it was written.
    :param value: Any JSON-serialisable value from an identity.
    :return: The value in canonical form.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), _HASH_PRECISION)
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class RunIdentity:
    """
    Everything that determines a run's outcome.

    :param policy: The label this policy runs under, as shown in tables and
        legends.
    :param policy_class: Class name of the policy. Carried separately from
        the label so that a subclass which forgot to extend ``params()``
        still cannot share an identity with its parent.
    :param policy_params: What the policy declared via ``params()``.
    :param world: The map and the world the bus drove in — provider, town,
        backend, and anything else that changes the driving.
    :param trial: The simulation parameters handed to ``run_trial``.
    :param seed: The demand seed. Part of the identity: repeated seeds are
        how a sample is built, and a repeat of one already recorded is a
        duplicate rather than an observation.
    """

    policy: str
    policy_class: str
    policy_params: dict[str, object]
    world: dict[str, object]
    trial: dict[str, object]
    seed: int | None

    def as_dict(self) -> dict[str, object]:
        """
        Render the identity as plain data.
        :return: The identity, ready for JSON.
        """
        return {
            "policy": self.policy,
            "policy_class": self.policy_class,
            "policy_params": dict(self.policy_params),
            "world": dict(self.world),
            "trial": dict(self.trial),
            "seed": self.seed,
        }

    @property
    def run_id(self) -> str:
        """The identity hash, which is also the file name."""
        return run_id_for(self.as_dict())


def run_id_for(identity: Mapping[str, object]) -> str:
    """
    Hash an identity into the name its run is filed under.
    :param identity: The identity, as plain data.
    :return: A 16-character hex digest.
    """
    canonical = json.dumps(
        _normalise(identity), sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:_ID_LENGTH]


@dataclass
class RunRecord:
    """
    One completed run: what produced it, and what it produced.

    :param identity: What determined this run.
    :param kpi: The KPIs it ended with.
    :param tick_count: Number of per-tick snapshots recorded.
    :param wait_times: Wait time (s) per fulfilled request.
    :param ride_times: Ride time (s) per fulfilled request.
    :param backend_stats: What the world reported about the driving.
    :param policy_stats: What the policy reported about its own work.
    :param provenance: How and when it was run — wall time, timestamp, git
        commit, ``PYTHONHASHSEED``. Never part of the identity: a run is the
        same run whoever ran it, and folding the clock in would make every
        record unique and dedup impossible.
    """

    identity: RunIdentity
    kpi: KPISnapshot
    tick_count: int
    wait_times: list[float]
    ride_times: list[float]
    backend_stats: dict[str, float] = field(default_factory=dict)
    policy_stats: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, object] = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        """The identity hash this run is filed under."""
        return self.identity.run_id

    def as_dict(self) -> dict[str, object]:
        """
        Render the whole record as plain data.
        :return: The record, ready for JSON.
        """
        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "identity": self.identity.as_dict(),
            "kpi": asdict(self.kpi),
            "tick_count": self.tick_count,
            "wait_times": [round(v, _SAMPLE_PRECISION) for v in
                           self.wait_times],
            "ride_times": [round(v, _SAMPLE_PRECISION) for v in
                           self.ride_times],
            "backend_stats": dict(self.backend_stats),
            "policy_stats": dict(self.policy_stats),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "RunRecord":
        """
        Rebuild a record from what was written.
        :param data: One decoded run file.
        :return: The record.
        """
        identity = data["identity"]
        return cls(
            identity=RunIdentity(
                policy=identity["policy"],
                policy_class=identity["policy_class"],
                policy_params=identity["policy_params"],
                world=identity["world"],
                trial=identity["trial"],
                seed=identity["seed"],
            ),
            kpi=KPISnapshot(**data["kpi"]),
            tick_count=data["tick_count"],
            wait_times=list(data["wait_times"]),
            ride_times=list(data["ride_times"]),
            backend_stats=dict(data.get("backend_stats", {})),
            policy_stats=dict(data.get("policy_stats", {})),
            provenance=dict(data.get("provenance", {})),
        )

    def as_trial_result(self) -> TrialResult:
        """
        Rebuild the shape the rest of the harness already understands.

        Per-tick history, animation frames and the graph are not stored — the
        first is large and the other two are only meaningful while the objects
        that produced them are alive.
        :return: A TrialResult carrying everything that was persisted.
        """
        return TrialResult(
            kpi=self.kpi,
            tick_count=self.tick_count,
            tick_history=[],
            wait_times=list(self.wait_times),
            ride_times=list(self.ride_times),
            backend_stats=dict(self.backend_stats),
            policy_stats=dict(self.policy_stats),
        )


def save_run(
    directory: Path,
    record: RunRecord,
    overwrite: bool = False,
) -> bool:
    """
    File a completed run, unless it is already there.

    This is the whole skip-or-overwrite rule, and it is a filesystem check
    rather than a query: the file name *is* the identity.

    Written to a temporary file and moved into place, so a run is either
    absent or complete and never half-parsed by a later reader.
    :param directory: Where runs are filed.
    :param record: The run to file.
    :param overwrite: Replace a run already recorded under this identity.
        Defaults to False, which leaves the existing one alone.
    :return: True if it was written, False if an identical run was already
        recorded and left in place.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.run_id}.json"
    if path.exists() and not overwrite:
        return False

    handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(record.as_dict(), stream, sort_keys=True, indent=1)
        # mkstemp creates at 0600, which is right for a secret and wrong for
        # a result: these are committed and synchronised, and a file only its
        # author can read is a nuisance on the next machine.
        os.chmod(temporary, _FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def is_recorded(directory: Path, identity: RunIdentity) -> bool:
    """
    Ask whether a run has already been done.
    :param directory: Where runs are filed.
    :param identity: The run in question.
    :return: True if a file for this identity exists.
    """
    return (directory / f"{identity.run_id}.json").exists()


def _dotted(data: Mapping[str, object], key: str) -> object:
    """
    Read a nested value by dotted path.
    :param data: The record as plain data.
    :param key: A path such as ``identity.trial.max_requests``.
    :return: The value, or None if any step is missing.
    """
    current: object = data
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def load_runs(
    directory: Path,
    where: Mapping[str, object] | None = None,
) -> list[RunRecord]:
    """
    Read every run filed, optionally keeping only some.
    :param directory: Where runs are filed.
    :param where: Dotted path to required value, e.g.
        ``{"identity.policy": "Window"}``. Defaults to None, which keeps all.
    :return: The matching records, ordered by policy then seed so that a
        caller indexing two policies together lines them up.
    """
    if not directory.is_dir():
        return []

    records: list[RunRecord] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if where and any(
            _dotted(data, key) != value for key, value in where.items()
        ):
            continue
        records.append(RunRecord.from_dict(data))

    return sorted(
        records,
        key=lambda record: (
            record.identity.policy,
            record.identity.seed is None,
            record.identity.seed or 0,
        ),
    )


def load_frame(directory: Path) -> pd.DataFrame:
    """
    Read every run into a table, one row per run.

    Nested keys become dotted columns — ``identity.trial.max_requests``,
    ``kpi.mean_wait_time`` — which is the shape both grouping and plotting
    want. Filter on the parameters being held fixed, group by the one being
    varied.
    :param directory: Where runs are filed.
    :return: One row per run; empty with no columns if nothing is filed.
    """
    records = [record.as_dict() for record in load_runs(directory)]
    if not records:
        return pd.DataFrame()
    return pd.json_normalize(records)


def to_results(
    records: Sequence[RunRecord],
    paired: bool = True,
) -> dict[str, list[TrialResult]]:
    """
    Rebuild the mapping every plot helper and the summary table already take.

    **``paired`` defaults to True**, and the reason is the failure it avoids.
    Every paired statistic in this project indexes two policies' lists
    together and assumes position *i* is the same seed in both. A campaign
    interrupted part-way leaves policies with different seed sets, and
    aligning those by position silently compares unrelated runs — wrong
    p-values, no error. Keeping only the seeds every policy has can lose
    runs, but losing runs is visible in the sample count and being wrong is
    not.

    Pass False when the comparison is not paired — a parameter sweep, or a
    single policy — and every recorded run should count.
    :param records: Runs to rebuild, typically from ``load_runs``.
    :param paired: Keep only the seeds present for every policy.
    :return: Policy label to its runs, ordered by seed.
    """
    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        grouped[record.identity.policy].append(record)

    if paired and len(grouped) > 1:
        shared = set.intersection(*(
            {record.identity.seed for record in runs}
            for runs in grouped.values()
        ))
        grouped = {
            label: [r for r in runs if r.identity.seed in shared]
            for label, runs in grouped.items()
        }

    return {
        label: [
            record.as_trial_result()
            for record in sorted(
                runs,
                key=lambda r: (r.identity.seed is None, r.identity.seed or 0),
            )
        ]
        for label, runs in grouped.items()
    }
