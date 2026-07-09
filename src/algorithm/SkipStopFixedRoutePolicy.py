"""Fixed-route policy variant that skips stops with no active demand."""

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.FixedRoutePolicy import FixedRoutePolicy
from src.algorithm.RequestPool import Request


class SkipStopFixedRoutePolicy(FixedRoutePolicy):
    """
    A fixed-route policy that follows the same predetermined cycle order as
    FixedRoutePolicy but skips stops with no boarding or alighting demand.

    Because the cycle order is preserved, the worst-case wait remains bounded
    by one full cycle (as in a plain fixed route), while skipping empty stops
    shortens cycles when demand is sparse. Boarding is capacity-gated: a
    pickup-only stop is skipped while the bus is projected to be full.
    """

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Return the next n_stops demanded stops in the fixed cycle.

        Walks the fixed cycle from the current position for at most one full
        pass, keeping only stops where a passenger would board or alight
        given the projected bus state. Returns fewer than n_stops stops when
        demand is sparse, or an empty list when there is no demand at all.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of demanded stop node IDs to visit next.
        """
        if n_stops == 0:
            return []

        start = self._first_stop_index(state.position)
        sim_onboard = list(state.onboard)
        sim_pending = list(state.pending_requests)
        sim_capacity = state.remaining_capacity

        route: list[str] = []
        for offset in range(len(self._stops)):
            stop = self._stops[(start + offset) % len(self._stops)]
            if not self._stop_has_demand(
                stop, sim_onboard, sim_pending, sim_capacity
            ):
                continue
            route.append(stop)
            sim_onboard, sim_pending, sim_capacity = self._simulate_stop(
                stop, sim_onboard, sim_pending, sim_capacity
            )
            if len(route) == n_stops:
                break

        return route

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _stop_has_demand(
        self,
        stop: str,
        onboard: list[Request],
        pending: list[Request],
        capacity: int,
    ) -> bool:
        """
        Decide whether visiting a stop would board or alight any passenger.

        A stop has demand if at least one onboard passenger alights there, or
        a pending passenger picks up there and there is room after alighting
        (the capacity gate).
        :param stop: Candidate stop node ID.
        :param onboard: Projected onboard passengers before the stop.
        :param pending: Projected pending passengers before the stop.
        :param capacity: Projected remaining capacity before the stop.
        :return: True if the stop is worth visiting, False otherwise.
        """
        alighting = [r for r in onboard if r.dropoff_stop_id == stop]
        if alighting:
            return True
        has_pickup = any(r.pickup_stop_id == stop for r in pending)
        return has_pickup and capacity + len(alighting) > 0
