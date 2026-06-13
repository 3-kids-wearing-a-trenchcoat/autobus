"""Tests for TransitGraph constructor behaviour."""

import unittest

import networkx as nx

from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord, RoadEdgeRecord, StopRecord


# Hand-authored test map (all edges directed, lengths in metres):
#
#   n4 (0,3) <─────────── n3 (4,3)
#      │      (len 4.0)       ^
#      │ (len 3.0)    (len 5.0)│ (len 3.0)
#      v               ╱      │
#   n1 (0,0) ─────────── n2 (4,0)
#                (len 4.0)
#
#   Diagonal n1→n3: sqrt(4²+3²) = 5.0  (3–4–5 Pythagorean triple)
#
#   Stops (coordinates auto-computed from edge + pos_on_edge):
#     s1 at 0.5 along n1→n2  →  (2.0, 0.0)
#     s2 at 0.5 along n3→n4  →  (2.0, 3.0)

_PROVIDER = StaticMapProvider(
    stops=[
        StopInput(id="s1", from_node_id="n1", to_node_id="n2", pos_on_edge=0.5),
        StopInput(id="s2", from_node_id="n3", to_node_id="n4", pos_on_edge=0.5),
    ],
    nodes=[
        RoadNodeRecord(id="n1", x=0.0, y=0.0),
        RoadNodeRecord(id="n2", x=4.0, y=0.0),
        RoadNodeRecord(id="n3", x=4.0, y=3.0),
        RoadNodeRecord(id="n4", x=0.0, y=3.0),
    ],
    edges=[
        ("n1", "n2"),  # length 4.0
        ("n2", "n3"),  # length 3.0
        ("n3", "n4"),  # length 4.0
        ("n4", "n1"),  # length 3.0
        ("n1", "n3"),  # length 5.0  (diagonal)
    ],
)


class TestTransitGraphConstructor(unittest.TestCase):
    """Verify that TransitGraph builds the correct graph for a known input."""

    def setUp(self) -> None:
        from src.map.TransitGraph import TransitGraph
        self.graph = TransitGraph(_PROVIDER)

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


if __name__ == "__main__":
    unittest.main()
