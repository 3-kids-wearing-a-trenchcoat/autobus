"""Passive simulation observer: collects tick snapshots and computes KPIs."""

import statistics
from dataclasses import dataclass

from src.algorithm.RequestPool import RequestPool


@dataclass(slots=True)
class TickSnapshot:
    """
    Metrics recorded at a single simulation tick.

    :param sim_time: Simulation time at which this snapshot was taken.
    :param pending_count: Number of requests with state PENDING.
    :param occupancy: Number of requests with state ON_BUS.
    """

    sim_time: float
    pending_count: int
    occupancy: int


@dataclass
class KPISnapshot:
    """
    Aggregate KPIs computed from completed requests.

    Timing means are computed over fulfilled requests only; in-flight
    (PENDING or ON_BUS) requests are excluded. All time values are in
    simulation seconds. Fields are None when there is not yet enough
    data to compute them.

    :param fulfilled_count: Total number of fulfilled requests.
    :param cancelled_count: Total number of cancelled requests.
    :param mean_wait_time: Average time from created_at to boarded_at.
        None if no requests have been fulfilled yet.
    :param mean_ride_time: Average time from boarded_at to fulfilled_at.
        None if no requests have been fulfilled yet.
    :param mean_journey_time: Average time from created_at to
        fulfilled_at. None if no requests have been fulfilled yet.
    :param max_wait_time: Longest wait time across all fulfilled
        requests. None if no requests have been fulfilled yet.
    :param p90_wait_time: 90th-percentile wait time across fulfilled
        requests. None if no requests have been fulfilled yet.
    :param fulfillment_rate: Fraction of terminal requests that were
        fulfilled (fulfilled / (fulfilled + cancelled)).
        None if no terminal requests exist yet.
    :param served_fraction: Fraction of ALL requests ever created that were
        fulfilled (fulfilled / (fulfilled + cancelled + pending + on_bus)).
        Unlike fulfillment_rate, still-waiting (PENDING) and in-flight
        (ON_BUS) demand counts against it, so stranded requests are not
        hidden. None if the pool holds no requests yet.
    :param throughput: Fulfilled requests per simulation minute
        (fulfilled_count / sim_time * 60).
        None if sim_time is 0.
    """

    fulfilled_count: int
    cancelled_count: int
    mean_wait_time: float | None
    mean_ride_time: float | None
    mean_journey_time: float | None
    max_wait_time: float | None
    p90_wait_time: float | None
    fulfillment_rate: float | None
    served_fraction: float | None
    throughput: float | None


class MetricsTracker:
    """
    Passive observer that records per-tick snapshots and computes
    aggregate KPIs from completed requests.

    MetricsTracker never writes to the pool; it only reads from it.

    Expected call pattern each simulation tick:
      1. ``bus_agent.on_arrival(stop_id, sim_time)``
      2. ``metrics_tracker.record_tick(sim_time)``
      3. Optionally: ``metrics_tracker.snapshot()`` to inspect KPIs.

    :param pool: The shared request pool to read from.
    """

    def __init__(self, pool: RequestPool) -> None:
        self._pool = pool
        self._ticks: list[TickSnapshot] = []

    def record_tick(self, sim_time: float) -> None:
        """
        Record a snapshot of queue state at the current tick.

        Call this once per simulation tick, after ``on_arrival`` has
        finished so that boarding and alighting are already reflected.
        :param sim_time: Current simulation time in seconds.
        """
        snapshot = TickSnapshot(
            sim_time=sim_time,
            pending_count=len(self._pool.get_pending()),
            occupancy=len(self._pool.get_on_bus()),
        )
        self._ticks.append(snapshot)

    @property
    def tick_history(self) -> list[TickSnapshot]:
        """Return a copy of the recorded per-tick snapshots."""
        return list(self._ticks)

    @property
    def tick_count(self) -> int:
        """Number of recorded per-tick snapshots (no list copy)."""
        return len(self._ticks)

    def snapshot(self, sim_time: float) -> KPISnapshot:
        """
        Compute aggregate KPIs as of the given simulation time.

        Timing means are computed over fulfilled requests only; in-flight
        requests are excluded. fulfillment_rate covers all terminal
        (fulfilled + cancelled) requests. throughput is None when
        sim_time is 0.
        :param sim_time: Current simulation time in seconds.
        :return: KPISnapshot with current aggregate metrics.
        """
        fulfilled = self._pool.get_fulfilled()
        cancelled = self._pool.get_cancelled()
        n_fulfilled = len(fulfilled)
        n_cancelled = len(cancelled)
        n_terminal = n_fulfilled + n_cancelled

        fulfillment_rate = (
            n_fulfilled / n_terminal if n_terminal > 0 else None
        )
        # served_fraction counts still-waiting and in-flight demand in the
        # denominator, so stranded requests lower it (unlike fulfillment_rate).
        n_total = (
            n_terminal
            + len(self._pool.get_pending())
            + len(self._pool.get_on_bus())
        )
        served_fraction = (
            n_fulfilled / n_total if n_total > 0 else None
        )
        throughput = (
            n_fulfilled / sim_time * 60 if sim_time > 0 else None
        )

        if not fulfilled:
            return KPISnapshot(
                fulfilled_count=0,
                cancelled_count=n_cancelled,
                mean_wait_time=None,
                mean_ride_time=None,
                mean_journey_time=None,
                max_wait_time=None,
                p90_wait_time=None,
                fulfillment_rate=fulfillment_rate,
                served_fraction=served_fraction,
                throughput=throughput,
            )

        wait_times = [r.boarded_at - r.created_at for r in fulfilled]
        ride_times = [r.fulfilled_at - r.boarded_at for r in fulfilled]
        journey_times = [r.fulfilled_at - r.created_at for r in fulfilled]
        n = len(fulfilled)

        return KPISnapshot(
            fulfilled_count=n,
            cancelled_count=n_cancelled,
            mean_wait_time=sum(wait_times) / n,
            mean_ride_time=sum(ride_times) / n,
            mean_journey_time=sum(journey_times) / n,
            max_wait_time=max(wait_times),
            p90_wait_time=(
                wait_times[0]
                if len(wait_times) == 1
                else statistics.quantiles(
                    wait_times, n=10, method='inclusive'
                )[8]
            ),
            fulfillment_rate=fulfillment_rate,
            served_fraction=served_fraction,
            throughput=throughput,
        )
