"""Tests for the Markdown comparison summary table."""

import statistics

from scipy import stats

from src.experiment.summary import (
    ComparisonSummary,
    _format_summary_table,
    _moe,
    summarize_comparison,
    summarize_results,
)
from src.experiment.trial import TrialResult
from src.metrics.MetricsTracker import KPISnapshot


def _kpi(
    fulfilled: int = 10,
    cancelled: int = 2,
    mean_wait: float | None = 30.0,
    p90_wait: float | None = 50.0,
    mean_ride: float | None = 20.0,
    fulfillment: float | None = 0.8,
    served: float | None = 0.9,
    throughput: float | None = 1.5,
) -> KPISnapshot:
    """Build a KPISnapshot with sensible defaults for testing."""
    return KPISnapshot(
        fulfilled_count=fulfilled,
        cancelled_count=cancelled,
        mean_wait_time=mean_wait,
        mean_ride_time=mean_ride,
        mean_journey_time=None,
        max_wait_time=None,
        p90_wait_time=p90_wait,
        fulfillment_rate=fulfillment,
        served_fraction=served,
        throughput=throughput,
    )


def _trial(
    kpi: KPISnapshot,
    wait_times: list[float] | None = None,
    ride_times: list[float] | None = None,
) -> TrialResult:
    """Build a TrialResult from a KPISnapshot and optional sample lists."""
    return TrialResult(
        kpi=kpi,
        tick_count=0,
        tick_history=[],
        wait_times=wait_times if wait_times is not None else [],
        ride_times=ride_times if ride_times is not None else [],
    )


def test_moe_matches_t_formula() -> None:
    """_moe equals t * s / sqrt(n) for a known sample."""
    values = [10.0, 12.0, 14.0, 16.0]
    n = len(values)
    expected = (
        stats.t.ppf(0.975, n - 1)
        * statistics.stdev(values)
        / (n ** 0.5)
    )
    assert _moe(values, 0.95) == expected


def test_moe_undefined_for_single_value() -> None:
    """_moe is None when fewer than two values are given."""
    assert _moe([5.0], 0.95) is None
    assert _moe([], 0.95) is None


def test_table_structure_has_column_per_policy() -> None:
    """Header/separator expose one column per policy plus 'Property'."""
    results = {
        "Fixed": [_trial(_kpi()), _trial(_kpi())],
        "Nearest": [_trial(_kpi()), _trial(_kpi())],
    }
    lines = _format_summary_table(results, 0.95).splitlines()

    header_cells = [cell.strip() for cell in lines[0].strip("|").split("|")]
    assert header_cells == ["Property", "Fixed", "Nearest"]
    # Separator row: one '---' per header column.
    assert lines[1].count("---") == len(header_cells)
    # One data row per measured property.
    assert len(lines) == 2 + 14


def test_mean_and_moe_cell_formatting() -> None:
    """A mean cell shows 'mean unit (±moe%)', moe relative to the mean."""
    results = {
        "P": [
            _trial(_kpi(mean_wait=20.0)),
            _trial(_kpi(mean_wait=40.0)),
        ],
    }
    table = _format_summary_table(results, 0.95)
    row = next(
        line for line in table.splitlines()
        if line.startswith("| Mean wait time |")
    )
    moe_pct = _moe([20.0, 40.0], 0.95) / 30.0 * 100.0
    assert f"30.0 s (±{moe_pct:.1f}%)" in row


def test_single_trial_omits_moe_term() -> None:
    """With one trial the margin of error term is dropped."""
    results = {"P": [_trial(_kpi(mean_wait=25.0))]}
    table = _format_summary_table(results, 0.95)
    row = next(
        line for line in table.splitlines()
        if line.startswith("| Mean wait time |")
    )
    assert "25.0 s" in row
    assert "±" not in row


def test_all_none_property_shows_na() -> None:
    """A property that is None in every run renders as N/A."""
    results = {
        "P": [
            _trial(_kpi(mean_wait=None)),
            _trial(_kpi(mean_wait=None)),
        ],
    }
    table = _format_summary_table(results, 0.95)
    row = next(
        line for line in table.splitlines()
        if line.startswith("| Mean wait time |")
    )
    assert "N/A" in row


def test_summarize_comparison_bundles_table_and_results(monkeypatch) -> None:
    """summarize_comparison returns the table alongside the raw results."""
    canned = {
        "P": [_trial(_kpi(mean_wait=20.0)), _trial(_kpi(mean_wait=40.0))],
    }

    def fake_compare_policies(policies, graph, **kwargs):
        return canned

    monkeypatch.setattr(
        "src.experiment.summary.compare_policies", fake_compare_policies
    )
    summary = summarize_comparison(
        [("P", lambda g: None)], graph=None, n_trials=2, base_seed=0
    )
    assert isinstance(summary, ComparisonSummary)
    assert summary.results is canned
    assert summary.markdown == _format_summary_table(canned, 0.95)


def test_summarize_comparison_forwards_the_world(monkeypatch) -> None:
    """
    provider and backend_factory must survive the trip to compare_policies.

    summarize_comparison is the entry point every notebook uses, so a
    parameter that stops here reaches nothing.
    """
    seen: dict = {}

    def fake_compare_policies(policies, graph, **kwargs):
        """Record the keywords and return one empty column."""
        seen.update(kwargs)
        return {"P": [_trial(_kpi())]}

    monkeypatch.setattr(
        "src.experiment.summary.compare_policies", fake_compare_policies
    )
    provider, factory = object(), object()
    summarize_comparison(
        [("P", lambda g: None)],
        provider=provider,
        backend_factory=factory,
        n_trials=1,
    )
    assert seen["provider"] is provider
    assert seen["backend_factory"] is factory


def test_percentage_scaling() -> None:
    """Fulfillment rate is scaled by 100 and shown with a % unit."""
    results = {"P": [_trial(_kpi(fulfillment=0.75))]}
    table = _format_summary_table(results, 0.95)
    row = next(
        line for line in table.splitlines()
        if line.startswith("| Fulfillment rate |")
    )
    assert "75.0 %" in row


def test_served_fraction_row_scaled_as_percentage() -> None:
    """Served fraction has its own row, scaled by 100 with a % unit."""
    results = {"P": [_trial(_kpi(served=0.865))]}
    table = _format_summary_table(results, 0.95)
    row = next(
        line for line in table.splitlines()
        if line.startswith("| Served fraction |")
    )
    assert "86.5 %" in row


def test_summarize_results_tabulates_without_running_anything() -> None:
    """
    The half that lets stored runs be tabulated. Hours of CARLA must not
    have to be repeated just to produce the table again.
    """
    results = {"P": [_trial(_kpi(mean_wait=20.0))]}
    summary = summarize_results(results)

    assert summary.results is results
    assert summary.markdown == _format_summary_table(results, 0.95)


def test_summarize_results_honours_the_confidence_level() -> None:
    results = {"P": [_trial(_kpi(mean_wait=20.0)), _trial(_kpi(40.0))]}
    assert (summarize_results(results, confidence=0.99).markdown
            == _format_summary_table(results, 0.99))
