"""Markdown summary table for multi-trial policy comparison."""

import statistics
from dataclasses import dataclass
from typing import Callable

from scipy import stats

from src.algorithm.DispatchPolicy import DispatchPolicy
from src.experiment.comparison import compare_policies
from src.experiment.trial import BackendFactory, TrialResult
from src.map.MapProvider import MapProvider
from src.map.TransitGraph import TransitGraph
from src.map.graph import PlanarStreetGraph

# One row per measured property, mirroring the "Inspecting TrialResult"
# subsection of the notebook. Each entry is
# (label, extractor, unit) where extractor maps a single TrialResult to a
# per-run scalar (or None when the property is undefined for that run) and
# unit selects how the aggregated value is formatted by _format_cell.
_RowSpec = tuple[str, Callable[[TrialResult], float | None], str]

_ROW_SPECS: list[_RowSpec] = [
    ("Fulfilled requests", lambda r: r.kpi.fulfilled_count, ""),
    ("Cancelled requests", lambda r: r.kpi.cancelled_count, ""),
    ("Mean wait time", lambda r: r.kpi.mean_wait_time, "s"),
    ("P90 wait time", lambda r: r.kpi.p90_wait_time, "s"),
    ("Mean ride time", lambda r: r.kpi.mean_ride_time, "s"),
    ("Fulfillment rate", lambda r: r.kpi.fulfillment_rate, "%"),
    ("Served fraction", lambda r: r.kpi.served_fraction, "%"),
    ("Throughput", lambda r: r.kpi.throughput, "req/min"),
    ("Tick snapshots", lambda r: r.tick_count, ""),
    ("Fulfilled (wait samples)", lambda r: len(r.wait_times), ""),
    ("Min wait time", lambda r: min(r.wait_times) if r.wait_times else None,
     "s"),
    ("Max wait time", lambda r: max(r.wait_times) if r.wait_times else None,
     "s"),
    ("Min ride time", lambda r: min(r.ride_times) if r.ride_times else None,
     "s"),
    ("Max ride time", lambda r: max(r.ride_times) if r.ride_times else None,
     "s"),
]


@dataclass
class ComparisonSummary:
    """
    Output of summarize_comparison: a table plus the runs behind it.

    :param markdown: The comparison table rendered as Markdown.
    :param results: Raw per-policy trial results, exactly as returned by
        compare_policies (a mapping from policy label to its list of
        TrialResult). Feed this to the plot helpers (plot_kpi_bars,
        plot_wait_distribution, plot_occupancy) to visualise the same runs
        without re-running the simulation.
    """

    markdown: str
    results: dict[str, list[TrialResult]]


def summarize_comparison(
    policies: list[tuple[str, Callable[[TransitGraph], DispatchPolicy]]],
    graph: PlanarStreetGraph | None = None,
    n_trials: int = 1,
    base_seed: int | None = None,
    confidence: float = 0.95,
    progress: bool = True,
    provider: MapProvider | None = None,
    backend_factory: BackendFactory | None = None,
    **trial_kwargs,
) -> ComparisonSummary:
    """
    Run every policy for n_trials and summarise the results.

    Delegates to compare_policies to run the trials, then builds a table
    whose rows are the measured properties and whose columns are the
    policies. Each cell is the mean of that property across runs, followed
    by its margin of error as a percentage of the mean (the relative
    confidence-interval half-width) in parentheses. Supports any number
    (>0) of policies.

    :param policies: List of (label, factory) pairs. Each factory accepts
        a TransitGraph and returns a DispatchPolicy.
    :param graph: Pre-built street graph shared across all runs. Ignored
        when ``provider`` is given; required otherwise.
    :param n_trials: Number of independent trials per policy.
    :param base_seed: Base random seed forwarded to compare_policies.
        Trial i uses base_seed + i. None = non-deterministic.
    :param confidence: Confidence level for the margin of error, in (0, 1).
    :param progress: When True, display one tqdm progress bar per policy
        while the trials run. Set False to run silently.
    :param provider: Map source shared across all runs, used instead of
        ``graph``. Defaults to None, which wraps ``graph`` in a
        PlanarMapProvider.
    :param backend_factory: World builder shared across all runs. Defaults
        to None, which gives every trial the usual mock backend.
    :param trial_kwargs: Additional keyword arguments forwarded to
        run_trial (e.g. max_requests, capacity, dt).
    :return: A ComparisonSummary bundling the Markdown table and the raw
        per-policy trial results (the latter for reuse by the plot
        helpers).
    """
    results = compare_policies(
        policies,
        graph,
        n_trials=n_trials,
        base_seed=base_seed,
        progress=progress,
        provider=provider,
        backend_factory=backend_factory,
        **trial_kwargs,
    )
    return summarize_results(results, confidence=confidence)


