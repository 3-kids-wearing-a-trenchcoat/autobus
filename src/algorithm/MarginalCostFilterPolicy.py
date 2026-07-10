"""Greedy dispatch policy that skips pickups whose diversion cost is too high."""

import statistics
from itertools import permutations

from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.map.TransitGraph import TransitGraph

# A pending pickup is considered worth serving if its travel cost is within
# this many times the typical inter-stop travel cost (the median cost over all
# ordered stop pairs). Tunable via the cost_factor constructor argument.
_DEFAULT_COST_FACTOR = 2.0


class MarginalCostFilterPolicy(NearestUnservedPolicy):
    """
    A demand-responsive policy that extends NearestUnservedPolicy by skipping
    pending pickups whose travel cost from the current position exceeds a
    threshold. Skipped requests stay PENDING — left for a later plan or another
    bus (the soft-skip model). Onboard dropoffs are committed obligations and
    are never filtered.

    The skip is not absolute: via the base policy's progress guarantee, when
    every pending pickup is filtered out and the bus would otherwise idle, the
    nearest pending pickup is served anyway rather than stranding the bus. In
    other words, far pickups are deferred only while nearer work exists.

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
        Compute the median travel cost over all ordered pairs of stops.

        This is the distance the bus typically covers to get from one stop to
        another, which is the scale a pickup diversion should be judged
        against. (Nearest-neighbor spacing, by contrast, only measures how
        tightly stops cluster and badly underestimates the cost of reaching an
        arbitrary pending pickup, starving the filter.)
        :return: Median ordered-pair stop-to-stop cost.
        :raises ValueError: If the graph has fewer than two stops.
        """
        stop_ids = [s.edge.id for s in self._graph.get_stops()]
        if len(stop_ids) < 2:
            raise ValueError(
                "Cannot auto-derive a pickup cost threshold: the graph has "
                "fewer than two stops. Pass max_pickup_cost explicitly."
            )
        pairwise_costs = [
            self._graph.best_path_cost(origin, destination)
            for origin, destination in permutations(stop_ids, 2)
        ]
        return statistics.median(pairwise_costs)
