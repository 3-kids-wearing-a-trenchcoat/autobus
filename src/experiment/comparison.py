"""Multi-policy comparison runner."""

from typing import Callable

from tqdm.auto import tqdm

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.experiment.trial import BackendFactory, TrialResult, run_trial
from src.map.MapProvider import MapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph


def compare_policies(
    policies: list[tuple[str, Callable[[TransitGraph], DispatchPolicy]]],
    graph: PlanarStreetGraph | None = None,
    n_trials: int = 1,
    base_seed: int | None = None,
    progress: bool = True,
    provider: MapProvider | None = None,
    backend_factory: BackendFactory | None = None,
    **trial_kwargs,
) -> dict[str, list[TrialResult]]:
    """
    Run every policy for n_trials on the same map and return results.

    Each trial index uses the same demand seed across all policies so
    that policies are compared on identical request sequences. Trial i
    is seeded with base_seed + i when base_seed is provided.

    ``provider`` and ``backend_factory`` are named here rather than left to
    ``trial_kwargs`` so that comparing policies on a CARLA town, or in a
    world other than the default mock, is a documented and tested capability
    instead of an accident of keyword forwarding.

    **The same backend factory is used for every trial**, which is right when
    the world is deterministic and wrong when it has luck of its own to
    resample: a CARLA campaign wants each trial's backend seeded differently,
    and builds its own factory per trial rather than sharing one here.

    :param policies: List of (label, factory) pairs. Each factory
        accepts a TransitGraph and returns a DispatchPolicy.
    :param graph: Pre-built street graph shared across all runs. Ignored
        when ``provider`` is given; required otherwise.
    :param n_trials: Number of independent trials per policy.
    :param base_seed: Base random seed. Trial i uses base_seed + i.
        None = non-deterministic.
    :param progress: When True, display one tqdm progress bar per policy
        (filling as trials complete). Set False to run silently.
    :param provider: Map source shared across all runs, used instead of
        ``graph``. Defaults to None, which wraps ``graph`` in a
        PlanarMapProvider.
    :param backend_factory: World builder shared across all runs, called
        once per trial with the TransitGraph and the bus's starting stop.
        Defaults to None, which gives every trial the usual mock backend.
    :param trial_kwargs: Additional keyword arguments forwarded to
        run_trial (e.g. max_requests, capacity, dt).
    :return: Dict mapping each policy label to a list of TrialResult,
        one per trial.
    :raises ValueError: If neither ``graph`` nor ``provider`` is given.
        Raised by run_trial on the first trial.
    """
    trial_kwargs.setdefault("silent", True)

    results: dict[str, list[TrialResult]] = {
        label: [] for label, _ in policies
    }

    # One progress bar per policy, labels padded to align the bars.
    label_width = max(len(label) for label, _ in policies)
    bars = {
        label: tqdm(
            total=n_trials,
            desc=label.ljust(label_width),
            position=index,
            leave=True,
            disable=not progress,
        )
        for index, (label, _) in enumerate(policies)
    }

    for trial_idx in range(n_trials):
        seed = (
            base_seed + trial_idx
            if base_seed is not None
            else None
        )
        for label, factory in policies:
            result = run_trial(
                policy_factory=factory,
                graph=graph,
                seed=seed,
                provider=provider,
                backend_factory=backend_factory,
                **trial_kwargs,
            )
            results[label].append(result)
            bars[label].update(1)

    for bar in bars.values():
        bar.close()

    return results
