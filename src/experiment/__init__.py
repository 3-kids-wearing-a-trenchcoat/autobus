"""Experiment helpers for comparing routing policies."""

from src.experiment.animation import (
    animate_trial,
    show_animation,
    show_animations,
)
from src.experiment.campaign import CampaignResult, run_campaign
from src.experiment.comparison import compare_policies
from src.experiment.plots import (
    plot_kpi_bars,
    plot_occupancy,
    plot_wait_distribution,
)
from src.experiment.store import (
    DEFAULT_RUNS_DIR,
    RunIdentity,
    RunRecord,
    is_recorded,
    load_frame,
    load_runs,
    save_run,
    to_results,
)
from src.experiment.summary import ComparisonSummary, summarize_comparison
from src.experiment.trial import BackendFactory, TrialResult, run_trial

__all__ = [
    "BackendFactory",
    "TrialResult",
    "run_trial",
    "compare_policies",
    "CampaignResult",
    "run_campaign",
    "DEFAULT_RUNS_DIR",
    "RunIdentity",
    "RunRecord",
    "is_recorded",
    "load_frame",
    "load_runs",
    "save_run",
    "to_results",
    "summarize_comparison",
    "ComparisonSummary",
    "plot_kpi_bars",
    "plot_wait_distribution",
    "plot_occupancy",
    "animate_trial",
    "show_animation",
    "show_animations",
]
