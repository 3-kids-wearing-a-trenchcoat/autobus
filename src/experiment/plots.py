"""Visualisation helpers for policy comparison experiments."""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from src.experiment.trial import TrialResult


_KPI_LABELS: dict[str, str] = {
    "mean_wait_time": "Mean wait time (s)",
    "p90_wait_time": "P90 wait time (s)",
    "max_wait_time": "Max wait time (s)",
    "mean_ride_time": "Mean ride time (s)",
    "mean_journey_time": "Mean journey time (s)",
    "fulfillment_rate": "Fulfillment rate",
    "throughput": "Throughput (req/min)",
    "fulfilled_count": "Fulfilled requests",
    "cancelled_count": "Cancelled requests",
}


def plot_kpi_bars(
    results: dict[str, list[TrialResult]],
    fields: list[str],
) -> Figure:
    """
    Bar chart comparing one or more KPI fields across policies.

    Each field gets its own subplot. Error bars show one standard
    deviation and are omitted for single-trial results.

    :param results: Policy label → list of TrialResult, as returned by
        compare_policies.
    :param fields: KPI field names to plot (attributes of KPISnapshot,
        e.g. ["mean_wait_time", "throughput"]).
    :return: Matplotlib Figure.
    """
    n_fields = len(fields)
    fig, axes = plt.subplots(
        1, n_fields, figsize=(5 * n_fields, 4), squeeze=False
    )
    labels = list(results.keys())
    x = np.arange(len(labels))

    for col, field in enumerate(fields):
        ax: Axes = axes[0][col]
        means, stds = _aggregate_kpi(results, field)
        n_trials = len(results[labels[0]])
        yerr = stds if n_trials > 1 else None
        ax.bar(x, means, yerr=yerr, capsize=4,
               color="steelblue", alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=15, ha="right")
        ax.set_ylabel(_KPI_LABELS.get(field, field))
        ax.set_title(_KPI_LABELS.get(field, field))

    fig.tight_layout()
    return fig


def plot_wait_distribution(
    results: dict[str, list[TrialResult]],
) -> Figure:
    """
    Box plot of per-request wait times, grouped by policy.

    All trials for a policy are pooled into a single distribution.

    :param results: Policy label → list of TrialResult, as returned by
        compare_policies.
    :return: Matplotlib Figure.
    """
    fig, ax = plt.subplots(figsize=(max(5, 2 * len(results)), 4))
    labels = list(results.keys())
    data = [
        [t for r in trials for t in r.wait_times]
        for trials in results.values()
    ]
    ax.boxplot(
        data,
        labels=labels,
        patch_artist=True,
        boxprops=dict(facecolor="steelblue", alpha=0.6),
    )
    ax.set_ylabel("Wait time (s)")
    ax.set_title("Wait time distribution by policy")
    fig.tight_layout()
    return fig


def plot_occupancy(
    results: dict[str, list[TrialResult]],
) -> Figure:
    """
    Line plot of bus occupancy and pending requests over time.

    When multiple trials are available, each trial is drawn as a
    semi-transparent line and the mean is overlaid as a solid line.
    One subplot pair (occupancy / pending) is drawn per policy.

    :param results: Policy label → list of TrialResult, as returned by
        compare_policies.
    :return: Matplotlib Figure.
    """
    labels = list(results.keys())
    n = len(labels)
    fig, axes = plt.subplots(
        n, 2, figsize=(12, 3 * n), squeeze=False
    )

    for row, label in enumerate(labels):
        _plot_occupancy_row(axes[row], label, results[label])

    fig.tight_layout()
    return fig


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _aggregate_kpi(
    results: dict[str, list[TrialResult]],
    field: str,
) -> tuple[list[float], list[float]]:
    """
    Compute per-policy mean and std for a single KPI field.

    :param results: Policy label → list of TrialResult.
    :param field: Attribute name on KPISnapshot.
    :return: (means, stds) lists aligned with results.keys().
    """
    means, stds = [], []
    for trials in results.values():
        values = [
            v for r in trials
            if (v := getattr(r.kpi, field)) is not None
        ]
        means.append(float(np.mean(values)) if values else 0.0)
        stds.append(
            float(np.std(values)) if len(values) > 1 else 0.0
        )
    return means, stds


def _plot_occupancy_row(
    axes: np.ndarray,
    label: str,
    trials: list[TrialResult],
) -> None:
    """
    Plot occupancy and pending count for one policy into an axes pair.

    :param axes: Array of two Axes: [occupancy_ax, pending_ax].
    :param label: Policy name used in subplot titles.
    :param trials: List of TrialResult for this policy.
    """
    occ_ax: Axes = axes[0]
    pend_ax: Axes = axes[1]

    for r in trials:
        times = [t.sim_time for t in r.tick_history]
        occ_ax.plot(
            times,
            [t.occupancy for t in r.tick_history],
            alpha=0.3, color="steelblue",
        )
        pend_ax.plot(
            times,
            [t.pending_count for t in r.tick_history],
            alpha=0.3, color="tomato",
        )

    if len(trials) > 1:
        min_len = min(len(r.tick_history) for r in trials)
        mean_times = [
            trials[0].tick_history[i].sim_time
            for i in range(min_len)
        ]
        mean_occ = [
            np.mean([r.tick_history[i].occupancy for r in trials])
            for i in range(min_len)
        ]
        mean_pend = [
            np.mean(
                [r.tick_history[i].pending_count for r in trials]
            )
            for i in range(min_len)
        ]
        occ_ax.plot(
            mean_times, mean_occ,
            color="steelblue", linewidth=2, label="mean",
        )
        pend_ax.plot(
            mean_times, mean_pend,
            color="tomato", linewidth=2, label="mean",
        )
        occ_ax.legend()
        pend_ax.legend()

    occ_ax.set_xlabel("Simulation time (s)")
    occ_ax.set_ylabel("Passengers on bus")
    occ_ax.set_title(f"{label} — occupancy")
    pend_ax.set_xlabel("Simulation time (s)")
    pend_ax.set_ylabel("Pending requests")
    pend_ax.set_title(f"{label} — pending requests")
