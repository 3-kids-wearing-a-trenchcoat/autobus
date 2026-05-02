"""Tests for TransitGraph constructor behaviour."""

import unittest

from map.StaticMapProvider import StaticMapProvider, StopInput
from map.map_data import RoadNodeRecord


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
        from map.TransitGraph import TransitGraph
        self.graph = TransitGraph(_PROVIDER)

    # TODO: add assertions once TransitGraph is implemented


if __name__ == "__main__":
    unittest.main()
