"""StaticMapProvider: a hand-authored MapProvider for testing and debugging."""

import math
from dataclasses import dataclass
from typing import Optional

from map.MapProvider import MapProvider
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord


@dataclass(frozen=True)
class StopInput:
    """Input specification for a bus stop in StaticMapProvider.

    The stop's (x, y) coordinates are derived automatically from the edge
    endpoints and ``pos_on_edge``, so only connectivity and position need to
    be provided.
    """

    id: str
    from_node_id: str
    to_node_id: str
    pos_on_edge: float
    """Fractional position along the edge: 0.0 = at from-node, 1.0 = at to-node."""
    name: Optional[str] = None


class StaticMapProvider(MapProvider):
    """MapProvider backed by hand-authored data.

    Accepts road nodes as records and directed edges as ``(from_id, to_id)``
    string pairs.  Edge lengths are computed automatically from node
    coordinates using Euclidean distance.  Stops are specified via
    :class:`StopInput`; their (x, y) coordinates are interpolated from the
    edge endpoints and ``pos_on_edge``.  All z-values are fixed at ``0.0``.

    Intended for unit tests and debugging.

    Example::

        provider = StaticMapProvider(
            stops=[
                StopInput(id="s1", from_node_id="n1", to_node_id="n2",
                          pos_on_edge=0.5),
            ],
            nodes=[
                RoadNodeRecord(id="n1", x=0.0, y=0.0),
                RoadNodeRecord(id="n2", x=4.0, y=0.0),
            ],
            edges=[("n1", "n2")],
        )
    """

    def __init__(
        self,
        stops: list[StopInput],
        nodes: list[RoadNodeRecord],
        edges: list[tuple[str, str]],
    ) -> None:
        """
        Args:
            stops: Bus stop specifications.  Coordinates are derived from
                the referenced edge and ``pos_on_edge``.
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
        """Return all bus stops with auto-computed positions and edge references."""
        result = []
        for stop in self._stops:
            a = self._node_index[stop.from_node_id]
            b = self._node_index[stop.to_node_id]
            length = math.sqrt((b.x - a.x) ** 2 + (b.y - a.y) ** 2)
            edge = RoadEdgeRecord(
                id=f"{stop.from_node_id}_{stop.to_node_id}",
                from_node_id=stop.from_node_id,
                to_node_id=stop.to_node_id,
                length_m=length,
            )
            x = a.x + stop.pos_on_edge * (b.x - a.x)
            y = a.y + stop.pos_on_edge * (b.y - a.y)
            result.append(StopRecord(
                id=stop.id,
                x=x,
                y=y,
                edge=edge,
                pos_on_edge=stop.pos_on_edge,
                name=stop.name,
            ))
        return result

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
