"""Cheapest-insertion dispatch policy that rejects out-of-bounds requests."""

import statistics
from itertools import permutations

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import Request
from src.map.TransitGraph import TransitGraph

# Defaults for the auto-derived bounds. The wait bound is wait_factor times the
# median inter-stop travel cost; the ride bound allows any rider a detour of up
# to ride_factor times their direct pickup->dropoff cost. Both are tunable.
_DEFAULT_WAIT_FACTOR = 2.0
_DEFAULT_RIDE_FACTOR = 1.5

# A route event: (node_id, load_delta, owner_id). load_delta is +1 for a pickup
# (a passenger boards) and -1 for a dropoff (a passenger alights); owner_id ties
# the event to the request it serves so per-rider ride cost can be measured.
_Event = tuple[str, int, str]


class ConstrainedInsertionPolicy(DispatchPolicy):
    """
    A demand-responsive policy that builds a route by cheapest pickup+dropoff
    insertion and rejects requests whose best insertion violates a bound.

    Unlike the greedy NearestUnservedPolicy family, each pending request is
    evaluated against the *entire* planned sequence: the policy tries every
    valid (pickup, dropoff) slot pair, keeps the cheapest one that respects
    passenger precedence, bus capacity, and two service bounds, and inserts
    there. A request with no feasible slot is *rejected* — it is left out of
    the route and stays PENDING (the soft-skip model), free to be served by a
    later plan or another bus.

    The two bounds are:

    - Wait bound: the along-route travel cost from the current position to the
      newly served pickup must not exceed ``max_wait_cost``.
    - Ride bound: after the insertion, *every* affected rider's along-route
      pickup->dropoff cost must stay within ``ride_factor`` times their direct
      shortest-path cost. This protects not only the new request but any
      already-planned passenger the insertion would detour (guarding mean ride
      time against excessive ride-sharing).

    Onboard passengers' dropoffs are committed obligations: they seed the route
    and are never rejected, though their remaining leg is still ride-bounded.

    Progress guarantee: if every pending request is rejected and the bus would
    otherwise idle, the nearest pending pickup is served anyway rather than
    stranding the bus (mirrors NearestUnservedPolicy).
    """

    def __init__(
        self,
        graph: TransitGraph,
        max_wait_cost: float | None = None,
        wait_factor: float = _DEFAULT_WAIT_FACTOR,
        ride_factor: float = _DEFAULT_RIDE_FACTOR,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param max_wait_cost: Explicit along-route wait bound (cost from the
            current position to a pickup). When None, it is auto-derived as
            wait_factor times the median inter-stop cost.
        :param wait_factor: Multiplier applied to the graph-derived baseline
            when max_wait_cost is None.
        :param ride_factor: Maximum allowed ratio of any rider's along-route
            pickup->dropoff cost to their direct shortest-path cost.
        :raises ValueError: If max_wait_cost is None and the graph has fewer
            than two stops (the baseline cannot be computed).
        """
        super().__init__(graph)
        self._ride_factor = ride_factor
        self._declared: dict[str, object] = {
            "max_wait_cost": max_wait_cost,
            "wait_factor": wait_factor,
            "ride_factor": ride_factor,
        }
        if max_wait_cost is not None:
            self._max_wait_cost = max_wait_cost
        else:
            self._max_wait_cost = wait_factor * self._baseline_stop_cost()

    def params(self) -> dict[str, object]:
        """Report the wait and ride bounds this policy was given."""
        return {**super().params(), **self._declared}

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Build a route by cheapest insertion, rejecting out-of-bounds requests.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of stop IDs to visit; may be shorter than n_stops
            when demand is exhausted or every remaining request is rejected.
        """
        if n_stops == 0:
            return []

        initial_load = len(state.onboard)
        capacity_total = state.remaining_capacity + initial_load
        events = self._seed_onboard_dropoffs(state)

        # Insert pending requests in FIFO (arrival) order for fairness. Regret /
        # cheapest-first insertion is a natural later refinement.
        for request in sorted(
            state.pending_requests, key=lambda r: r.created_at
        ):
            best = self._best_insertion(
                events, request, state.position, capacity_total, initial_load
            )
            if best is not None:
                events = best  # accepted; otherwise the request is rejected

        route = self._flatten(events, state.position)[:n_stops]
        if not route:
            route = self._progress_fallback(state)[:n_stops]
        return route

    # ------------------------------------------------------------------
    # Route construction
    # ------------------------------------------------------------------

    def _seed_onboard_dropoffs(self, state: BusState) -> list[_Event]:
        """
        Order onboard passengers' dropoffs nearest-neighbor from the position.

        Dropoffs only lower the load, so their ordering never risks a capacity
        violation; nearest-neighbor just gives a cheap sensible starting route.
        :param state: Current bus state snapshot.
        :return: List of (dropoff_node_id, -1, owner_id) events, one per onboard
            passenger.
        """
        remaining = list(state.onboard)
        current = state.position
        ordered: list[_Event] = []
        while remaining:
            nearest = min(
                remaining,
                key=lambda r: self._graph.best_path_cost(
                    current, r.dropoff_stop_id
                ),
            )
            ordered.append((nearest.dropoff_stop_id, -1, nearest.id))
            current = nearest.dropoff_stop_id
            remaining.remove(nearest)
        return ordered

    def _best_insertion(
        self,
        events: list[_Event],
        request: Request,
        position: str,
        capacity_total: int,
        initial_load: int,
    ) -> list[_Event] | None:
        """
        Find the cheapest feasible insertion of a request's pickup and dropoff.

        Tries every (pickup index, dropoff index) pair with the pickup no later
        than the dropoff, discards pairs that break capacity or a bound, and
        returns the events list for the cheapest surviving pair.
        :param events: Current route events.
        :param request: The pending request to insert.
        :param position: Current bus position node ID.
        :param capacity_total: Total seat capacity of the bus.
        :param initial_load: Onboard passenger count before any event (the load
            the route starts at).
        :return: The cheapest feasible events list, or None if the request has
            no feasible slot (it is then rejected).
        """
        pickup: _Event = (request.pickup_stop_id, 1, request.id)
        dropoff: _Event = (request.dropoff_stop_id, -1, request.id)

        best_events: list[_Event] | None = None
        best_cost = float("inf")
        for i in range(len(events) + 1):
            for j in range(i, len(events) + 1):
                candidate = (
                    events[:i] + [pickup] + events[i:j] + [dropoff]
                    + events[j:]
                )
                if not self._capacity_ok(
                    candidate, capacity_total, initial_load
                ):
                    continue
                if not self._wait_ok(candidate, position, i):
                    continue
                if not self._rides_ok(candidate, position):
                    continue
                cost = self._sequence_cost(
                    [position] + [node for node, _, _ in candidate]
                )
                if cost < best_cost:
                    best_cost = cost
                    best_events = candidate
        return best_events

    # ------------------------------------------------------------------
    # Feasibility checks
    # ------------------------------------------------------------------

    def _capacity_ok(
        self, events: list[_Event], capacity_total: int, initial_load: int
    ) -> bool:
        """
        Check that the running onboard load never exceeds capacity.

        The load starts at ``initial_load`` (onboard passengers already occupy
        seats); each pickup event raises it and each dropoff lowers it.
        :param events: Candidate route events.
        :param capacity_total: Total seat capacity of the bus.
        :param initial_load: Onboard passenger count before any event.
        :return: True if the load stays within capacity throughout.
        """
        load = initial_load
        for _, delta, _ in events:
            load += delta
            if load > capacity_total:
                return False
        return True

    def _wait_ok(
        self, events: list[_Event], position: str, pickup_index: int
    ) -> bool:
        """
        Check the along-route wait to the just-inserted pickup.
        :param events: Candidate route events (pickup at ``pickup_index``).
        :param position: Current bus position node ID.
        :param pickup_index: Index of the inserted pickup in events.
        :return: True if the wait bound holds.
        """
        nodes = [node for node, _, _ in events]
        wait_cost = self._sequence_cost(
            [position] + nodes[:pickup_index + 1]
        )
        return wait_cost <= self._max_wait_cost

    def _rides_ok(self, events: list[_Event], position: str) -> bool:
        """
        Check the ride bound for every rider served by the candidate route.

        Each rider's along-route pickup->dropoff cost must stay within
        ride_factor times their direct cost. A pending rider's ride runs from
        their pickup event to their dropoff event; an onboard rider (pickup
        already past) is measured from the current position to their dropoff.
        :param events: Candidate route events.
        :param position: Current bus position node ID.
        :return: True if no rider exceeds the ride bound.
        """
        nodes = [position] + [node for node, _, _ in events]
        # node index in ``nodes`` is the event index + 1 (position is index 0).
        pickup_at: dict[str, int] = {}
        dropoff_at: dict[str, int] = {}
        for event_index, (_, delta, owner) in enumerate(events):
            if delta > 0:
                pickup_at[owner] = event_index + 1
            else:
                dropoff_at[owner] = event_index + 1

        for owner, drop_idx in dropoff_at.items():
            # Onboard riders have no pickup event: their leg starts at position.
            start_idx = pickup_at.get(owner, 0)
            along = self._sequence_cost(nodes[start_idx:drop_idx + 1])
            direct = self._graph.best_path_cost(
                nodes[start_idx], nodes[drop_idx]
            )
            if along > self._ride_factor * direct:
                return False
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _flatten(self, events: list[_Event], position: str) -> list[str]:
        """
        Turn the events into a stop list, dropping degenerate transitions.

        Consecutive duplicate nodes are collapsed and a leading node equal to
        the current position is dropped, since a zero-length self-transition
        would stall the bus (see DispatchPolicy.PlaceholderPolicy).
        :param events: Final route events.
        :param position: Current bus position node ID.
        :return: Ordered list of stop node IDs to visit.
        """
        route: list[str] = []
        previous = position
        for node, _, _ in events:
            if node != previous:
                route.append(node)
                previous = node
        return route

    def _progress_fallback(self, state: BusState) -> list[str]:
        """
        Serve the nearest pending pickup when every request was rejected.

        Without this, an all-rejected plan returns an empty route, freezing the
        bus at its position forever — its position never changes, so nothing
        ever becomes eligible (a permanent deadlock). Mirrors the progress
        guarantee in NearestUnservedPolicy._candidates.
        :param state: Current bus state snapshot.
        :return: A single-stop route to the nearest pending pickup, or empty.
        """
        if state.remaining_capacity <= 0 or not state.pending_requests:
            return []
        nearest = min(
            state.pending_requests,
            key=lambda r: self._graph.best_path_cost(
                state.position, r.pickup_stop_id
            ),
        )
        return [nearest.pickup_stop_id]

    def _sequence_cost(self, nodes: list[str]) -> float:
        """
        Sum the shortest-path cost of each consecutive hop along a node list.
        :param nodes: Ordered node IDs describing a path to walk.
        :return: Total along-route travel cost (0 for zero or one node).
        """
        return sum(
            self._graph.best_path_cost(nodes[i], nodes[i + 1])
            for i in range(len(nodes) - 1)
        )

    def _baseline_stop_cost(self) -> float:
        """
        Compute the median travel cost over all ordered pairs of stops.

        This is the scale a pickup diversion should be judged against.
        (Duplicated from MarginalCostFilterPolicy to avoid touching that policy
        mid-experiment; the two could later share a helper.)
        :return: Median ordered-pair stop-to-stop cost.
        :raises ValueError: If the graph has fewer than two stops.
        """
        stop_ids = [s.edge.id for s in self._graph.get_stops()]
        if len(stop_ids) < 2:
            raise ValueError(
                "Cannot auto-derive a wait bound: the graph has fewer than "
                "two stops. Pass max_wait_cost explicitly."
            )
        pairwise_costs = [
            self._graph.best_path_cost(origin, destination)
            for origin, destination in permutations(stop_ids, 2)
        ]
        return statistics.median(pairwise_costs)
