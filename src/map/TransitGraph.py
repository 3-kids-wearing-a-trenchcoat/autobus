import networkx as nx
from itertools import permutations
from src.map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord, DEFAULT_SPEED_LIMIT
from src.map.MapProvider import MapProvider

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

def _calc_weights(graph: nx.DiGraph) -> None:
    """Calculate edge weights according to their nodes"""
    # Currently, this just sets each edge's weight to the expected travel time on its parent node.
    # Which is very simple, but would probably need to be updated later to account for things like congestion
    for origin_id, dest_id in graph.edges():
        origin_data = graph.nodes[origin_id]
        length_m: float = origin_data.get("length")
        speed_limit_kph: float = origin_data.get("speed_limit") or DEFAULT_SPEED_LIMIT
        speed_limit_mps = speed_limit_kph * 1000 / 3600
        travel_time_s = length_m / speed_limit_mps
        graph[origin_id][dest_id]["weight"] = travel_time_s

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
            output.add_edge(origin_edge_id, dest.id)
    # Give edges weights, that they may be used with built-in networkx algorithms
    _calc_weights(output)

    return output

class TransitGraph:
    """Graph-based representation of the road map"""
    def __init__(self, provider: MapProvider):
        self.provider: MapProvider = provider
        self.map: nx.DiGraph = _build_map(self.provider)
        # Cache of single-source shortest-path distances, keyed by origin.
        # Safe because the map is static after construction; must be cleared
        # if the map ever becomes mutable (future congestion/road-block TODO).
        self._dist_cache: dict[str, dict[str, float]] = {}

    def get_stops(self) -> list[StopRecord]:
        """Get a list of all bus stops"""
        return [data.get("stop")
                for _, data in self.map.nodes(data=True)
                if data.get("stop") is not None]

    def successors(self, node_id: str) -> list[str]:
        """
        Get list of successor nodes
        :param node_id: ID of origin node
        :return: List of IDs corresponding to nodes that are immediate successors of the input node
        """
        return list(self.map.successors(node_id))

    def best_path(self, origin_id: str, destination_id: str) -> list[str]:
        """
        Find the best route from the origin to the destination.
        :param origin_id: ID of the start node
        :param destination_id: ID of the target node
        :return: ordered list of node IDs representing the most efficient path from origin to destination
                 (including start and destination)
        """
        return nx.dijkstra_path(self.map, origin_id, destination_id, weight="weight")

    def best_path_cost(self, origin_id: str, destination_id: str) -> float:
        """
        Calculate the cost of the best route from origin to destination.

        Distances from a given origin are computed once (a single-source
        Dijkstra) and cached, so repeated queries from the same origin — the
        common case when scoring many candidate stops — are O(1) lookups. This
        is valid because the map is static after construction.
        :param origin_id: ID of the start node
        :param destination_id: ID of the target node
        :return: total weight of the most efficient path from origin to destination
        """
        distances = self._distances_from(origin_id)
        if destination_id not in distances:
            raise nx.NetworkXNoPath(
                f"No path between {origin_id} and {destination_id}.")
        return distances[destination_id]

    def _distances_from(self, origin_id: str) -> dict[str, float]:
        """
        Return the shortest-path cost from origin_id to every reachable node,
        computing it once and caching the result.
        :param origin_id: ID of the start node
        :return: mapping from reachable node ID to its shortest-path cost
        """
        cached = self._dist_cache.get(origin_id)
        if cached is None:
            cached = nx.single_source_dijkstra_path_length(
                self.map, origin_id, weight="weight")
            self._dist_cache[origin_id] = cached
        return cached

    def best_tour(self, start_id: str, stop_ids: list[str]) -> list[str]:
        """
        Find the cheapest tour starting at start_id that visits all nodes in stop_ids.
        :param start_id: ID of the starting node
        :param stop_ids: list of node IDs to visit
        :return: ordered list of node IDs starting with start_id,
                 representing the cheapest visitation order
        """
        # TODO: brute-force O(n!) — consider replacing with a more efficient algorithm
        # (e.g. nearest-neighbor, dynamic programming) for larger inputs.
        all_nodes = [start_id] + stop_ids
        costs: dict[tuple[str, str], float] = {
            (a, b): self.best_path_cost(a, b)
            for a in all_nodes for b in all_nodes if a != b
        }
        best_order: list[str] = []
        best_cost = float("inf")
        for perm in permutations(stop_ids):
            tour = [start_id] + list(perm)
            cost = sum(costs[(tour[i], tour[i + 1])]
                       for i in range(len(tour) - 1))
            if cost < best_cost:
                best_cost = cost
                best_order = tour
        return best_order

    # TODO: Later (after PoC phase) it's worth considering congestion and road-blocks influencing routing.
