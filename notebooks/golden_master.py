"""
Golden-master capture for the Phase 2 backend refactor.

Records the exact observable behaviour of the current simulation stack so
that moving orchestration out of ``SimulationBackend`` can be proven to
change nothing. The refactor shuffles arithmetic between classes, and its
characteristic failure is a silent one-tick shift that no unit test would
notice — so the bar here is **bit-identical output**, not "close enough".

Run as a script; it prints one JSON document to stdout::

    PYTHONHASHSEED=0 ./run.sh notebooks/golden_master.py > baseline.json

``PYTHONHASHSEED`` matters and is not optional. ``run_trial(seed=N)`` seeds
the global RNG, but several policies take ``min()`` over a ``set[str]`` and
break ties by iteration order, which is hash-dependent. Without a pinned hash
seed two identical runs in different processes disagree, and the golden master
would raise false alarms for the whole refactor. ``golden-master.ipynb``
invokes this module in a subprocess with the variable forced, so it does not
matter how JupyterLab itself was launched.
"""

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.algorithm.FixedRoutePolicy import FixedRoutePolicy
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.ReorderingInsertionPolicy import ReorderingInsertionPolicy
from src.algorithm.TravelModel import GraphTravelModel
from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.experiment import TrialResult, run_trial
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph

# The campus map every experiment in this project has used.
MAP_SEED = 42
SEEDS = (0, 1, 2)
WINDOW = 750.0

# Deliberately lighter than the experiment notebooks' 200 requests: this runs
# after every refactor step, so it is tuned for turnaround rather than for
# statistical power. It is a regression check, not a measurement.
BASE_TRIAL = dict(
    max_requests=120,
    capacity=40,
    commitment=0,
    silent=True,
    # Animation is on everywhere because AnimationFrame records
    # (sim_time, bus_stop_id) per arrival — the single most valuable thing to
    # pin, since reproducing the arrival sequence is exactly what the new
    # advance() contract has to guarantee.
    record_animation=True,
    keep_tick_history=True,
)

PolicyFactory = Callable[[TransitGraph], DispatchPolicy]


def _window_insertion(dwell_s: float = 0.0) -> PolicyFactory:
    """
    Build an L0 window-insertion factory.
    :param dwell_s: Dwell the policy should plan against. Non-zero requires an
        explicit travel model, or projections drift from what the bus does.
    :return: A policy factory.
    """
    def factory(graph: TransitGraph) -> DispatchPolicy:
        model = GraphTravelModel(graph, dwell_s=dwell_s) if dwell_s else None
        return WindowInsertionPolicy(
            graph, travel_model=model, max_wait_s=WINDOW
        )
    return factory


def _reordering(n_passes: int | None) -> PolicyFactory:
    """
    Build an L1 relocate-repair factory.
    :param n_passes: Repair sweeps per call; None sweeps to convergence.
    :return: A policy factory.
    """
    def factory(graph: TransitGraph) -> DispatchPolicy:
        return ReorderingInsertionPolicy(
            graph, max_wait_s=WINDOW, n_passes=n_passes, retry_rejected=False
        )
    return factory


def _fixed_route(graph: TransitGraph) -> DispatchPolicy:
    """Cyclic fixed-route policy, auto-generating its stop order."""
    return FixedRoutePolicy(graph)


def _nearest(graph: TransitGraph) -> DispatchPolicy:
    """Greedy nearest-unserved policy — a different policy family entirely."""
    return NearestUnservedPolicy(graph)


@dataclass(frozen=True)
class Scenario:
    """
    One configuration to pin, and the reason it is worth pinning.

    :param name: Key the results are filed under.
    :param why: The code path this scenario exists to cover. Kept in the data
        so a future session can tell whether a diff matters.
    :param policy: Factory building the policy under test.
    :param trial_kwargs: Overrides merged over BASE_TRIAL.
    """

    name: str
    why: str
    policy: PolicyFactory
    trial_kwargs: dict = field(default_factory=dict)


