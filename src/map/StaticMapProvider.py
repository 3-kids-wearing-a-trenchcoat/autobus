"""StaticMapProvider: a hand-authored MapProvider for testing and debugging."""

import math

from map.MapProvider import MapProvider
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord


class StaticMapProvider(MapProvider):
    """MapProvider backed by hand-authored data.

    Accepts stops and road nodes as records and directed edges as
    ``(from_id, to_id)`` string pairs.  Edge lengths are computed
    automatically from node coordinates using Euclidean distance, so
    callers never have to calculate lengths by hand.  All z-values are
    fixed at ``0.0``.

    Intended for unit tests and debugging.

    Example::

        provider = StaticMapProvider(
            stops=[StopRecord(id="s1", x=2.0, y=0.0)],
            nodes=[
                RoadNodeRecord(id="n1", x=0.0, y=0.0),
                RoadNodeRecord(id="n2", x=4.0, y=0.0),
            ],
            edges=[("n1", "n2")],
        )
    """

    def __init__(
        self,
        stops: list[StopRecord],
        nodes: list[RoadNodeRecord],
        edges: list[tuple[str, str]],
    ) -> None:
        """
        Args:
            stops: Bus stop records.
            nodes: Road node records (z is ignored; always treated as 0.0).
            edges: Directed edges as ``(from_node_id, to_node_id)`` pairs.
                   Both IDs must appear in ``nodes``.
        """
        self._stops = stops
        self._nodes = nodes
        self._edges = edges
        self._node_index: dict[str, RoadNodeRecord] = {
            node.id: node for node in nodes
        }

    def get_stops(self) -> list[StopRecord]:
        """Return all bus stops."""
        return list(self._stops)

    def get_road_nodes(self) -> list[RoadNodeRecord]:
        """Return all road nodes."""
        return list(self._nodes)

    def get_road_edges(self) -> list[RoadEdgeRecord]:
        """Return directed road edges with Euclidean lengths auto-computed
        from node coordinates."""
        result = []
        for from_id, to_id in self._edges:
            a = self._node_index[from_id]
            b = self._node_index[to_id]
            length = math.sqrt((b.x - a.x) ** 2 + (b.y - a.y) ** 2)
            result.append(RoadEdgeRecord(
                id=f"{from_id}_{to_id}",
                from_node_id=from_id,
                to_node_id=to_id,
                length_m=length,
            ))
        return result
