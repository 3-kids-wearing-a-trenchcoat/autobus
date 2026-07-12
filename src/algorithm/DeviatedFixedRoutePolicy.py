"""Fixed-route variant that allows bounded detours off the skip-stop backbone."""

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.RequestPool import Request
from src.algorithm.SkipStopFixedRoutePolicy import SkipStopFixedRoutePolicy
from src.map.TransitGraph import TransitGraph

# A deviation is allowed only if its extra travel cost is within this many times
# the direct hop to the next backbone stop, minus one (i.e. detour_factor=1.0
# permits only zero-cost, on-the-way stops, reducing the policy to skip-stop).
_DEFAULT_DETOUR_FACTOR = 1.5


class DeviatedFixedRoutePolicy(SkipStopFixedRoutePolicy):
    """
    A skip-stop fixed route that additionally takes detours to serve nearby
    demand out of cycle order.

    Plain SkipStopFixedRoutePolicy preserves the cycle order strictly, which
    bounds the worst-case ride at one cycle but makes passengers wait for the
    bus to come around in order. This policy keeps that backbone but, before
    heading to the next demanded backbone stop, may divert to a currently
    demanded stop elsewhere if doing so barely lengthens the current hop. That
    grabs geographically near out-of-order demand, cutting wait and would-be
    cancellations.

    A stop ``S`` is a valid deviation from the current position ``pos`` toward
    the next backbone stop ``B`` when its extra cost stays within a bound::

        extra(S) = cost(pos, S) + cost(S, B) - cost(pos, B)
        allowed  = max_detour_cost           if given
                   else (detour_factor - 1) * cost(pos, B)

    The cheapest valid deviation (smallest ``extra``) is served first, then the
    scan repeats with ``B`` still pending. ``detour_factor = 1.0`` (or
    ``max_detour_cost = 0``) reduces the policy exactly to skip-stop.

    CAVEAT — this does NOT preserve skip-stop's ride bound. The cap is measured
    against the hop to the *next* backbone stop (which can be large) and
    deviations chain over the whole demand set, so when backbone stops are far
    apart the policy admits large, repeated detours and a rider's ride can grow
    well beyond one cycle. Empirically (campus-scale sweep, 2026-07) its ride
    time collapsed toward the greedy NearestUnservedPolicy rather than staying
    near the cycle bound. Treat ``detour_factor`` as an exploratory knob, not a
    ride guarantee; genuinely capping ride would require tracking each onboard
    rider's accumulated ride explicitly.
    """

    def __init__(
        self,
        graph: TransitGraph,
        stop_order: list[str] | None = None,
        max_detour_cost: float | None = None,
        detour_factor: float = _DEFAULT_DETOUR_FACTOR,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param stop_order: Ordered list of node IDs defining the fixed cycle.
            If None, a cycle is auto-generated from all stops in the graph.
        :param max_detour_cost: Explicit absolute cap on a deviation's extra
            cost. When None, the cap is relative: (detour_factor - 1) times the
            direct hop to the next backbone stop.
        :param detour_factor: Multiplier bounding the relative detour when
            max_detour_cost is None. 1.0 permits no detour (equals skip-stop).
        """
        super().__init__(graph, stop_order=stop_order)
        self._max_detour_cost = max_detour_cost
        self._detour_factor = detour_factor

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Return the next n_stops stops: the skip-stop backbone with detours to
        nearby out-of-order demand inserted along the way.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of stop node IDs to visit next.
        """
        if n_stops == 0:
            return []

        start = self._first_stop_index(state.position)
        sim_onboard = list(state.onboard)
        sim_pending = list(state.pending_requests)
        sim_capacity = state.remaining_capacity

        position = state.position
        route: list[str] = []
        offset = 0
        while len(route) < n_stops and offset < len(self._stops):
            backbone_stop, offset = self._next_backbone_stop(
                start, offset, sim_onboard, sim_pending, sim_capacity
            )
            if backbone_stop is None:
                break

            deviation = self._best_deviation(
                position, backbone_stop,
                sim_onboard, sim_pending, sim_capacity,
            )
            target = deviation if deviation is not None else backbone_stop

            route.append(target)
            sim_onboard, sim_pending, sim_capacity = self._simulate_stop(
                target, sim_onboard, sim_pending, sim_capacity
            )
            position = target
            # Only advance past the backbone stop once it is actually served; a
            # deviation leaves it pending for the next iteration.
            if target == backbone_stop:
                offset += 1

        return route

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _next_backbone_stop(
        self,
        start: int,
        offset: int,
        onboard: list[Request],
        pending: list[Request],
        capacity: int,
    ) -> tuple[str | None, int]:
        """
        Scan the cycle forward for the next stop with demand.

        :param start: Cycle index the walk began from.
        :param offset: Offset from ``start`` to resume scanning at.
        :param onboard: Projected onboard passengers.
        :param pending: Projected pending passengers.
        :param capacity: Projected remaining capacity.
        :return: (backbone stop node ID, its offset), or (None, offset) when no
            demanded stop remains in this one-pass scan.
        """
        while offset < len(self._stops):
            stop = self._stops[(start + offset) % len(self._stops)]
            if self._stop_has_demand(stop, onboard, pending, capacity):
                return stop, offset
            offset += 1
        return None, offset

    def _best_deviation(
        self,
        position: str,
        backbone_stop: str,
        onboard: list[Request],
        pending: list[Request],
        capacity: int,
    ) -> str | None:
        """
        Find the cheapest demanded stop worth diverting to before the backbone.

        Considers every currently demanded stop other than the backbone target
        and returns the one with the smallest extra cost, provided that extra
        stays within the detour bound. Returns None when no deviation qualifies.
        :param position: Current position node ID.
        :param backbone_stop: The next demanded backbone stop the bus would
            otherwise head to.
        :param onboard: Projected onboard passengers.
        :param pending: Projected pending passengers.
        :param capacity: Projected remaining capacity.
        :return: The node ID to divert to, or None to stay on the backbone.
        """
        direct = self._graph.best_path_cost(position, backbone_stop)
        allowed = (
            self._max_detour_cost
            if self._max_detour_cost is not None
            else (self._detour_factor - 1.0) * direct
        )

        candidates = {r.dropoff_stop_id for r in onboard}
        candidates |= {r.pickup_stop_id for r in pending}
        candidates.discard(backbone_stop)

        best_stop: str | None = None
        best_extra = float("inf")
        for stop in candidates:
            if not self._stop_has_demand(stop, onboard, pending, capacity):
                continue
            extra = (
                self._graph.best_path_cost(position, stop)
                + self._graph.best_path_cost(stop, backbone_stop)
                - direct
            )
            # Strict inequality so detour_factor=1.0 (allowed=0) — and
            # max_detour_cost=0 — take no deviation at all, reducing the policy
            # exactly to skip-stop rather than reordering zero-extra stops.
            if extra < allowed and extra < best_extra:
                best_extra = extra
                best_stop = stop
        return best_stop
