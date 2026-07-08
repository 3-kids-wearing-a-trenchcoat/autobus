"""Visualisation helpers for policy comparison experiments."""

import math

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
    title: str | None = None,
    max_cols: int = 2,
) -> Figure:
    """
    Bar chart comparing one or more KPI fields across policies.

    Each field gets its own subplot; subplots wrap into a grid of at most
    max_cols columns so the figure keeps a sensible aspect ratio (a single
    wide row overflows the notebook's output area). Error bars show one
    standard deviation and are omitted for single-trial results.

    :param results: Policy label → list of TrialResult, as returned by
        compare_policies.
    :param fields: KPI field names to plot (attributes of KPISnapshot,
        e.g. ["mean_wait_time", "throughput"]).
    :param title: Optional figure-level title (suptitle).
    :param max_cols: Maximum number of subplot columns before wrapping to
        a new row.
    :return: Matplotlib Figure.
    """
    n_fields = len(fields)
    n_cols = min(n_fields, max_cols)
    n_rows = math.ceil(n_fields / n_cols)
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(5 * n_cols, 4 * n_rows),
        squeeze=False,
    )
    flat_axes = axes.ravel()
    labels = list(results.keys())
    x = np.arange(len(labels))

    for idx, field in enumerate(fields):
        ax: Axes = flat_axes[idx]
        means, stds = _aggregate_kpi(results, field)
        n_trials = len(results[labels[0]])
        yerr = stds if n_trials > 1 else None
        ax.bar(x, means, yerr=yerr, capsize=4,
               color="steelblue", alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=15, ha="right")
        ax.set_ylabel(_KPI_LABELS.get(field, field))
        ax.set_title(_KPI_LABELS.get(field, field))

    # Hide any leftover axes when the grid is not completely filled.
    for idx in range(n_fields, n_rows * n_cols):
        flat_axes[idx].set_visible(False)

    # tight_layout (computed eagerly) renders reliably under the notebook's
    # ipympl backend, unlike constrained_layout which defers to draw time.
    if title:
        fig.suptitle(title)
        fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))
    else:
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
        tick_labels=labels,
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
    Mean occupancy and pending-request curves over time, per policy.

    For each policy the mean across trials is drawn as a solid line with a
    shaded band spanning ±1 standard deviation (the run-to-run spread);
    with a single trial only its line is shown. Trials are aligned by tick
    index and truncated to the shortest run. One subplot pair (occupancy /
    pending) is drawn per policy.

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

    # Align trials by tick index (dt is constant, so index maps to time)
    # and truncate to the shortest run so every tick has a value from all
    # trials.
    min_len = min(len(r.tick_history) for r in trials)
    times = [trials[0].tick_history[i].sim_time for i in range(min_len)]
    occupancy = np.array([
        [r.tick_history[i].occupancy for i in range(min_len)]
        for r in trials
    ])
    pending = np.array([
        [r.tick_history[i].pending_count for i in range(min_len)]
        for r in trials
    ])

    _plot_mean_band(occ_ax, times, occupancy, "steelblue")
    _plot_mean_band(pend_ax, times, pending, "tomato")

    occ_ax.set_xlabel("Simulation time (s)")
    occ_ax.set_ylabel("Passengers on bus")
    occ_ax.set_title(f"{label} — occupancy")
    pend_ax.set_xlabel("Simulation time (s)")
    pend_ax.set_ylabel("Pending requests")
    pend_ax.set_title(f"{label} — pending requests")


def _plot_mean_band(
    ax: Axes,
    times: list[float],
    data: np.ndarray,
    color: str,
) -> None:
    """
    Draw a mean line with a ±1 standard-deviation shaded band.

    The band's lower edge is clipped at zero, since occupancy and pending
    counts are non-negative. With a single trial only the line is drawn.

    :param times: X values (simulation time per tick).
    :param data: 2-D array of shape (n_trials, n_ticks).
    :param color: Line and band colour.
    """
    mean = data.mean(axis=0)
    ax.plot(times, mean, color=color, linewidth=2, label="mean")

    if data.shape[0] > 1:
        std = data.std(axis=0)
        ax.fill_between(
            times,
            np.maximum(mean - std, 0.0),
            mean + std,
            color=color, alpha=0.25, linewidth=0, label="±1σ",
        )
        ax.legend(loc="upper right")
