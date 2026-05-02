import networkx as nx
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord
from map.MapProvider import MapProvider

def _dict_edge_by_origin_node(road_nodes: list[RoadNodeRecord],
                              road_edges: list[RoadEdgeRecord]) -> dict[str, list[RoadEdgeRecord]]:
    """
    Generate a dictionary mapping node id to list of edges which begin at that node.
    If any edge begins or ends at a node not in road_nodes, a ValueError is raised.
    :param road_nodes: list of RoadNodeRecord objects
    :param road_edges: list of RoadEdgeRecord objects
    :return: dict mapping RoadNodeRecord ids to a list of RoadEdgeRecords whose from_node_id matches the key.
    """
    node_ids = {node.id for node in road_nodes}
    output: dict[str, list[RoadEdgeRecord]] = {node_id: [] for node_id in node_ids}
    for edge in road_edges:
        from_id = edge.from_node_id
        if from_id not in node_ids:
            raise ValueError("Edge " + edge.id + " originates from node " + from_id +
                             ", which does not exist in the list of road_nodes.")
        if edge.to_node_id not in node_ids:
            raise ValueError("Edge " + edge.id + " leads to " + edge.to_node_id +
                             ", which does not exist in the list of road_nodes.")
        output[from_id].append(edge)
    return output

def _build_map(provider: MapProvider) -> nx.DiGraph:
    """Generate the map graph from a given MapProvider"""
    stops, road_nodes, road_edges = provider.get_stops(), provider.get_road_nodes(), provider.get_road_edges()
    output = nx.DiGraph()
    edge_by_origin = _dict_edge_by_origin_node(road_nodes, road_edges)
    # TODO: added edges as nodes to output and create edges between these nodes to represent legal transitions
    # bus stops should be associated with edges

    return output

class TransitGraph:
    """Graph-based representation of the road map"""
    def __init__(self, provider: MapProvider):
        self.provider: MapProvider = provider
        self.map: nx.DiGraph = _build_map(self.provider)

    # TODO: methods
