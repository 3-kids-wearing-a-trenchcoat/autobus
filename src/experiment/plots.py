"""Visualisation helpers for policy comparison experiments."""

import math

import numpy as np
import pandas as pd
import seaborn
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

    if any(not r.tick_history for r in trials):
        raise ValueError(
            f"Cannot plot occupancy for '{label}': tick history was not "
            "retained. Re-run with keep_tick_history=True."
        )

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


def plot_parameter_sweep(
    frame: pd.DataFrame,
    x: str,
    y: str,
    hue: str | None = "identity.policy",
    title: str | None = None,
    confidence: int = 95,
    ax: Axes | None = None,
) -> Figure:
    """
    Plot a KPI against a swept parameter, one line per policy.

    Takes the table from ``store.load_frame`` rather than TrialResults,
    because a sweep asks a question the other plot helpers cannot: how a KPI
    moves as a *parameter* moves. Columns are dotted paths into a stored run
    — ``identity.trial.max_requests``, ``kpi.mean_wait_time``.

    The band is a confidence interval computed across the repeated seeds at
    each x, which is what those seeds were run for. **Filter the frame to the
    parameters being held fixed before calling this**: seaborn will happily
    average across a parameter nobody meant to vary, and the result looks
    perfectly reasonable.

    :param frame: One row per run, from ``store.load_frame``.
    :param x: Column holding the swept parameter.
    :param y: Column holding the KPI to plot.
    :param hue: Column to draw a separate line for. Defaults to the policy
        label; pass None for a single line.
    :param title: Figure title. Defaults to None, which names the two axes.
    :param confidence: Confidence level for the band, as a percentage.
    :param ax: Axes to draw on. Defaults to None, which makes a new figure.
    :return: The figure drawn on.
    :raises KeyError: If ``x`` or ``y`` is not a column of the frame.
    """
    for column in (x, y):
        if column not in frame.columns:
            raise KeyError(
                f"{column!r} is not a column of this frame; "
                f"available: {sorted(frame.columns)}"
            )

    if ax is None:
        _, ax = plt.subplots(figsize=(7.0, 4.5))

    seaborn.lineplot(
        data=frame, x=x, y=y, hue=hue, marker="o",
        errorbar=("ci", confidence), ax=ax,
    )
    ax.set_xlabel(_sweep_label(x))
    ax.set_ylabel(_sweep_label(y))
    ax.set_title(title or f"{_sweep_label(y)} against {_sweep_label(x)}")
    ax.grid(True, alpha=0.3)
    ax.figure.tight_layout()
    return ax.figure


def _sweep_label(column: str) -> str:
    """
    Turn a dotted column name into something readable on an axis.
    :param column: A column such as ``kpi.mean_wait_time``.
    :return: A human-readable label.
    """
    leaf = column.rsplit(".", 1)[-1]
    return _KPI_LABELS.get(leaf, leaf.replace("_", " ").capitalize())
