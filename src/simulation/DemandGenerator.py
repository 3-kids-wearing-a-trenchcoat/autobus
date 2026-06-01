"""Demand generation: policy interface and DemandGenerator."""

import math
import random
from abc import ABC, abstractmethod

from src.algorithm.RequestPool import Request, RequestPool
from src.map.map_data import StopRecord


class GenerationPolicy(ABC):
    """
    Decides how many new requests to emit on each simulation tick.

    Implementations are responsible for tracking their own state
    (e.g. last-generation time, running total).
    """

    @abstractmethod
    def should_generate(self, sim_time: float) -> int:
        """
        Return the number of requests to generate at this tick.
        :param sim_time: Current simulation time in seconds.
        :return: Number of requests to generate (0 = none).
        """


class PeriodicPolicy(GenerationPolicy):
    """
    Emits one request every ``interval_s`` simulation-seconds,
    up to a total of ``max_requests``.

    :param interval_s: Minimum simulation-time gap between emissions.
    :param max_requests: Total number of requests to emit before stopping.
    """

    def __init__(self, interval_s: float, max_requests: int) -> None:
        self._interval_s = interval_s
        self._max_requests = max_requests
        self._last_time: float = -math.inf
        self._total: int = 0

    def should_generate(self, sim_time: float) -> int:
        """
        Return 1 if the interval has elapsed and the cap hasn't been reached,
        otherwise return 0.
        :param sim_time: Current simulation time in seconds.
        :return: 1 or 0.
        """
        if self._total >= self._max_requests:
            return 0
        if sim_time - self._last_time < self._interval_s:
            return 0
        self._last_time = sim_time
        self._total += 1
        return 1


class DemandGenerator:
    """
    Generates passenger requests and adds them to a ``RequestPool``.

    On each call to ``update``, it asks the ``GenerationPolicy`` how
    many requests to create, then picks stops and adds the requests.

    Stop selection uses the same weight distribution for both pickup
    and dropoff; pickup and dropoff are guaranteed to differ.

    :param pool: The request pool to add new requests to.
    :param policy: Policy that decides when and how many to generate.
    :param stops: Available bus stops to draw from.
    :param weights: Optional per-stop weights for biased selection,
        keyed by stop ID. Stops absent from this dict get weight 0.
        If ``None``, all stops are equally likely.
    :raises ValueError: If fewer than two stops are provided.
    """

    def __init__(
        self,
        pool: RequestPool,
        policy: GenerationPolicy,
        stops: list[StopRecord],
        weights: dict[str, float] | None = None,
    ) -> None:
        if len(stops) < 2:
            raise ValueError(
                "DemandGenerator requires at least two stops."
            )
        self._pool = pool
        self._policy = policy
        self._stops = stops
        self._weights = (
            [weights.get(s.id, 0.0) for s in stops]
            if weights is not None
            else None
        )
        self._next_id: int = 0

    def update(self, sim_time: float) -> None:
        """
        Advance the generator by one tick.
        :param sim_time: Current simulation time in seconds.
        """
        count = self._policy.should_generate(sim_time)
        for _ in range(count):
            self._emit_request()

    def _emit_request(self) -> None:
        """Pick stops, build a Request, and add it to the pool."""
        pickup = random.choices(self._stops, weights=self._weights)[0]

        dropoff_stops = [s for s in self._stops if s.id != pickup.id]
        dropoff_weights = (
            [w for s, w in zip(self._stops, self._weights)
             if s.id != pickup.id]
            if self._weights is not None
            else None
        )
        dropoff = random.choices(dropoff_stops, weights=dropoff_weights)[0]

        request = Request(
            id=f"req_{self._next_id}",
            pickup_stop_id=pickup.id,
            dropoff_stop_id=dropoff.id,
        )
        self._pool.add(request)
        self._next_id += 1
