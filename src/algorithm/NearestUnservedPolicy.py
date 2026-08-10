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

    Progress guarantee: whenever the bus has free capacity and pending
    demand exists, at least one candidate is always produced (see
    ``_candidates``). This is a no-op for the base policy but prevents
    filtering subclasses from stranding an idle bus.
    """

    def params(self) -> dict[str, object]:
        """Report no settings; this policy is pure nearest-first greed."""
        return super().params()

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
                sim_position, sim_onboard, sim_pending,
                sim_remaining_capacity,
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
        sim_position: str,
        sim_onboard: list[Request],
        sim_pending: list[Request],
        sim_remaining_capacity: int,
    ) -> set[str]:
        """
        Build the set of candidate stops for the next routing step.
        :param sim_position: Simulated current position (node ID), used to
            evaluate pickup eligibility.
        :param sim_onboard: Simulated onboard passengers.
        :param sim_pending: Simulated pending passengers.
        :param sim_remaining_capacity: Simulated remaining seat capacity.
        :return: Set of candidate stop IDs.
        """
        dropoff_candidates = {r.dropoff_stop_id for r in sim_onboard}
        if sim_remaining_capacity > 0:
            pickup_candidates = {
                r.pickup_stop_id for r in sim_pending
                if self._pickup_eligible(r.pickup_stop_id, sim_position)
            }
        else:
            pickup_candidates = set()

        candidates = dropoff_candidates | pickup_candidates
        if not candidates and sim_remaining_capacity > 0 and sim_pending:
            # Progress guarantee: never strand an idle bus while serveable
            # demand exists. When _pickup_eligible filters out every pending
            # pickup (a subclass hook; always False here), the empty route
            # would keep the bus stationary, so its position never changes and
            # nothing ever becomes eligible — a permanent deadlock. Fall back
            # to the nearest pending pickup so the bus keeps moving.
            nearest = min(
                sim_pending,
                key=lambda r: self._graph.best_path_cost(
                    sim_position, r.pickup_stop_id
                ),
            )
            candidates = {nearest.pickup_stop_id}
        return candidates

    def _pickup_eligible(self, pickup_stop: str, position: str) -> bool:
        """
        Decide whether a pending pickup is worth considering as a candidate.

        The base policy accepts every pickup; subclasses may override this to
        filter pickups (e.g. by travel cost). Onboard dropoffs are never
        subject to this hook.
        :param pickup_stop: Candidate pickup stop node ID.
        :param position: Simulated current position node ID.
        :return: True if the pickup should be a candidate, False otherwise.
        """
        return True
