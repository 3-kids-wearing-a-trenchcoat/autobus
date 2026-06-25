"""AnimationRecorder: captures per-stop-arrival snapshots for animation."""

from dataclasses import dataclass

from src.algorithm.RequestPool import RequestPool


@dataclass
class AnimationFrame:
    """
    Snapshot of simulation state at the moment the bus arrives at a stop.

    :param sim_time: Simulation time in seconds at the moment of arrival.
    :param bus_stop_id: TransitGraph node ID the bus just reached.
    :param prev_stop_id: Node ID the bus came from (used for heading arrow);
        None on the very first frame.
    :param pending_counts: Mapping of stop_id to number of PENDING passengers
        waiting there, captured after boarding has completed.
    :param onboard_count: Total passengers on the bus after boarding/alighting.
    :param dropoff_stop_ids: Set of stop IDs that are dropoff targets for any
        passenger currently on the bus.
    """

    sim_time: float
    bus_stop_id: str
    prev_stop_id: str | None
    pending_counts: dict[str, int]
    onboard_count: int
    dropoff_stop_ids: set[str]


class AnimationRecorder:
    """
    Read-only observer that captures one AnimationFrame each time the bus
    arrives at a stop.  Attach to SimulationBackend before running.

    Follows the same passive-observer pattern as MetricsTracker: it never
    modifies the pool, only queries it.

    :param pool: Shared request pool to query on each arrival.
    """

    def __init__(self, pool: RequestPool) -> None:
        self._pool = pool
        self._frames: list[AnimationFrame] = []
        self._last_stop_id: str | None = None

    def record_arrival(self, stop_id: str, sim_time: float) -> None:
        """
        Capture a frame for the bus's arrival at stop_id.

        Must be called after BusAgent.on_arrival() so that boarded passengers
        are already reflected in the ON_BUS state.

        :param stop_id: TransitGraph node ID of the stop just reached.
        :param sim_time: Simulation time in seconds at the moment of arrival.
        """
        pending = self._pool.get_pending()
        on_bus = self._pool.get_on_bus()

        pending_counts: dict[str, int] = {}
        for r in pending:
            pending_counts[r.pickup_stop_id] = (
                pending_counts.get(r.pickup_stop_id, 0) + 1
            )

        dropoff_stop_ids: set[str] = {r.dropoff_stop_id for r in on_bus}

        self._frames.append(AnimationFrame(
            sim_time=sim_time,
            bus_stop_id=stop_id,
            prev_stop_id=self._last_stop_id,
            pending_counts=pending_counts,
            onboard_count=len(on_bus),
            dropoff_stop_ids=dropoff_stop_ids,
        ))
        self._last_stop_id = stop_id

    @property
    def frames(self) -> list[AnimationFrame]:
        """Return a copy of all captured frames."""
        return list(self._frames)
