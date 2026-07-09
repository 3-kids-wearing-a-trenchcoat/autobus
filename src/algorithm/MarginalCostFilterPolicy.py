"""Greedy dispatch policy that skips pickups whose diversion cost is too high."""

import statistics

from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.map.TransitGraph import TransitGraph

# A pending pickup is considered worth serving if its travel cost is within
# this many times the typical inter-stop spacing (median nearest-neighbor
# stop cost). Tunable via the cost_factor constructor argument.
_DEFAULT_COST_FACTOR = 2.0


class MarginalCostFilterPolicy(NearestUnservedPolicy):
    """
    A demand-responsive policy that extends NearestUnservedPolicy by skipping
    pending pickups whose travel cost from the current position exceeds a
    threshold. Skipped requests stay PENDING — left for a later plan or another
    bus (the soft-skip model). Onboard dropoffs are committed obligations and
    are never filtered.

    The threshold is either supplied explicitly (max_pickup_cost) or derived
    from the graph as cost_factor times the median nearest-neighbor stop cost.
    """

    def __init__(
        self,
        graph: TransitGraph,
        max_pickup_cost: float | None = None,
        cost_factor: float = _DEFAULT_COST_FACTOR,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param max_pickup_cost: Explicit maximum pickup travel cost. When None,
            the threshold is auto-derived from the graph.
        :param cost_factor: Multiplier applied to the graph-derived baseline
            when max_pickup_cost is None.
        :raises ValueError: If max_pickup_cost is None and the graph has fewer
            than two stops (the baseline cannot be computed).
        """
        super().__init__(graph)
        if max_pickup_cost is not None:
            self._threshold = max_pickup_cost
        else:
            self._threshold = cost_factor * self._baseline_stop_cost()

    def _pickup_eligible(self, pickup_stop: str, position: str) -> bool:
        """
        Accept a pickup only if reaching it costs no more than the threshold.
        :param pickup_stop: Candidate pickup stop node ID.
        :param position: Simulated current position node ID.
        :return: True if the pickup is within the cost threshold.
        """
        return self._graph.best_path_cost(position, pickup_stop) <= (
            self._threshold
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _baseline_stop_cost(self) -> float:
        """
        Compute the median nearest-neighbor travel cost between stops.

        For each stop, the minimum best_path_cost to any other stop is taken;
        the median of those per-stop minima is returned as a scale-free proxy
        for typical inter-stop spacing.
        :return: Median nearest-neighbor stop cost.
        :raises ValueError: If the graph has fewer than two stops.
        """
        stop_ids = [s.edge.id for s in self._graph.get_stops()]
        if len(stop_ids) < 2:
            raise ValueError(
                "Cannot auto-derive a pickup cost threshold: the graph has "
                "fewer than two stops. Pass max_pickup_cost explicitly."
            )
        nearest_costs = [
            min(
                self._graph.best_path_cost(origin, other)
                for other in stop_ids
                if other != origin
            )
            for origin in stop_ids
        ]
        return statistics.median(nearest_costs)
