"""
Runs trials and files them in the results store.

Deliberately not built on ``compare_policies``. That runs a whole grid and
returns at the end, which cannot express the two things a campaign needs:
asking the store whether a run has already been done *before* paying for it,
and giving each trial a backend of its own so that a CARLA campaign varies
traffic across the sample instead of holding it fixed.

**Seeds rather than a trial count.** A campaign is a set of
``(parameters, seed)`` runs, and the store already knows which of them exist.
Extending a campaign to seeds 30–59 later therefore skips 0–29 and runs only
what is missing, with no bookkeeping of its own.

A trial that raises is allowed to stop the campaign rather than being
swallowed: everything already completed is in the store, so restarting picks
up where it left off, and a campaign that quietly produced three runs out of
ninety would be worse than one that stopped.
"""

import os
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

from tqdm.auto import tqdm

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.experiment.store import (
    DEFAULT_RUNS_DIR,
    RunIdentity,
    RunRecord,
    is_recorded,
    load_runs,
    save_run,
    to_results,
)
from src.experiment.trial import BackendFactory, TrialResult, run_trial
from src.map.MapProvider import MapProvider
from src.map.PlanarMapProvider import PlanarMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph

PolicyFactory = Callable[[TransitGraph], DispatchPolicy]

# Builds the world for one trial, given that trial's seed. The seed is the
# whole reason this exists rather than a plain BackendFactory: CARLA's traffic
# and spawn placement are luck of their own, and holding them fixed across a
# campaign would under-sample the variance the campaign exists to measure.
SeededBackendFactory = Callable[[int], BackendFactory]

# Arguments to run_trial that cannot change a KPI, and so have no business in
# a run's identity: they govern reporting and retention, not the simulation.
_NOT_IN_IDENTITY = frozenset({
    "policy_factory", "graph", "provider", "backend_factory", "seed",
    "silent", "record_animation", "keep_tick_history",
})


@dataclass
class CampaignResult:
    """
    What a campaign found and what it had to run for it.

    :param records: Every run in the store belonging to this campaign,
        whether it was run now or found already recorded.
    :param ran: Runs actually simulated this time.
    :param skipped: Runs already recorded and left alone.
    :param directory: Where the runs are filed.
    """

    records: list[RunRecord] = field(default_factory=list)
    ran: int = 0
    skipped: int = 0
    directory: Path = DEFAULT_RUNS_DIR

    def results(self, paired: bool = True) -> dict[str, list[TrialResult]]:
        """
        Rebuild the mapping the plot helpers and summary table take.
        :param paired: Keep only the seeds every policy has. See
            ``store.to_results`` for why that is the default.
        :return: Policy label to its runs, ordered by seed.
        """
        return to_results(self.records, paired=paired)


def _effective_trial_params(trial_kwargs: Mapping[str, object]) -> dict:
    """
    Resolve the trial parameters against ``run_trial``'s own defaults.

    Recording only what a caller passed would give two identities to one run:
    naming ``capacity=16`` explicitly and leaving it to the default are the
    same simulation, and would otherwise be filed twice and never dedupe.
    Resolving means the identity describes the run rather than the call.
    :param trial_kwargs: What the caller passed through to run_trial.
    :return: Every behaviour-affecting trial parameter and its value.
    """
    defaults = {
        name: parameter.default
        for name, parameter in signature(run_trial).parameters.items()
        if name not in _NOT_IN_IDENTITY
    }
    return {**defaults, **{
        name: value for name, value in trial_kwargs.items()
        if name not in _NOT_IN_IDENTITY
    }}


def _git_commit() -> str | None:
    """
    Read the commit the code was at, for provenance.
    :return: The short hash, or None outside a repository.
    """
    try:
        finished = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return finished.stdout.strip() or None


def _provenance(wall_seconds: float) -> dict[str, object]:
    """
    Describe how and when a run was produced.

    Never part of the identity — a run is the same run whoever ran it, and
    folding a clock in would make every record unique and dedup impossible.
    ``PYTHONHASHSEED`` is here because without it pinned a seeded trial is not
    reproducible across processes at all.
    :param wall_seconds: How long the trial took in real time.
    :return: The provenance block.
    """
    return {
        "finished_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "wall_seconds": round(wall_seconds, 3),
        "git_commit": _git_commit(),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
    }


