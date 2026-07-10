"""Abstract simulation backend: time advancement and stop-arrival logic."""

from abc import ABC, abstractmethod

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import RequestPool
from src.metrics.MetricsTracker import MetricsTracker
from src.simulation.AnimationRecorder import AnimationRecorder
from src.simulation.DemandGenerator import DemandGenerator


class SimulationBackend(ABC):
    """
    Abstract controller that receives ticks and advances simulation state.

    Coordinates BusAgent, DemandGenerator, and MetricsTracker in the correct
    per-tick order. Subclasses supply only one method — ``_travel_time`` —
    which encapsulates all environment-specific movement physics.

    :param agent: The bus agent that manages routing and passenger state.
    :param demand_generator: Generates new passenger requests each tick.
    :param metrics_tracker: Passive observer that records per-tick snapshots.
    :param pool: Shared request pool; read here only for ``is_done``.
    :param recorder: Optional animation recorder; when provided, captures a
        frame on each stop arrival. Defaults to None.
    :param max_wait_s: Maximum time a request may stay PENDING before the
        passenger gives up and the request is cancelled. None disables
        abandonment (requests wait indefinitely). Defaults to None.
    """

    def __init__(
        self,
        agent: BusAgent,
        demand_generator: DemandGenerator,
        metrics_tracker: MetricsTracker,
        pool: RequestPool,
        recorder: AnimationRecorder | None = None,
        max_wait_s: float | None = None,
    ) -> None:
        self._agent = agent
        self._demand_generator = demand_generator
        self._metrics_tracker = metrics_tracker
        self._pool = pool
        self._recorder = recorder
        self._max_wait_s = max_wait_s
        self._sim_time: float = 0.0
        self._elapsed_toward_next: float = 0.0
        self._time_to_next_stop: float | None = (
            self._compute_next_travel_time()
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def _travel_time(self, from_id: str, to_id: str) -> float:
        """
        Return the travel time in seconds between two TransitGraph node IDs.
        :param from_id: TransitGraph node ID of the origin stop.
        :param to_id: TransitGraph node ID of the destination stop.
        :return: Travel time in seconds (must be strictly positive).
        """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def step(self, dt: float) -> None:
        """
        Advance the simulation by dt seconds.

        Per-tick order: advance time → generate demand → expire long-waiting
        requests → process movement (firing on_arrival for each stop reached)
        → record metrics snapshot.
        :param dt: Time delta in seconds.
        """
        self._sim_time += dt
        self._demand_generator.update(self._sim_time)
        self._expire_pending()
        self._process_movement(dt)
        self._metrics_tracker.record_tick(self._sim_time)

    def is_done(self) -> bool:
        """Return True when no requests remain pending or on the bus."""
        return (
            len(self._pool.get_pending()) == 0
            and len(self._pool.get_on_bus()) == 0
        )

    @property
    def sim_time(self) -> float:
        """Current simulation time in seconds."""
        return self._sim_time

    @property
    def metrics(self) -> MetricsTracker:
        """The MetricsTracker for reading KPIs."""
        return self._metrics_tracker

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _expire_pending(self) -> None:
        """
        Cancel PENDING requests whose passengers have waited too long.

        A request is abandoned once it has been PENDING for at least
        ``max_wait_s`` seconds (measured from ``created_at``). Onboard
        (ON_BUS) requests are committed and never abandoned. No-op when
        ``max_wait_s`` is None. ``get_pending`` returns a fresh list, so
        cancelling under it is safe.
        """
        if self._max_wait_s is None:
            return
        for request in self._pool.get_pending():
            if self._sim_time - request.created_at >= self._max_wait_s:
                self._pool.cancel(request.id)

    def _compute_next_travel_time(self) -> float | None:
        """Return travel time to the next stop, or None if route is empty."""
        next_stop = self._agent.next_stop()
        if next_stop is None:
            return None
        return self._travel_time(self._agent.position, next_stop)

    def _process_movement(self, dt: float) -> None:
        """Process bus movement, firing on_arrival for each stop reached."""
        remaining = dt
        while remaining > 0.0:
            next_stop = self._agent.next_stop()
            if next_stop is None:
                break
            if self._time_to_next_stop is None:
                self._time_to_next_stop = self._compute_next_travel_time()
                if self._time_to_next_stop is None:
                    break
            time_left = self._time_to_next_stop - self._elapsed_toward_next
            if remaining >= time_left:
                arrival_time = self._sim_time - (remaining - time_left)
                remaining -= time_left
                self._elapsed_toward_next = 0.0
                self._agent.on_arrival(next_stop, arrival_time)
                if self._recorder is not None:
                    self._recorder.record_arrival(next_stop, arrival_time)
                self._time_to_next_stop = self._compute_next_travel_time()
            else:
                self._elapsed_toward_next += remaining
                remaining = 0.0
