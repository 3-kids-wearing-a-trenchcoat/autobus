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

    @abstractmethod
    def is_exhausted(self) -> bool:
        """
        Return whether this policy will ever emit another request.

        The runner needs this to know when a run is genuinely over. Without
        it, an empty pool looks like the end of the run even when more demand
        is still to come, and a bus that happens to finish its only request
        before the next one arrives ends the trial early — reporting perfect
        service for the handful of requests that existed. A policy that
        generates forever answers False forever, which is correct: such a run
        ends on ``max_time`` instead.
        :return: True when no further requests will be emitted.
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

    def is_exhausted(self) -> bool:
        """
        Return whether the request cap has been reached.
        :return: True once ``max_requests`` have been emitted.
        """
        return self._total >= self._max_requests


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
            self._emit_request(sim_time)

    def is_exhausted(self) -> bool:
        """
        Return whether any further requests will be generated.
        :return: True when the policy has no more demand to emit.
        """
        return self._policy.is_exhausted()

    def _emit_request(self, sim_time: float) -> None:
        """
        Pick stops, build a Request, and add it to the pool.
        :param sim_time: Current simulation time in seconds.
        """
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
            created_at=sim_time,
        )
        self._pool.add(request)
        self._next_id += 1
