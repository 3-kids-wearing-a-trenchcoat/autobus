"""Multi-policy comparison runner."""

from typing import Callable

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.experiment.trial import TrialResult, run_trial
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph


def compare_policies(
    policies: list[tuple[str, Callable[[TransitGraph], DispatchPolicy]]],
    graph: PlanarStreetGraph,
    n_trials: int = 1,
    base_seed: int | None = None,
    **trial_kwargs,
) -> dict[str, list[TrialResult]]:
    """
    Run every policy for n_trials on the same map and return results.

    Each trial index uses the same demand seed across all policies so
    that policies are compared on identical request sequences. Trial i
    is seeded with base_seed + i when base_seed is provided.

    :param policies: List of (label, factory) pairs. Each factory
        accepts a TransitGraph and returns a DispatchPolicy.
    :param graph: Pre-built street graph shared across all runs.
    :param n_trials: Number of independent trials per policy.
    :param base_seed: Base random seed. Trial i uses base_seed + i.
        None = non-deterministic.
    :param trial_kwargs: Additional keyword arguments forwarded to
        run_trial (e.g. max_requests, capacity, dt).
    :return: Dict mapping each policy label to a list of TrialResult,
        one per trial.
    """
    trial_kwargs.setdefault("silent", True)

    results: dict[str, list[TrialResult]] = {
        label: [] for label, _ in policies
    }

    for trial_idx in range(n_trials):
        seed = (
            base_seed + trial_idx
            if base_seed is not None
            else None
        )
        for label, factory in policies:
            print(f"  [{label}] trial {trial_idx + 1}/{n_trials}")
            result = run_trial(
                policy_factory=factory,
                graph=graph,
                seed=seed,
                **trial_kwargs,
            )
            results[label].append(result)

    return results
