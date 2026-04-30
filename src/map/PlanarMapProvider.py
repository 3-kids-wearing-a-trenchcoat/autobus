"""PlanarMapProvider: MapProvider backed by a PlanarStreetGraph."""

from map.MapProvider import MapProvider
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord
from map.graph import PlanarStreetGraph, NodeType


class PlanarMapProvider(MapProvider):
    """MapProvider implementation that draws from a PlanarStreetGraph."""

    def __init__(self, graph: PlanarStreetGraph) -> None:
        """Wrap a PlanarStreetGraph as a MapProvider."""
        self._source = graph

    def get_stops(self) -> list[StopRecord]:
        """Return a StopRecord for every BUS_STOP node."""
        return [
            StopRecord(id=str(node_id), x=data['x'], y=data['y'])
            for node_id, data in self._source._graph.nodes(data=True)
            if data['type'] == NodeType.BUS_STOP
        ]

    def get_road_nodes(self) -> list[RoadNodeRecord]:
        """Return a RoadNodeRecord for every INTERSECTION node."""
        return [
            RoadNodeRecord(id=str(node_id), x=data['x'], y=data['y'])
            for node_id, data in self._source._graph.nodes(data=True)
            if data['type'] == NodeType.INTERSECTION
        ]

    def get_road_edges(self) -> list[RoadEdgeRecord]:
        """Return a RoadEdgeRecord for every directed edge."""
        return [
            RoadEdgeRecord(
                id=f"{u}_{v}",
                from_node_id=str(u),
                to_node_id=str(v),
                length_m=data['distance'],
                speed_limit_kph=data['speed_limit'],
            )
            for u, v, data in self._source._graph.edges(data=True)
        ]
