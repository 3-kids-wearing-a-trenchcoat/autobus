"""Greedy dispatch policy that caps onboard ride time by forcing deliveries."""

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.RequestPool import Request
from src.map.TransitGraph import TransitGraph

# A rider's along-route ride is judged against this many times the direct
# pickup-to-dropoff cost, unless an absolute max_ride_cost is supplied instead.
_DEFAULT_RIDE_FACTOR = 1.5


class RideBoundedGreedyPolicy(NearestUnservedPolicy):
    """
    A demand-responsive policy that extends NearestUnservedPolicy by capping
    how far an onboard rider may be detoured before the bus must deliver them.

    Plain nearest-unserved always heads for the closest candidate, so under
    dense demand there is always a nearer pickup and onboard riders get dragged
    around indefinitely (an unbounded ride tail). This policy adds a hard
    ride-slack gate: whenever serving another pickup would leave any onboard
    rider unable to still reach their dropoff within their ride budget, pickups
    are disabled for that step and the bus heads to a dropoff instead.

    The gate only reorders — it never rejects. A deferred pickup stays PENDING
    and is served on a later step (or a later plan) once riders have slack
    again, so coverage matches the base greedy policy while the ride tail is
    bounded.

    Each rider's budget is either an explicit absolute cap (max_ride_cost) or
    ride_factor times the direct pickup-to-dropoff cost. Ride is measured as
    along-route distance accrued within the current plan; the pre-plan portion
    of an already-onboard rider's trip is not visible here (sim_time is not in
    the plan() interface), but frequent re-planning re-checks the bound each
    time the bus reaches a committed stop.
    """

    def __init__(
        self,
        graph: TransitGraph,
        max_ride_cost: float | None = None,
        ride_factor: float = _DEFAULT_RIDE_FACTOR,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param max_ride_cost: Explicit absolute along-route ride budget applied
            to every rider. When None, each rider's budget is derived from their
            own trip as ride_factor times the direct pickup-to-dropoff cost.
        :param ride_factor: Multiplier on each rider's direct pickup-to-dropoff
            cost, used when max_ride_cost is None.
        """
        super().__init__(graph)
        self._max_ride_cost = max_ride_cost
        self._ride_factor = ride_factor

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Compute a greedy route that caps each onboard rider's along-route ride.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of stop IDs to visit; may be shorter than
            n_stops if active demand is exhausted before that many steps.
        """
        sim_position = state.position
        sim_onboard = list(state.onboard)
        sim_pending = list(state.pending_requests)
        sim_remaining_capacity = state.remaining_capacity

        # Along-route distance each onboard rider has accrued within this plan.
        # Riders already aboard at plan start begin at 0 (their pre-plan ride is
        # not observable here); riders picked up mid-plan are initialised on the
        # step they board, so their accrual starts from that point.
        ride_so_far: dict[str, float] = {r.id: 0.0 for r in sim_onboard}

        route: list[str] = []
        for _ in range(n_stops):
            candidates = self._gated_candidates(
                sim_position, sim_onboard, sim_pending,
                sim_remaining_capacity, ride_so_far,
            )
            if not candidates:
                break
            chosen = min(
                candidates,
                key=lambda s: self._graph.best_path_cost(sim_position, s),
            )
            route.append(chosen)

            # Every rider aboard for this leg accrues its travel cost before the
            # boarding/alighting at the chosen stop is applied.
            step_cost = self._graph.best_path_cost(sim_position, chosen)
            for rider in sim_onboard:
                ride_so_far[rider.id] += step_cost

            sim_onboard, sim_pending, sim_remaining_capacity = (
                self._simulate_stop(
                    chosen, sim_onboard, sim_pending,
                    sim_remaining_capacity,
                )
            )
            # Newly boarded riders start their ride accrual from this stop.
            for rider in sim_onboard:
                ride_so_far.setdefault(rider.id, 0.0)
            sim_position = chosen

        return route

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _gated_candidates(
        self,
        sim_position: str,
        sim_onboard: list[Request],
        sim_pending: list[Request],
        sim_remaining_capacity: int,
        ride_so_far: dict[str, float],
    ) -> set[str]:
        """
        Candidate stops for the next step, with the ride-slack gate applied.

        When any onboard rider has no slack left (serving another pickup could
        push their along-route ride past their budget), the candidates are
        restricted to onboard dropoffs so the bus makes delivery progress.
        Otherwise the base policy's candidates (dropoffs plus eligible pickups,
        with its progress guarantee) are used unchanged.
        :param sim_position: Simulated current position (node ID).
        :param sim_onboard: Simulated onboard passengers.
        :param sim_pending: Simulated pending passengers.
        :param sim_remaining_capacity: Simulated remaining seat capacity.
        :param ride_so_far: Per-rider along-route ride accrued so far this plan.
        :return: Set of candidate stop IDs.
        """
        if sim_onboard and self._gate_triggered(
            sim_position, sim_onboard, ride_so_far
        ):
            return {r.dropoff_stop_id for r in sim_onboard}
        return self._candidates(
            sim_position, sim_onboard, sim_pending, sim_remaining_capacity,
        )

    def _gate_triggered(
        self,
        sim_position: str,
        sim_onboard: list[Request],
        ride_so_far: dict[str, float],
    ) -> bool:
        """
        Decide whether the ride-slack gate should fire.

        The gate fires when at least one onboard rider has non-positive slack:
        even heading straight to their dropoff now would meet or exceed their
        ride budget, so no further pickup detour can be afforded.
        :param sim_position: Simulated current position (node ID).
        :param sim_onboard: Simulated onboard passengers.
        :param ride_so_far: Per-rider along-route ride accrued so far this plan.
        :return: True if pickups must be suspended in favour of deliveries.
        """
        for rider in sim_onboard:
            direct_to_dropoff = self._graph.best_path_cost(
                sim_position, rider.dropoff_stop_id
            )
            projected = ride_so_far[rider.id] + direct_to_dropoff
            if projected >= self._budget(rider):
                return True
        return False

    def _budget(self, rider: Request) -> float:
        """
        Along-route ride budget for a rider.
        :param rider: The onboard request whose budget is needed.
        :return: The absolute max_ride_cost when set, otherwise ride_factor
            times the direct pickup-to-dropoff cost.
        """
        if self._max_ride_cost is not None:
            return self._max_ride_cost
        return self._ride_factor * self._graph.best_path_cost(
            rider.pickup_stop_id, rider.dropoff_stop_id
        )