def summarize_results(
    results: dict[str, list[TrialResult]],
    confidence: float = 0.95,
) -> ComparisonSummary:
    """
    Summarise runs that have already been done.

    The half of ``summarize_comparison`` that does not run anything, so that
    runs rebuilt from the results store — hours of CARLA that must not be
    repeated to be tabulated — produce the same table as runs still in memory.
    :param results: Policy label to its trial results, as returned by
        ``compare_policies`` or ``store.to_results``.
    :param confidence: Confidence level for the margin of error, in (0, 1).
    :return: A ComparisonSummary bundling the Markdown table and the results
        it was built from.
    """
    return ComparisonSummary(
        markdown=_format_summary_table(results, confidence),
        results=results,
    )


def _mean(values: list[float]) -> float | None:
    """
    Mean of the given values, or None when there are no values.

    :param values: Per-run scalar values (already stripped of None).
    :return: The arithmetic mean, or None if values is empty.
    """
    return statistics.fmean(values) if values else None


def _moe(values: list[float], confidence: float) -> float | None:
    """
    Margin of error (confidence-interval half-width) for a mean.

    Uses the Student t-distribution: half-width = t * s / sqrt(n), where
    t is the two-sided critical value at the given confidence level with
    n - 1 degrees of freedom, s is the sample standard deviation and n is
    the number of values.

    :param values: Per-run scalar values (already stripped of None).
    :param confidence: Confidence level in (0, 1).
    :return: The margin of error, or None when it is undefined (fewer than
        two values).
    """
    n = len(values)
    if n < 2:
        return None
    std = statistics.stdev(values)
    critical = stats.t.ppf((1.0 + confidence) / 2.0, n - 1)
    return critical * std / (n ** 0.5)


def _format_cell(
    mean: float | None,
    moe: float | None,
    unit: str,
) -> str:
    """
    Format a single table cell as "mean unit (±margin of error %)".

    The margin of error is shown as a percentage of the mean (its relative
    confidence-interval half-width), so a target such as "below 5%" reads
    directly off the cell.

    :param mean: Mean of the property across runs, or None.
    :param moe: Margin of error (in the property's own units), or None when
        it is undefined.
    :param unit: Display unit; "%" scales values by 100, "" is unitless.
    :return: The formatted cell text, or "N/A" when mean is None.
    """
    if mean is None:
        return "N/A"

    scale = 100.0 if unit == "%" else 1.0
    suffix = f" {unit}" if unit else ""
    value = f"{mean * scale:.1f}"
    if moe is None:
        return f"{value}{suffix}"
    moe_pct = 0.0 if mean == 0 else abs(moe / mean) * 100.0
    return f"{value}{suffix} (±{moe_pct:.1f}%)"


def _format_summary_table(
    results: dict[str, list[TrialResult]],
    confidence: float,
) -> str:
    """
    Build the Markdown comparison table from per-policy trial results.

    :param results: Mapping of policy label to its list of TrialResult.
    :param confidence: Confidence level for the margin of error.
    :return: A Markdown table string with one column per policy and one
        row per measured property.
    """
    labels = list(results.keys())
    header = "| Property | " + " | ".join(labels) + " |"
    separator = "| --- | " + " | ".join("---" for _ in labels) + " |"

    lines = [header, separator]
    for label, extractor, unit in _ROW_SPECS:
        cells = []
        for policy_label in labels:
            values = [
                value
                for trial in results[policy_label]
                if (value := extractor(trial)) is not None
            ]
            cells.append(
                _format_cell(_mean(values), _moe(values, confidence), unit)
            )
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    return "\n".join(lines)
