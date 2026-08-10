"""Fixed-route dispatch policy: cycles through a predetermined stop sequence."""

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.map.TransitGraph import TransitGraph


class FixedRoutePolicy(DispatchPolicy):
    """
    A policy that follows a fixed, repeating stop sequence regardless of
    passenger demand — analogous to a real-life scheduled bus line.

    The cycle can be supplied explicitly or auto-generated from the graph
    using a nearest-neighbor heuristic.
    """

    def __init__(
        self,
        graph: TransitGraph,
        stop_order: list[str] | None = None,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param stop_order: Ordered list of TransitGraph node IDs defining the
            fixed cycle. If None, a cycle is generated automatically from all
            stops in the graph via a nearest-neighbor heuristic.
        :raises ValueError: If stop_order contains an unknown node ID, or if
            no stops are found in the graph when auto-generating.
        """
        super().__init__(graph)
        self._declared: dict[str, object] = {"stop_order": stop_order}
        if stop_order is not None:
            self._stops = self._validate_stop_order(stop_order)
        else:
            stop_node_ids = [s.edge.id for s in graph.get_stops()]
            if not stop_node_ids:
                raise ValueError(
                    "Cannot auto-generate a fixed route: the graph has no stops."
                )
            self._stops = self._generate_cycle(stop_node_ids)

    def params(self) -> dict[str, object]:
        """
        Report the cycle this policy was given.

        ``None`` means the cycle was generated from the graph, which depends
        on ``PYTHONHASHSEED`` — the store records that in a run's provenance.
        :return: Parameter name to value.
        """
        return {**super().params(), **self._declared}

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Return the next n_stops stops in the fixed cycle.

        Passenger demand (onboard, pending) is ignored — the bus follows its
        schedule unconditionally.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Number of stops to include in the route.
        :return: Ordered list of stop node IDs to visit next.
        """
        if n_stops == 0:
            return []
        start = self._first_stop_index(state.position)
        return [
            self._stops[(start + i) % len(self._stops)]
            for i in range(n_stops)
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_stop_order(self, stop_order: list[str]) -> list[str]:
        """
        Check that every ID in stop_order exists in the graph.
        :param stop_order: Caller-supplied list of node IDs.
        :return: The same list, unchanged.
        :raises ValueError: On the first unrecognised node ID.
        """
        for node_id in stop_order:
            if node_id not in self._graph.map:
                raise ValueError(
                    f"stop_order contains unknown node ID: '{node_id}'"
                )
        return list(stop_order)

    def _first_stop_index(self, position: str) -> int:
        """
        Return the cycle index of the first stop the bus should head to next.

        If the bus is currently at a stop that is part of the cycle, return
        the index of the *following* stop (the bus just departed that one).
        Otherwise (e.g. initial position), return the index of the nearest
        cycle stop by travel-time cost.
        :param position: Current TransitGraph node ID of the bus.
        :return: Index into self._stops.
        """
        if position in self._stops:
            current_idx = self._stops.index(position)
            return (current_idx + 1) % len(self._stops)
        # Initial or off-cycle position: head to the nearest stop.
        nearest_idx = min(
            range(len(self._stops)),
            key=lambda i: self._graph.best_path_cost(position, self._stops[i]),
        )
        return nearest_idx

    def _generate_cycle(self, stop_node_ids: list[str]) -> list[str]:
        """
        Build a cycle through all stops using a nearest-neighbor heuristic.

        Starting from the first stop in the list, repeatedly appends the
        closest unvisited stop (by shortest-path travel time) until all stops
        have been visited. O(n²) in the number of stops.
        :param stop_node_ids: TransitGraph node IDs of all stops to include.
        :return: Ordered list of node IDs forming the cycle.
        """
        unvisited = set(stop_node_ids)
        current = stop_node_ids[0]
        cycle = [current]
        unvisited.remove(current)

        while unvisited:
            nearest = min(
                unvisited,
                key=lambda s: self._graph.best_path_cost(current, s),
            )
            cycle.append(nearest)
            unvisited.remove(nearest)
            current = nearest

        return cycle
