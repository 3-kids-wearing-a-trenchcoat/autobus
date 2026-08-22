"""Tests for TransitGraph: how it is built, and how it routes."""

import unittest

import networkx as nx

from src.map.TransitGraph import TransitGraph
from src.map.map_data import RoadEdgeRecord, StopRecord

from maps import SQUARE_MAP


# All edges in SQUARE_MAP use the RoadEdgeRecord default speed limit.
_SPEED_LIMIT_MPS = 130.0 * 1000 / 3600


class TestTransitGraphConstructor(unittest.TestCase):
    """Verify that TransitGraph builds the correct graph for a known input."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def test_map_structure(self) -> None:
        """TransitGraph.map has the correct nodes, attributes, and edges."""
        s1_edge = RoadEdgeRecord(
            id="n1_n2", from_node_id="n1", to_node_id="n2", length_m=4.0
        )
        s1 = StopRecord(
            id="s1", x=2.0, y=0.0, edge=s1_edge, pos_on_edge=0.5
        )
        s2_edge = RoadEdgeRecord(
            id="n3_n4", from_node_id="n3", to_node_id="n4", length_m=4.0
        )
        s2 = StopRecord(
            id="s2", x=2.0, y=3.0, edge=s2_edge, pos_on_edge=0.5
        )

        expected = nx.DiGraph()
        expected.add_node(
            "n1_n2", stop=s1, stop_pos=0.5, stop_name=None,
            length=4.0, speed_limit=None,
            start_node="n1", stop_node="n2", metadata=None,
        )
        expected.add_node(
            "n2_n3", stop=None, stop_pos=0.0, stop_name=None,
            length=3.0, speed_limit=None,
            start_node="n2", stop_node="n3", metadata=None,
        )
        expected.add_node(
            "n3_n4", stop=s2, stop_pos=0.5, stop_name=None,
            length=4.0, speed_limit=None,
            start_node="n3", stop_node="n4", metadata=None,
        )
        expected.add_node(
            "n4_n1", stop=None, stop_pos=0.0, stop_name=None,
            length=3.0, speed_limit=None,
            start_node="n4", stop_node="n1", metadata=None,
        )
        expected.add_node(
            "n1_n3", stop=None, stop_pos=0.0, stop_name=None,
            length=5.0, speed_limit=None,
            start_node="n1", stop_node="n3", metadata=None,
        )

        expected.add_edge("n1_n2", "n2_n3")
        expected.add_edge("n2_n3", "n3_n4")
        expected.add_edge("n3_n4", "n4_n1")
        expected.add_edge("n4_n1", "n1_n2")
        expected.add_edge("n4_n1", "n1_n3")
        expected.add_edge("n1_n3", "n3_n4")

        actual = self.graph.map
        self.assertEqual(set(expected.nodes), set(actual.nodes))
        for node in expected.nodes:
            self.assertEqual(expected.nodes[node], actual.nodes[node])
        self.assertEqual(set(expected.edges), set(actual.edges))


class TestTransitGraphRouting(unittest.TestCase):
    """Verify routing behaviour of TransitGraph on a known hand-authored map.

    The test map (defined in test_transit_graph.py):

        n1_n2 (4 m) → n2_n3 (3 m) → n3_n4 (4 m) → n4_n1 (3 m) → (cycle)
        n4_n1 → n1_n3 (5 m) → n3_n4                               (shortcut)

    All edges share the same speed limit (130 kph), so the cheapest path
    is always the one with the shortest total length in metres.
    """

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    # --- best_path ---

    def test_best_path_only_route(self) -> None:
        """best_path returns the only route when there is no other."""
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
        # Optimal path n4_n1 -> n1_n3 -> n3_n4 traverses 3 m + 5 m at
        # _SPEED_LIMIT_MPS.
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


class TestTransitGraphDistanceCache(unittest.TestCase):
    """Verify that best_path_cost caching preserves values and reuses work."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def test_matches_direct_dijkstra(self) -> None:
        """Cached costs equal a direct networkx Dijkstra for every pair."""
        nodes = list(self.graph.map.nodes)
        for origin in nodes:
            for destination in nodes:
                if origin == destination or not nx.has_path(
                    self.graph.map, origin, destination
                ):
                    continue
                self.assertAlmostEqual(
                    self.graph.best_path_cost(origin, destination),
                    nx.dijkstra_path_length(
                        self.graph.map, origin, destination, weight="weight"
                    ),
                )

    def test_populates_cache_on_first_query(self) -> None:
        """The first query from an origin fills the per-origin cache."""
        self.assertNotIn("n4_n1", self.graph._dist_cache)
        self.graph.best_path_cost("n4_n1", "n3_n4")
        self.assertIn("n4_n1", self.graph._dist_cache)

    def test_reuses_cached_source_for_other_destinations(self) -> None:
        """One cached single-source result serves multiple destinations."""
        self.graph.best_path_cost("n4_n1", "n3_n4")
        # A different destination from the same (now cached) origin still
        # returns the correct value without recomputing a new source.
        self.assertAlmostEqual(
            self.graph.best_path_cost("n4_n1", "n1_n2"),
            nx.dijkstra_path_length(
                self.graph.map, "n4_n1", "n1_n2", weight="weight"
            ),
        )

    def test_unreachable_destination_raises(self) -> None:
        """An unreachable destination raises NetworkXNoPath (as before)."""
        # n2_n3 has no path back to n1_n2 only if disconnected; here every
        # node is reachable, so use a node guaranteed absent from distances by
        # querying across a manually severed copy.
        severed = TransitGraph(SQUARE_MAP)
        severed.map.remove_edge("n4_n1", "n1_n2")
        severed.map.remove_edge("n4_n1", "n1_n3")
        severed._dist_cache.clear()
        with self.assertRaises(nx.NetworkXNoPath):
            severed.best_path_cost("n4_n1", "n1_n2")


if __name__ == "__main__":
    unittest.main()
