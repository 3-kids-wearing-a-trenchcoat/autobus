"""Tests for TransitGraph routing methods: best_path, best_path_cost, best_tour."""

import unittest

from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

# All edges in _PROVIDER use the RoadEdgeRecord default speed limit.
_SPEED_LIMIT_MPS = 130.0 * 1000 / 3600


class TestTransitGraphRouting(unittest.TestCase):
    """Verify routing behaviour of TransitGraph on a known hand-authored map.

    The test map (defined in test_transit_graph.py):

        n1_n2 (4 m) → n2_n3 (3 m) → n3_n4 (4 m) → n4_n1 (3 m) → (cycle)
        n4_n1 → n1_n3 (5 m) → n3_n4                               (shortcut)

    All edges share the same speed limit (130 kph), so the cheapest path
    is always the one with the shortest total length in metres.
    """

    def setUp(self) -> None:
        self.graph = TransitGraph(_PROVIDER)

    # --- best_path ---

    def test_best_path_only_route(self) -> None:
        """best_path returns the only available route when no alternatives exist."""
        self.assertEqual(
            self.graph.best_path("n1_n2", "n3_n4"),
            ["n1_n2", "n2_n3", "n3_n4"],
        )

    def test_best_path_prefers_shorter_route(self) -> None:
        """best_path picks the diagonal (8 m) over the cycle (10 m)."""
        self.assertEqual(
            self.graph.best_path("n4_n1", "n3_n4"),
            ["n4_n1", "n1_n3", "n3_n4"],
        )

    # --- best_path_cost ---

    def test_best_path_cost_value(self) -> None:
        """best_path_cost returns the correct numeric travel time."""
        # Optimal path n4_n1 → n1_n3 → n3_n4 traverses 3 m + 5 m at _SPEED_LIMIT_MPS.
        expected = (3.0 + 5.0) / _SPEED_LIMIT_MPS
        self.assertAlmostEqual(
            self.graph.best_path_cost("n4_n1", "n3_n4"),
            expected,
        )

    def test_best_path_cost_consistent_with_best_path(self) -> None:
        """best_path_cost equals the sum of edge weights along best_path.

        Guards against best_path and best_path_cost drifting apart if the
        routing metric is ever changed.
        """
        path = self.graph.best_path("n4_n1", "n3_n4")
        manual_cost = sum(
            self.graph.map[path[i]][path[i + 1]]["weight"]
            for i in range(len(path) - 1)
        )
        self.assertAlmostEqual(
            self.graph.best_path_cost("n4_n1", "n3_n4"),
            manual_cost,
        )

    # --- best_tour ---

    def test_best_tour_reorders_for_optimal_cost(self) -> None:
        """best_tour reorders stops when the input order is suboptimal.

        Visiting n3_n4 first (7 + 4 = 11 m) is cheaper than
        visiting n4_n1 first (11 + 8 = 19 m).
        """
        self.assertEqual(
            self.graph.best_tour("n1_n2", ["n4_n1", "n3_n4"]),
            ["n1_n2", "n3_n4", "n4_n1"],
        )

    def test_best_tour_start_is_always_first(self) -> None:
        """best_tour always places start_id at index 0."""
        result = self.graph.best_tour("n1_n2", ["n3_n4"])
        self.assertEqual(result[0], "n1_n2")

    def test_best_tour_empty_stops(self) -> None:
        """best_tour returns [start_id] when stop_ids is empty."""
        self.assertEqual(
            self.graph.best_tour("n1_n2", []),
            ["n1_n2"],
        )


if __name__ == "__main__":
    unittest.main()
