import networkx as nx
from map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord
from map.MapProvider import MapProvider

def _road_edge_to_dict()

def _build_map(provider: MapProvider) -> nx.DiGraph:
    """Generate the map graph from a given MapProvider"""
    stops, road_nodes, road_edges = provider.get_stops(), provider.get_road_nodes(), provider.get_road_edges()
    output = nx.DiGraph()
    #


    return output

class TransitGraph:
    """Graph-based representation of the road map"""
    def __init__(self, provider: MapProvider):
        self.provider: MapProvider = provider
        self.map: nx.DiGraph = _build_map(self.provider)

    # TODO: methods
