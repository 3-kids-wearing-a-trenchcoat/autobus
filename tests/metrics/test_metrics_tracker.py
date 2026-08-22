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


@pytest.fixture
def one_rider() -> RequestPool:
    """
    One delivered request: created at 0, boarded at 10, dropped at 25.
    So a wait of 10, a ride of 15 and a journey of 25 — three different
    numbers, so a KPI reading the wrong one cannot pass by coincidence.
    """
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    return pool


@pytest.fixture
def two_riders() -> RequestPool:
    """
    Two delivered requests: waits 10 and 20, rides 20 and 20, journeys 30
    and 40. The waits differ so a mean can be told from a max.
    """
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=30.0))
    pool.add(make_fulfilled_request("r2", created_at=0.0,
                                    boarded_at=20.0, fulfilled_at=40.0))
    return pool


@pytest.fixture
def three_riders() -> RequestPool:
    """Three identical delivered requests, for the ratio KPIs."""
    pool = RequestPool()
    for i in range(3):
        pool.add(make_fulfilled_request(f"r{i}", 0.0, 5.0, 10.0))
    return pool


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

def test_an_empty_pool_counts_nothing() -> None:
    kpi = MetricsTracker(RequestPool()).snapshot(sim_time=10.0)
    assert (kpi.fulfilled_count, kpi.cancelled_count) == (0, 0)
    assert kpi.throughput == pytest.approx(0.0)


# Every KPI that averages or divides has nothing to work with here, and each
# must say so with None rather than 0.0 — a zero would read as "measured, and
# it was nothing" and would average into a campaign as if it were data.
@pytest.mark.parametrize("field", [
    "mean_wait_time",
    "mean_ride_time",
    "mean_journey_time",
    "fulfillment_rate",
    "served_fraction",
    "max_wait_time",
    "p90_wait_time",
])
def test_an_empty_pool_has_nothing_to_average(field: str) -> None:
    kpi = MetricsTracker(RequestPool()).snapshot(sim_time=10.0)
    assert getattr(kpi, field) is None


# ---------------------------------------------------------------------------
# snapshot — only cancelled
# ---------------------------------------------------------------------------

def test_snapshot_only_cancelled_means_are_none() -> None:
    pool = RequestPool()
    pool.add(make_pending_request("c1"))
    pool.cancel("c1")

    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.mean_wait_time is None
    assert kpi.mean_ride_time is None
    assert kpi.mean_journey_time is None


def test_snapshot_only_cancelled_count_is_correct() -> None:
    pool = RequestPool()
    pool.add(make_pending_request("c1"))
    pool.add(make_pending_request("c2"))
    pool.cancel("c1")
    pool.cancel("c2")

    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.cancelled_count == 2
    assert kpi.fulfilled_count == 0


# ---------------------------------------------------------------------------
# snapshot — single fulfilled request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, expected", [
    ("mean_wait_time", 10.0),        # created 0 -> boarded 10
    ("mean_ride_time", 15.0),        # boarded 10 -> dropped 25
    ("mean_journey_time", 25.0),     # created 0 -> dropped 25
    ("max_wait_time", 10.0),         # the only wait there is
    ("fulfilled_count", 1),
])
def test_one_rider_is_measured_end_to_end(
    one_rider: RequestPool, field: str, expected: float
) -> None:
    kpi = MetricsTracker(one_rider).snapshot(sim_time=0.0)
    assert getattr(kpi, field) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# snapshot — multiple fulfilled requests (mean check)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, expected", [
    ("mean_wait_time", 15.0),        # waits 10 and 20
    ("mean_ride_time", 20.0),        # rides 20 and 20
    ("mean_journey_time", 35.0),     # journeys 30 and 40
    ("max_wait_time", 20.0),         # the longer of the two waits
])
def test_two_riders_are_averaged_not_summed(
    two_riders: RequestPool, field: str, expected: float
) -> None:
    kpi = MetricsTracker(two_riders).snapshot(sim_time=0.0)
    assert getattr(kpi, field) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# snapshot — excludes in-flight requests
# ---------------------------------------------------------------------------

def test_snapshot_excludes_pending_from_fulfilled_count() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=5.0, fulfilled_at=10.0))
    pool.add(make_pending_request("p1"))

    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.fulfilled_count == 1


