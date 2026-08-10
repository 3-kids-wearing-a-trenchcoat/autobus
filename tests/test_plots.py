"""
Tests for the parameter-sweep plot.

Only the new helper is covered here: it is the one that reads the results
store's flattened table rather than TrialResults, and the one whose failure
mode is quiet. A misnamed column would otherwise plot nothing and look like
a sweep that simply had no effect.
"""

import matplotlib
import pandas as pd
import pytest

matplotlib.use("Agg")

from src.experiment.plots import _sweep_label, plot_parameter_sweep


@pytest.fixture
def frame() -> pd.DataFrame:
    """Two policies swept over three window widths, three seeds each."""
    rows = [
        {
            "identity.policy": policy,
            "identity.seed": seed,
            "identity.policy_params.max_wait_s": window,
            "kpi.mean_wait_time": window / 10.0 + seed,
        }
        for policy in ("Window", "Reordering")
        for window in (600.0, 750.0, 900.0)
        for seed in (0, 1, 2)
    ]
    return pd.DataFrame(rows)


def test_a_sweep_draws_a_line_per_policy(frame: pd.DataFrame) -> None:
    figure = plot_parameter_sweep(
        frame,
        x="identity.policy_params.max_wait_s",
        y="kpi.mean_wait_time",
    )
    axes = figure.axes[0]
    assert len(axes.get_lines()) >= 2
    assert axes.get_xlabel() == "Max wait s"
    assert axes.get_ylabel() == "Mean wait time (s)"


def test_a_single_line_is_allowed(frame: pd.DataFrame) -> None:
    """A sweep of one policy needs no hue."""
    figure = plot_parameter_sweep(
        frame,
        x="identity.policy_params.max_wait_s",
        y="kpi.mean_wait_time",
        hue=None,
    )
    assert figure.axes[0].get_lines()


def test_an_unknown_column_is_refused(frame: pd.DataFrame) -> None:
    """
    The quiet failure this guards against: a mistyped dotted path would
    otherwise draw an empty axes that reads as a parameter with no effect.
    """
    with pytest.raises(KeyError, match="identity.trial.nonsense"):
        plot_parameter_sweep(
            frame, x="identity.trial.nonsense", y="kpi.mean_wait_time"
        )


def test_a_known_kpi_gets_its_documented_label() -> None:
    assert _sweep_label("kpi.mean_ride_time") == "Mean ride time (s)"


def test_an_unknown_column_still_reads_sensibly() -> None:
    """Anything the KPI table does not name still needs an axis label."""
    assert _sweep_label("identity.trial.max_requests") == "Max requests"
