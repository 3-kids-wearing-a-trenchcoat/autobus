"""
Maps the tests share.

Kept here rather than in whichever test file happened to define one
first: ten files used to import the map below out of
``test_transit_graph``, which made that file impossible to rename or
reorganise without breaking nine others.
"""

from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord

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
SQUARE_MAP = StaticMapProvider(
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

# Four nodes in a line, 1300 m apart. At DEFAULT_SPEED_LIMIT (130 km/h) each
# segment takes exactly 36 s, so distances translate into round numbers.
#
#   n1 ──1300m──► n2 ──1300m──► n3 ──1300m──► n4
#
# TransitGraph nodes are the road edges: "n1_n2", "n2_n3", "n3_n4".
LINE_SEGMENT_S = 36.0

LINE_MAP = StaticMapProvider(
    stops=[
        StopInput("s1", "n1", "n2", 0.5),
        StopInput("s2", "n2", "n3", 0.5),
        StopInput("s3", "n3", "n4", 0.5),
    ],
    nodes=[
        RoadNodeRecord("n1", x=0.0, y=0.0),
        RoadNodeRecord("n2", x=1300.0, y=0.0),
        RoadNodeRecord("n3", x=2600.0, y=0.0),
        RoadNodeRecord("n4", x=3900.0, y=0.0),
    ],
    edges=[("n1", "n2"), ("n2", "n3"), ("n3", "n4")],
)
