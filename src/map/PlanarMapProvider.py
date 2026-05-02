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
        """Return a StopRecord for every BUS_STOP node.

        Each stop is associated with the road edge it sits on.  Because
        ``PlanarStreetGraph`` inserts stops by splitting an edge A → B into
        A → S → B, the original edge is reconstructed here and the stop
        position is computed as ``d(A, S) / (d(A, S) + d(S, B))``.

        For two-way roads a single stop node serves both directions, so two
        ``StopRecord`` objects are returned (one per directed edge), with IDs
        of the form ``"{stop_id}_{from_id}_{to_id}"``.
        """
        g = self._source._graph
        result = []
        for s, data in g.nodes(data=True):
            if data['type'] != NodeType.BUS_STOP:
                continue
            for a in g.predecessors(s):
                if g.nodes[a]['type'] != NodeType.INTERSECTION:
                    continue
                # Find the INTERSECTION successor of s that is not a —
                # that is the original destination of the pre-split edge.
                b_list = [
                    n for n in g.successors(s)
                    if g.nodes[n]['type'] == NodeType.INTERSECTION
                    and n != a
                ]
                if not b_list:
                    continue
                b = b_list[0]
                half_a = g[a][s]['distance']
                half_b = g[s][b]['distance']
                full_dist = half_a + half_b
                edge = RoadEdgeRecord(
                    id=f"{a}_{b}",
                    from_node_id=str(a),
                    to_node_id=str(b),
                    length_m=full_dist,
                    speed_limit_kph=g[a][s]['speed_limit'],
                )
                result.append(StopRecord(
                    id=f"{s}_{a}_{b}",
                    x=data['x'],
                    y=data['y'],
                    edge=edge,
                    pos_on_edge=half_a / full_dist,
                ))
        return result

    def get_road_nodes(self) -> list[RoadNodeRecord]:
        """Return a RoadNodeRecord for every INTERSECTION node."""
        return [
            RoadNodeRecord(id=str(node_id), x=data['x'], y=data['y'])
            for node_id, data in self._source._graph.nodes(data=True)
            if data['type'] == NodeType.INTERSECTION
        ]

    def get_road_edges(self) -> list[RoadEdgeRecord]:
        """Return a RoadEdgeRecord for every directed road between intersections.

        Edges that were split by a BUS_STOP insertion are reconstructed as a
        single edge spanning the full original distance.  BUS_STOP → INTERSECTION
        half-edges are skipped; they are covered by the corresponding
        INTERSECTION → BUS_STOP entry.
        """
        g = self._source._graph
        result = []
        for u, v, data in g.edges(data=True):
            u_type = g.nodes[u]['type']
            v_type = g.nodes[v]['type']

            if u_type == NodeType.INTERSECTION and v_type == NodeType.INTERSECTION:
                result.append(RoadEdgeRecord(
                    id=f"{u}_{v}",
                    from_node_id=str(u),
                    to_node_id=str(v),
                    length_m=data['distance'],
                    speed_limit_kph=data['speed_limit'],
                ))

            elif u_type == NodeType.INTERSECTION and v_type == NodeType.BUS_STOP:
                # First half of a split edge u → S → w; reconstruct u → w.
                s = v
                w_list = [
                    n for n in g.successors(s)
                    if g.nodes[n]['type'] == NodeType.INTERSECTION
                    and n != u
                ]
                if not w_list:
                    continue
                w = w_list[0]
                result.append(RoadEdgeRecord(
                    id=f"{u}_{w}",
                    from_node_id=str(u),
                    to_node_id=str(w),
                    length_m=data['distance'] + g[s][w]['distance'],
                    speed_limit_kph=data['speed_limit'],
                ))
            # BUS_STOP → INTERSECTION edges are skipped; handled above.

        return result
