"""PlanarMapProvider: MapProvider backed by a PlanarStreetGraph."""

from map.MapProvider import MapProvider
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord
from map.graph import PlanarStreetGraph


class PlanarMapProvider(MapProvider):
    """MapProvider implementation that draws from a PlanarStreetGraph."""

    def __init__(self, graph: PlanarStreetGraph) -> None:
        """Wrap a PlanarStreetGraph as a MapProvider."""
        self._source = graph

    def get_stops(self) -> list[StopRecord]:
        """Return a StopRecord for every bus stop in the graph.

        Bus stops are stored as metadata on edges.  A stop on a two-way road
        produces two records — one per directed edge — with IDs of the form
        ``"{stop_id}_{from_node_id}_{to_node_id}"``.
        """
        g = self._source._graph
        result = []
        for u, v, data in g.edges(data=True):
            stop = data.get('stop')
            if stop is None:
                continue
            edge = RoadEdgeRecord(
                id=f"{u}_{v}",
                from_node_id=str(u),
                to_node_id=str(v),
                length_m=data['distance'],
                speed_limit_kph=data['speed_limit'],
            )
            result.append(StopRecord(
                id=f"{stop['id']}_{u}_{v}",
                x=stop['x'],
                y=stop['y'],
                edge=edge,
                pos_on_edge=stop['pos'],
            ))
        return result

    def get_road_nodes(self) -> list[RoadNodeRecord]:
        """Return a RoadNodeRecord for every node in the graph."""
        return [
            RoadNodeRecord(id=str(node_id), x=data['x'], y=data['y'])
            for node_id, data in self._source._graph.nodes(data=True)
        ]

    def get_road_edges(self) -> list[RoadEdgeRecord]:
        """Return a RoadEdgeRecord for every directed edge in the graph."""
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
