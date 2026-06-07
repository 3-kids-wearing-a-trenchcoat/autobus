"""Tests for MetricsTracker, TickSnapshot, and KPISnapshot."""

import pytest

from src.algorithm.RequestPool import Request, RequestPool, RequestState
from src.metrics.MetricsTracker import KPISnapshot, MetricsTracker, TickSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_fulfilled_request(
    id: str,
    created_at: float,
    boarded_at: float,
    fulfilled_at: float,
) -> Request:
    """Build a Request that is already in the FULFILLED state."""
    r = Request(
        id=id,
        pickup_stop_id="A",
        dropoff_stop_id="B",
        created_at=created_at,
    )
    r.boarded_at = boarded_at
    r.fulfilled_at = fulfilled_at
    r.state = RequestState.FULFILLED
    return r


def make_pending_request(id: str) -> Request:
    """Build a minimal PENDING request."""
    return Request(id=id, pickup_stop_id="A", dropoff_stop_id="B",
                   created_at=0.0)


# ---------------------------------------------------------------------------
# record_tick / tick_history
# ---------------------------------------------------------------------------

def test_tick_history_empty_initially() -> None:
    tracker = MetricsTracker(RequestPool())
    assert tracker.tick_history == []


def test_record_tick_appends_snapshot() -> None:
    tracker = MetricsTracker(RequestPool())
    tracker.record_tick(5.0)
    assert len(tracker.tick_history) == 1
    assert tracker.tick_history[0].sim_time == 5.0


def test_record_tick_counts_pending_and_on_bus() -> None:
    pool = RequestPool()
    pool.add(make_pending_request("p1"))
    pool.add(make_pending_request("p2"))
    pool.add(make_pending_request("o1"))
    pool.update_state("o1", RequestState.ON_BUS)

    tracker = MetricsTracker(pool)
    tracker.record_tick(0.0)

    snap = tracker.tick_history[0]
    assert snap.pending_count == 2
    assert snap.occupancy == 1


def test_record_tick_multiple_ticks_ordered() -> None:
    tracker = MetricsTracker(RequestPool())
    for t in [1.0, 2.0, 3.0]:
        tracker.record_tick(t)
    times = [s.sim_time for s in tracker.tick_history]
    assert times == [1.0, 2.0, 3.0]


def test_tick_history_returns_copy() -> None:
    tracker = MetricsTracker(RequestPool())
    tracker.record_tick(1.0)
    history = tracker.tick_history
    history.clear()
    assert len(tracker.tick_history) == 1


# ---------------------------------------------------------------------------
# snapshot — empty pool
# ---------------------------------------------------------------------------

def test_snapshot_empty_pool_counts_are_zero() -> None:
    kpi = MetricsTracker(RequestPool()).snapshot()
    assert kpi.fulfilled_count == 0
    assert kpi.cancelled_count == 0


def test_snapshot_empty_pool_means_are_none() -> None:
    kpi = MetricsTracker(RequestPool()).snapshot()
    assert kpi.mean_wait_time is None
    assert kpi.mean_ride_time is None
    assert kpi.mean_journey_time is None


# ---------------------------------------------------------------------------
# snapshot — only cancelled
# ---------------------------------------------------------------------------

def test_snapshot_only_cancelled_means_are_none() -> None:
    pool = RequestPool()
    pool.add(make_pending_request("c1"))
    pool.cancel("c1")

    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_wait_time is None
    assert kpi.mean_ride_time is None
    assert kpi.mean_journey_time is None


def test_snapshot_only_cancelled_count_is_correct() -> None:
    pool = RequestPool()
    pool.add(make_pending_request("c1"))
    pool.add(make_pending_request("c2"))
    pool.cancel("c1")
    pool.cancel("c2")

    kpi = MetricsTracker(pool).snapshot()
    assert kpi.cancelled_count == 2
    assert kpi.fulfilled_count == 0


# ---------------------------------------------------------------------------
# snapshot — single fulfilled request
# ---------------------------------------------------------------------------

def test_snapshot_single_fulfilled_wait_time() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_wait_time == pytest.approx(10.0)


def test_snapshot_single_fulfilled_ride_time() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_ride_time == pytest.approx(15.0)


def test_snapshot_single_fulfilled_journey_time() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_journey_time == pytest.approx(25.0)


def test_snapshot_single_fulfilled_count() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.fulfilled_count == 1


# ---------------------------------------------------------------------------
# snapshot — multiple fulfilled requests (mean check)
# ---------------------------------------------------------------------------

def test_snapshot_mean_wait_time_across_multiple() -> None:
    pool = RequestPool()
    # wait times: 10 and 20 → mean 15
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=30.0))
    pool.add(make_fulfilled_request("r2", created_at=0.0,
                                    boarded_at=20.0, fulfilled_at=40.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_wait_time == pytest.approx(15.0)


def test_snapshot_mean_ride_time_across_multiple() -> None:
    pool = RequestPool()
    # ride times: 20 and 20 → mean 20
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=30.0))
    pool.add(make_fulfilled_request("r2", created_at=0.0,
                                    boarded_at=20.0, fulfilled_at=40.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_ride_time == pytest.approx(20.0)


def test_snapshot_mean_journey_time_across_multiple() -> None:
    pool = RequestPool()
    # journey times: 30 and 40 → mean 35
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=30.0))
    pool.add(make_fulfilled_request("r2", created_at=0.0,
                                    boarded_at=20.0, fulfilled_at=40.0))
    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_journey_time == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# snapshot — excludes in-flight requests
# ---------------------------------------------------------------------------

def test_snapshot_excludes_pending_from_fulfilled_count() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=5.0, fulfilled_at=10.0))
    pool.add(make_pending_request("p1"))

    kpi = MetricsTracker(pool).snapshot()
    assert kpi.fulfilled_count == 1


def test_snapshot_excludes_on_bus_from_fulfilled_count() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=5.0, fulfilled_at=10.0))
    pool.add(make_pending_request("o1"))
    pool.update_state("o1", RequestState.ON_BUS)

    kpi = MetricsTracker(pool).snapshot()
    assert kpi.fulfilled_count == 1


def test_snapshot_means_unaffected_by_in_flight() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    pool.add(make_pending_request("p1"))   # no timestamps

    kpi = MetricsTracker(pool).snapshot()
    assert kpi.mean_wait_time == pytest.approx(10.0)
    assert kpi.mean_ride_time == pytest.approx(15.0)
    assert kpi.mean_journey_time == pytest.approx(25.0)
