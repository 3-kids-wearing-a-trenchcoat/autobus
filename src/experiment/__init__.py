"""Experiment helpers for comparing routing policies."""

from src.experiment.animation import (
    animate_trial,
    show_animation,
    show_animations,
)
from src.experiment.comparison import compare_policies
from src.experiment.plots import (
    plot_kpi_bars,
    plot_occupancy,
    plot_wait_distribution,
)
from src.experiment.summary import summarize_comparison
from src.experiment.trial import TrialResult, run_trial

__all__ = [
    "TrialResult",
    "run_trial",
    "compare_policies",
    "summarize_comparison",
    "plot_kpi_bars",
    "plot_wait_distribution",
    "plot_occupancy",
    "animate_trial",
    "show_animation",
    "show_animations",
]
