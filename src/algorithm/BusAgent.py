"""Bus agent: decision-making component that manages state and routing."""

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import Request, RequestPool, RequestState


class BusAgent:
    """
    Manages bus state and coordinates routing decisions.

    On each stop arrival the agent updates passenger states (board/alight),
    then re-plans using a projected future state that skips past the committed
    prefix of the current route.
    """

    def __init__(
        self,
        pool: RequestPool,
        policy: DispatchPolicy,
        capacity: int,
        position: str,
        n_stops: int,
        commitment: int,
    ) -> None:
        """
        :param pool: Shared passenger request pool.
        :param policy: Routing policy used to compute routes.
        :param capacity: Maximum number of passengers the bus can carry.
        :param position: Initial TransitGraph node ID of the bus.
        :param n_stops: Number of stops to plan ahead on each re-plan.
        :param commitment: Rolling window of leading stops locked in during
            re-planning.
        """
        self._pool = pool
        self._policy = policy
        self._capacity = capacity
        self._position = position
        self._n_stops = n_stops
        self._commitment = commitment
        self._route: list[str] = []
        self._onboard: list[Request] = []
        self._replan()

    def on_arrival(self, stop_id: str, sim_time: float) -> None:
        """
        Called by the runner when the bus arrives at a stop.

        Updates passenger states (alight then board), advances the route,
        and triggers a re-plan.
        :param stop_id: TransitGraph node ID of the stop just arrived at.
        :param sim_time: Current simulation time in seconds.
        """
        if self._route and self._route[0] == stop_id:
            self._route.pop(0)
        self._position = stop_id

        self._alight(stop_id, sim_time)
        self._board(stop_id, sim_time)
        self._replan()

    @property
    def position(self) -> str:
        """Current TransitGraph node ID of the bus."""
        return self._position

    def next_stop(self) -> str | None:
        """
        Return the next stop to drive to without triggering a re-plan.
        :return: TransitGraph node ID of the next stop, or None if the route
            is empty.
        """
        return self._route[0] if self._route else None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _alight(self, stop_id: str, sim_time: float) -> None:
        """Drop off all passengers whose destination is stop_id."""
        alighting = [r for r in self._onboard if r.dropoff_stop_id == stop_id]
        for request in alighting:
            request.fulfilled_at = sim_time
            self._pool.update_state(request.id, RequestState.FULFILLED)
            self._onboard.remove(request)

    def _board(self, stop_id: str, sim_time: float) -> None:
        """Board pending passengers waiting at stop_id, up to capacity."""
        for request in self._pool.get_pending():
            if len(self._onboard) >= self._capacity:
                break
            if request.pickup_stop_id == stop_id:
                request.boarded_at = sim_time
                self._pool.update_state(request.id, RequestState.ON_BUS)
                self._onboard.append(request)

    def _replan(self) -> None:
        """Re-plan the route, preserving the committed prefix."""
        committed = self._route[:self._commitment]
        projected = self._build_projected_state()
        new_tail = self._policy.plan(projected, self._n_stops)
        self._route = committed + new_tail

    def _build_projected_state(self) -> BusState:
        """
        Build a BusState that reflects the bus after executing the committed
        prefix of the current route, without mutating real state.
        :return: Projected BusState from which the policy will plan the tail.
        """
        sim_position = self._position
        sim_onboard = list(self._onboard)
        sim_boarded_ids: set[str] = set()

        for stop_id in self._route[:self._commitment]:
            sim_onboard = [
                r for r in sim_onboard if r.dropoff_stop_id != stop_id
            ]
            for request in self._pool.get_pending():
                if request.id in sim_boarded_ids:
                    continue
                if request.pickup_stop_id == stop_id:
                    if len(sim_onboard) < self._capacity:
                        sim_onboard.append(request)
                        sim_boarded_ids.add(request.id)
            sim_position = stop_id

        sim_pending = [
            r for r in self._pool.get_pending()
            if r.id not in sim_boarded_ids
        ]
        return BusState(
            position=sim_position,
            onboard=sim_onboard,
            pending_requests=sim_pending,
            remaining_capacity=self._capacity - len(sim_onboard),
        )
