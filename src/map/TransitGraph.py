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
    """
    Generate an edge-node graph representing the road map, where each directed edge A -> B from the world map
    becomes its own node.
    :param provider: A MapProvider subclass which abstracts the data given by the map source. (Carla, Planar, Static)
    :return: networkx DiGraph Object
    """
    stops, road_nodes, road_edges = provider.get_stops(), provider.get_road_nodes(), provider.get_road_edges()
    output = nx.DiGraph()
    edge_by_origin = _dict_edge_by_origin_node(road_nodes, road_edges)
    stops_by_edge: dict[RoadEdgeRecord, StopRecord] = {stop.edge: stop for stop in stops}

    # Create all edge-nodes
    for edge in road_edges:
        associated_stop = stops_by_edge.get(edge)   # get associated stop if it exists, 'None' otherwise
        associated_stop_pos = 0.0 if associated_stop is None else associated_stop.pos_on_edge
        associated_stop_name = None if associated_stop is None else associated_stop.name
        # TODO: the nodes will almost certainly need more data than I'm giving it now
        output.add_node(edge.id, stop=associated_stop, stop_pos=associated_stop_pos, stop_name=associated_stop_name,
                        length=edge.length_m, speed_limit=edge.speed_limit_kph,
                        start_node=edge.from_node_id, stop_node=edge.to_node_id, metadata=edge.metadata)
    # Create edges between edge-nodes, representing the ability to transition from one route to another
    for edge in road_edges:
        origin_edge_id = edge.id
        # TODO: add a way to filter illegal road transitions. (probably via metadata)
        destination_edges = edge_by_origin[edge.to_node_id] # edges whose from_node are this edge's to_node
        for dest in destination_edges:
            output.add_edge(origin_edge_id, dest.id) # TODO: should add weights

    return output

class TransitGraph:
    """Graph-based representation of the road map"""
    def __init__(self, provider: MapProvider):
        self.provider: MapProvider = provider
        self.map: nx.DiGraph = _build_map(self.provider)

    # TODO: methods