SCENARIOS: list[Scenario] = [
    Scenario(
        "baseline",
        "The reference path: default tick, no dwell, no abandonment.",
        _window_insertion(),
    ),
    Scenario(
        "coarse_tick",
        "dt=120 forces several arrivals inside one tick, which is the "
        "sequentially-dependent inner loop the advance() contract must "
        "reproduce. The single most important scenario here.",
        _window_insertion(),
        dict(dt=120.0),
    ),
    Scenario(
        "dwell",
        "Dwell is folded into the leading edge of each leg by the backend; "
        "pins that arithmetic and the policy/backend dwell agreement.",
        _window_insertion(dwell_s=20.0),
        dict(dwell_s=20.0),
    ),
    Scenario(
        "abandonment",
        "A tight threshold makes _expire_pending actually cancel, pinning "
        "both the rule and its position in the tick order.",
        _window_insertion(),
        dict(max_wait_s=400.0),
    ),
    Scenario(
        "capacity_pressure",
        "A nearly-full bus forces rejections and drives the policy into the "
        "progress fallback, where the schedule is empty.",
        _window_insertion(),
        dict(capacity=2),
    ),
    Scenario(
        "noise",
        "Execution noise: the backend drives a perturbed model while the "
        "policy plans clean. Pins the estimate/reality split.",
        _window_insertion(),
        dict(travel_noise=0.2, noise_seed=7),
    ),
    Scenario(
        "commitment",
        "A locked prefix makes the agent plan from a projected future rather "
        "than from now — the replanning path most likely to shift.",
        _window_insertion(),
        dict(commitment=2),
    ),
    Scenario(
        "l1",
        "Relocate-repair, the policy that rewrites its schedule most "
        "aggressively between arrivals.",
        _reordering(1),
    ),
    Scenario(
        "l1_converge",
        "The n_passes=None fixed-point path, newest code in the stack.",
        _reordering(None),
    ),
    Scenario(
        "fixed_route",
        "A cyclic route: a completely different shape of stop sequence, and "
        "the one baseline the project is graded against.",
        _fixed_route,
    ),
    Scenario(
        "nearest_unserved",
        "Greedy family, no service window — exercises policies that never "
        "build a Schedule at all.",
        _nearest,
    ),
]


def build_graph() -> PlanarStreetGraph:
    """
    Build the shared campus map.
    :return: The planar street graph every scenario runs on.
    """
    return PlanarStreetGraph.generate(
        n_seeds=17,
        bounds=(0.0, 0.0, 2000.0, 2000.0),
        seed=MAP_SEED,
        stop_density=0.5,
    )


def capture_trial(result: TrialResult) -> dict:
    """
    Reduce one trial to everything worth pinning.

    Full per-request arrays are kept rather than summaries so that a
    disagreement can be localised to a request instead of merely detected.
    :param result: The completed trial.
    :return: A JSON-serialisable record.
    """
    return {
        "kpi": {
            "fulfilled_count": result.kpi.fulfilled_count,
            "cancelled_count": result.kpi.cancelled_count,
            "mean_wait_time": result.kpi.mean_wait_time,
            "mean_ride_time": result.kpi.mean_ride_time,
            "mean_journey_time": result.kpi.mean_journey_time,
            "max_wait_time": result.kpi.max_wait_time,
            "p90_wait_time": result.kpi.p90_wait_time,
            "fulfillment_rate": result.kpi.fulfillment_rate,
            "served_fraction": result.kpi.served_fraction,
            "throughput": result.kpi.throughput,
        },
        "tick_count": result.tick_count,
        "n_tick_history": len(result.tick_history),
        "wait_times": result.wait_times,
        "ride_times": result.ride_times,
        # The arrival sequence: what the bus did, and when. This is the
        # behaviour the refactor is most likely to perturb.
        "arrivals": [
            [frame.sim_time, frame.bus_stop_id]
            for frame in result.animation_frames
        ],
    }


def _git_commit() -> str | None:
    """
    Return the current commit, or None outside a repository.
    :return: Short commit hash, or None if git is unavailable.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def environment() -> dict:
    """
    Describe the process, so mismatched baselines are caught not compared.

    ``hash_randomization`` is the load-bearing field: baselines captured with
    it on cannot be reproduced in another process, so comparing across a
    change in it would report differences that are pure environment.
    :return: A JSON-serialisable environment record.
    """
    return {
        "python": platform.python_version(),
        "hash_randomization": bool(sys.flags.hash_randomization),
        "git_commit": _git_commit(),
        "map_seed": MAP_SEED,
        "seeds": list(SEEDS),
        "base_trial": {
            key: value for key, value in BASE_TRIAL.items()
        },
    }


def capture_all() -> dict:
    """
    Run every scenario at every seed and collect the results.
    :return: The complete golden-master document.
    """
    graph = build_graph()
    scenarios: dict[str, dict] = {}
    for scenario in SCENARIOS:
        trials: dict[str, dict] = {}
        for seed in SEEDS:
            result = run_trial(
                policy_factory=scenario.policy,
                graph=graph,
                seed=seed,
                **{**BASE_TRIAL, **scenario.trial_kwargs},
            )
            trials[str(seed)] = capture_trial(result)
        scenarios[scenario.name] = {
            "why": scenario.why,
            "trial_kwargs": {
                key: value for key, value in scenario.trial_kwargs.items()
            },
            "trials": trials,
        }
    return {"environment": environment(), "scenarios": scenarios}


def main() -> int:
    """
    Print the golden-master document to stdout as JSON.
    :return: Process exit code.
    """
    json.dump(capture_all(), sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
