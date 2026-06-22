"""Capacity-gated greedy nearest-unserved-stop dispatch policy."""

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import Request


class NearestUnservedPolicy(DispatchPolicy):
    """
    A demand-responsive policy that greedily routes to the nearest stop
    with active demand, gated by bus capacity.

    At each planning step the policy considers two sets of candidates:

    - Dropoff stops for onboard passengers (always eligible).
    - Pickup stops for pending passengers (only when remaining_capacity > 0
      — the capacity gate).

    The nearest candidate by shortest-path travel time is chosen, the
    simulation state is advanced (alight then board), and the process
    repeats for up to n_stops steps. Planning stops early if no active
    demand remains.
    """

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Compute a greedy capacity-gated route from the given bus state.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of stop IDs to visit; may be shorter than
            n_stops if active demand is exhausted before that many steps.
        """
        sim_position = state.position
        sim_onboard = list(state.onboard)
        sim_pending = list(state.pending_requests)
        sim_remaining_capacity = state.remaining_capacity

        route: list[str] = []
        for _ in range(n_stops):
            candidates = self._candidates(
                sim_onboard, sim_pending, sim_remaining_capacity
            )
            if not candidates:
                break
            chosen = min(
                candidates,
                key=lambda s: self._graph.best_path_cost(sim_position, s),
            )
            route.append(chosen)
            sim_onboard, sim_pending, sim_remaining_capacity = (
                self._simulate_stop(
                    chosen, sim_onboard, sim_pending,
                    sim_remaining_capacity,
                )
            )
            sim_position = chosen

        return route

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _candidates(
        self,
        sim_onboard: list[Request],
        sim_pending: list[Request],
        sim_remaining_capacity: int,
    ) -> set[str]:
        """
        Build the set of candidate stops for the next routing step.
        :param sim_onboard: Simulated onboard passengers.
        :param sim_pending: Simulated pending passengers.
        :param sim_remaining_capacity: Simulated remaining seat capacity.
        :return: Set of candidate stop IDs.
        """
        dropoff_candidates = {r.dropoff_stop_id for r in sim_onboard}
        if sim_remaining_capacity > 0:
            pickup_candidates = {r.pickup_stop_id for r in sim_pending}
        else:
            pickup_candidates = set()
        return dropoff_candidates | pickup_candidates

    def _simulate_stop(
        self,
        stop_id: str,
        sim_onboard: list[Request],
        sim_pending: list[Request],
        sim_remaining_capacity: int,
    ) -> tuple[list[Request], list[Request], int]:
        """
        Advance the simulation state after visiting a stop.

        Passengers whose dropoff matches stop_id alight first (freeing
        capacity), then pending passengers whose pickup matches stop_id
        board up to the available capacity.
        :param stop_id: The stop being visited.
        :param sim_onboard: Simulated onboard passengers before the stop.
        :param sim_pending: Simulated pending passengers before the stop.
        :param sim_remaining_capacity: Remaining capacity before the stop.
        :return: Updated (sim_onboard, sim_pending, sim_remaining_capacity).
        """
        # Alight
        alighting = [r for r in sim_onboard if r.dropoff_stop_id == stop_id]
        sim_onboard = [
            r for r in sim_onboard if r.dropoff_stop_id != stop_id
        ]
        sim_remaining_capacity += len(alighting)

        # Board
        boarding: list[Request] = []
        remaining_pending: list[Request] = []
        for r in sim_pending:
            if (r.pickup_stop_id == stop_id
                    and sim_remaining_capacity > 0):
                boarding.append(r)
                sim_remaining_capacity -= 1
            else:
                remaining_pending.append(r)

        return (
            sim_onboard + boarding,
            remaining_pending,
            sim_remaining_capacity,
        )