def test_snapshot_excludes_on_bus_from_fulfilled_count() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=5.0, fulfilled_at=10.0))
    pool.add(make_pending_request("o1"))
    pool.update_state("o1", RequestState.ON_BUS)

    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.fulfilled_count == 1


def test_snapshot_means_unaffected_by_in_flight() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=10.0, fulfilled_at=25.0))
    pool.add(make_pending_request("p1"))   # no timestamps

    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.mean_wait_time == pytest.approx(10.0)
    assert kpi.mean_ride_time == pytest.approx(15.0)
    assert kpi.mean_journey_time == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# fulfillment_rate
# ---------------------------------------------------------------------------

def test_snapshot_fulfillment_rate_all_fulfilled(
    three_riders: RequestPool
) -> None:
    kpi = MetricsTracker(three_riders).snapshot(sim_time=10.0)
    assert kpi.fulfillment_rate == pytest.approx(1.0)


def test_snapshot_fulfillment_rate_mixed(three_riders: RequestPool) -> None:
    three_riders.add(make_pending_request("c1"))
    three_riders.cancel("c1")
    kpi = MetricsTracker(three_riders).snapshot(sim_time=10.0)
    assert kpi.fulfillment_rate == pytest.approx(0.75)


def test_snapshot_fulfillment_rate_all_cancelled() -> None:
    pool = RequestPool()
    for i in range(2):
        pool.add(make_pending_request(f"c{i}"))
        pool.cancel(f"c{i}")
    kpi = MetricsTracker(pool).snapshot(sim_time=10.0)
    assert kpi.fulfillment_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# served_fraction
# ---------------------------------------------------------------------------

def test_snapshot_served_fraction_all_fulfilled(
    three_riders: RequestPool
) -> None:
    kpi = MetricsTracker(three_riders).snapshot(sim_time=10.0)
    assert kpi.served_fraction == pytest.approx(1.0)


def test_snapshot_served_fraction_counts_stranded_and_in_flight(
    three_riders: RequestPool
) -> None:
    """PENDING and ON_BUS demand lowers served_fraction below fulfillment_rate.

    3 fulfilled + 1 cancelled + 1 pending gives served_fraction 3/5 = 0.6,
    while fulfillment_rate (terminal only) is 3/4 = 0.75.
    """
    three_riders.add(make_pending_request("c1"))
    three_riders.cancel("c1")
    three_riders.add(make_pending_request("p1"))
    kpi = MetricsTracker(three_riders).snapshot(sim_time=10.0)
    assert kpi.served_fraction == pytest.approx(0.6)
    assert kpi.fulfillment_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# throughput
# ---------------------------------------------------------------------------

def test_snapshot_throughput_none_when_sim_time_zero() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", 0.0, 5.0, 10.0))
    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.throughput is None


def test_snapshot_throughput_correct() -> None:
    pool = RequestPool()
    for i in range(4):
        pool.add(make_fulfilled_request(f"r{i}", 0.0, 5.0, 10.0))
    kpi = MetricsTracker(pool).snapshot(sim_time=20.0)
    assert kpi.throughput == pytest.approx(12.0)  # 4 req / 20 s * 60 = 12 req/min


# ---------------------------------------------------------------------------
# p90_wait_time
# ---------------------------------------------------------------------------

def test_snapshot_p90_wait_time_single() -> None:
    pool = RequestPool()
    pool.add(make_fulfilled_request("r1", created_at=0.0,
                                    boarded_at=15.0, fulfilled_at=30.0))
    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.p90_wait_time == pytest.approx(15.0)


def test_snapshot_p90_wait_time_multiple() -> None:
    pool = RequestPool()
    # 11 requests with wait times 10, 20, ..., 110.
    # inclusive method: index = 9/10 * (11-1) = 9.0 → value at index 9 = 100.
    for i in range(1, 12):
        pool.add(make_fulfilled_request(
            f"r{i}",
            created_at=0.0,
            boarded_at=float(i * 10),
            fulfilled_at=float(i * 10 + 5),
        ))
    kpi = MetricsTracker(pool).snapshot(sim_time=0.0)
    assert kpi.p90_wait_time == pytest.approx(100.0)
