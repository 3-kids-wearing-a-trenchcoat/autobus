import networkx as nx
from map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord
from MapProvider import MapProvider

def build_map(provider: MapProvider) -> nx.DiGraph:
    """Generate the map graph from a given MapProvider"""
    stops, road_nodes, road_edges = provider.get_stops(), provider.get_road_nodes(), provider.get_road_edges()
    # TODO: Implement

class TransitGraph:
    """Graph-based representation of the road map"""
    def __init__(self, provider: MapProvider):
        self.provider: MapProvider = provider
        self.map: nx.DiGraph = nx.DiGraph()

    # TODO: methods