def _transit_graph(
    graph: PlanarStreetGraph | None, provider: MapProvider | None
) -> TransitGraph:
    """
    Build the graph once, for interrogating policies before they are run.
    :param graph: Planar map, if that is what the campaign runs on.
    :param provider: Map source, used instead of ``graph``.
    :return: The transit graph.
    :raises ValueError: If neither was given.
    """
    if provider is None:
        if graph is None:
            raise ValueError("run_campaign needs either a graph or a provider")
        provider = PlanarMapProvider(graph)
    return TransitGraph(provider)


def run_campaign(
    policies: Sequence[tuple[str, PolicyFactory]],
    seeds: Iterable[int],
    world: Mapping[str, object],
    directory: Path = DEFAULT_RUNS_DIR,
    graph: PlanarStreetGraph | None = None,
    provider: MapProvider | None = None,
    backend_factory_for: SeededBackendFactory | None = None,
    overwrite: bool = False,
    progress: bool = True,
    **trial_kwargs,
) -> CampaignResult:
    """
    Run every policy at every seed that is not already recorded.

    :param policies: (label, factory) pairs. Each factory takes a
        TransitGraph and returns a DispatchPolicy.
    :param seeds: Demand seeds to run. Each ``(parameters, seed)`` pair is one
        run; repeats of a seed already recorded are skipped or overwritten
        rather than added, so no single run is counted twice into the sample.
    :param world: What the bus drove in — town, backend, traffic, and for a
        synthetic map the parameters of the map itself. **Required**, and
        deliberately not guessed: this is the part of a run's identity that
        cannot be read off the arguments, and a thin ``world`` would let two
        genuinely different runs share an identity.
    :param directory: Where runs are filed. Defaults to ``results/runs``.
    :param graph: Planar map to simulate on. Ignored when ``provider`` is
        given; required otherwise.
    :param provider: Map source, used instead of ``graph``.
    :param backend_factory_for: Given a seed, returns the backend factory for
        that trial. Defaults to None, which gives every trial the usual mock
        backend. Supply one to run in CARLA with per-trial variation.
    :param overwrite: Re-run and replace runs already recorded, rather than
        skipping them. Defaults to False.
    :param progress: Show a progress bar over the whole campaign.
    :param trial_kwargs: Forwarded to run_trial (max_requests, capacity, dt,
        max_time, ...). These are resolved against run_trial's own defaults
        before being recorded, so the identity describes the simulation rather
        than the call that launched it.
    :return: A CampaignResult holding every run belonging to this campaign.
    :raises ValueError: If neither ``graph`` nor ``provider`` is given.
    """
    transit_graph = _transit_graph(graph, provider)
    trial_params = _effective_trial_params(trial_kwargs)
    seeds = list(seeds)

    # One throwaway policy per label, purely to ask what it is and how it is
    # configured. The trials build their own: policies carry a schedule, so
    # sharing one across runs would leak state between them.
    probes = {label: factory(transit_graph) for label, factory in policies}

    identities = {
        (label, seed): RunIdentity(
            policy=label,
            policy_class=type(probes[label]).__name__,
            policy_params=probes[label].params(),
            world=dict(world),
            trial=trial_params,
            seed=seed,
        )
        for label, _ in policies
        for seed in seeds
    }

    result = CampaignResult(directory=directory)
    bar = tqdm(
        total=len(identities), disable=not progress, desc="campaign"
    )
    for seed in seeds:
        for label, factory in policies:
            identity = identities[(label, seed)]
            if not overwrite and is_recorded(directory, identity):
                result.skipped += 1
                bar.update(1)
                continue

            started = time.perf_counter()
            trial = run_trial(
                policy_factory=factory,
                graph=graph,
                provider=provider,
                seed=seed,
                backend_factory=(
                    None if backend_factory_for is None
                    else backend_factory_for(seed)
                ),
                silent=True,
                **trial_kwargs,
            )
            save_run(
                directory,
                RunRecord(
                    identity=identity,
                    kpi=trial.kpi,
                    tick_count=trial.tick_count,
                    wait_times=trial.wait_times,
                    ride_times=trial.ride_times,
                    backend_stats=trial.backend_stats,
                    policy_stats=trial.policy_stats,
                    provenance=_provenance(time.perf_counter() - started),
                ),
                overwrite=overwrite,
            )
            result.ran += 1
            bar.update(1)
    bar.close()

    wanted = {identity.run_id for identity in identities.values()}
    result.records = [
        record for record in load_runs(directory)
        if record.run_id in wanted
    ]
    return result
