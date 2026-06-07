"""Passive simulation observer: collects tick snapshots and computes KPIs."""

from dataclasses import dataclass

from src.algorithm.RequestPool import RequestPool


@dataclass
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
    Aggregate KPIs computed from fulfilled requests only.

    In-flight (PENDING or ON_BUS) requests are excluded from all
    calculations. All time values are in simulation seconds.

    :param fulfilled_count: Total number of fulfilled requests.
    :param cancelled_count: Total number of cancelled requests.
    :param mean_wait_time: Average time from created_at to boarded_at.
        None if no requests have been fulfilled yet.
    :param mean_ride_time: Average time from boarded_at to fulfilled_at.
        None if no requests have been fulfilled yet.
    :param mean_journey_time: Average time from created_at to
        fulfilled_at. None if no requests have been fulfilled yet.
    """

    fulfilled_count: int
    cancelled_count: int
    mean_wait_time: float | None
    mean_ride_time: float | None
    mean_journey_time: float | None


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

    def snapshot(self) -> KPISnapshot:
        """
        Compute aggregate KPIs from all fulfilled requests.

        In-flight (PENDING or ON_BUS) requests are excluded. If no
        requests have been fulfilled yet, all means are None.
        :return: KPISnapshot with current aggregate metrics.
        """
        fulfilled = self._pool.get_fulfilled()
        cancelled = self._pool.get_cancelled()

        if not fulfilled:
            return KPISnapshot(
                fulfilled_count=0,
                cancelled_count=len(cancelled),
                mean_wait_time=None,
                mean_ride_time=None,
                mean_journey_time=None,
            )

        wait_times = [r.boarded_at - r.created_at for r in fulfilled]
        ride_times = [r.fulfilled_at - r.boarded_at for r in fulfilled]
        journey_times = [r.fulfilled_at - r.created_at for r in fulfilled]
        n = len(fulfilled)

        return KPISnapshot(
            fulfilled_count=n,
            cancelled_count=len(cancelled),
            mean_wait_time=sum(wait_times) / n,
            mean_ride_time=sum(ride_times) / n,
            mean_journey_time=sum(journey_times) / n,
        )
