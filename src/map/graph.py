"""Planar street graph built from a Voronoi diagram, used as a map for bus routing."""

import networkx as nx
import numpy as np
from scipy.spatial import Voronoi


# Speed limit options (km/h) randomly assigned per road segment
_SPEED_OPTIONS: list[float] = [20.0, 30.0, 60.0]


class PlanarStreetGraph:
    """Randomly generated planar street graph backed by a networkx DiGraph.

    Every node is a road intersection carrying a position ``(x, y)``.  Every
    directed edge carries a ``distance``, ``speed_limit``, and derived
    ``travel_time``.  Edges that have a bus stop at their midpoint also carry
    a ``stop`` attribute; use :meth:`edge_stop` to query it.
    """

    def __init__(self, graph: nx.DiGraph) -> None:
        """Wrap a pre-built DiGraph as a PlanarStreetGraph."""
        self._graph = graph

    @classmethod
    def generate(
        cls,
        n_seeds: int,
        bounds: tuple[float, float, float, float],
        seed: int | None = None,
        one_way_probability: float = 0.3,
        stop_density: float = 0.15,
    ) -> "PlanarStreetGraph":
        """Generate a random planar street graph using a clipped Voronoi diagram."""
        rng = np.random.default_rng(seed)
        xmin, ymin, xmax, ymax = bounds

        interior_seeds = rng.uniform(
            low=[xmin, ymin],
            high=[xmax, ymax],
            size=(n_seeds, 2)
        )

        # Exterior seeds placed far outside the bbox cause scipy to treat
        # all within-bbox Voronoi ridges as finite — no infinite rays to clip
        margin = max(xmax - xmin, ymax - ymin) * 2
        exterior_seeds = np.array([
            [xmin - margin, ymin - margin],
            [xmax + margin, ymin - margin],
            [xmin - margin, ymax + margin],
            [xmax + margin, ymax + margin],
        ])
        all_seeds = np.vstack([interior_seeds, exterior_seeds])

        vor = Voronoi(all_seeds)

        graph = _build_intersection_graph(
            vor, bounds, one_way_probability, rng
        )
        graph = _ensure_strongly_connected(graph, rng)

        next_stop_id = max(graph.nodes()) + 1
        graph = _insert_bus_stops(graph, stop_density, rng, next_stop_id)

        return cls(graph)

    def edge_stop(self, u: int, v: int) -> dict | None:
        """Return stop metadata for edge u → v, or None if there is no stop.

        The returned dict contains:
        - ``'id'``: unique integer identifying the stop location
        - ``'x'``, ``'y'``: stop coordinates (midpoint of the edge)
        - ``'pos'``: fractional position along the edge (always ``0.5``
          for generated graphs)
        """
        return self._graph[u][v].get('stop')

    def shortest_path(self, source: int, target: int) -> list[int]:
        """Return the minimum travel-time path from source to target as a node list."""
        return nx.shortest_path(
            self._graph, source, target, weight='travel_time'
        )


def _build_intersection_graph(
    vor: Voronoi,
    bounds: tuple[float, float, float, float],
    one_way_probability: float,
    rng: np.random.Generator,
) -> nx.DiGraph:
    """Build a DiGraph of intersection nodes from in-bounds Voronoi geometry."""
    xmin, ymin, xmax, ymax = bounds
    graph = nx.DiGraph()

    # Map Voronoi vertex indices to graph node IDs for edge construction below
    vertex_to_node: dict[int, int] = {}
    node_id = 0

    for i, (vx, vy) in enumerate(vor.vertices):
        if xmin <= vx <= xmax and ymin <= vy <= ymax:
            graph.add_node(
                node_id,
                x=float(vx),
                y=float(vy),
            )
            vertex_to_node[i] = node_id
            node_id += 1

    for ridge_vertices in vor.ridge_vertices:
        v1_idx, v2_idx = ridge_vertices
        # Ridges where either endpoint falls outside the bbox (or is the
        # sentinel value -1 for an infinite ray) are skipped
        if v1_idx not in vertex_to_node or v2_idx not in vertex_to_node:
            continue

        n1 = vertex_to_node[v1_idx]
        n2 = vertex_to_node[v2_idx]

        x1, y1 = graph.nodes[n1]['x'], graph.nodes[n1]['y']
        x2, y2 = graph.nodes[n2]['x'], graph.nodes[n2]['y']
        distance = float(np.hypot(x2 - x1, y2 - y1))
        speed_limit = float(rng.choice(_SPEED_OPTIONS))
        travel_time = distance / speed_limit

        if rng.random() < one_way_probability:
            src, dst = (n1, n2) if rng.random() < 0.5 else (n2, n1)
            graph.add_edge(
                src, dst,
                distance=distance,
                speed_limit=speed_limit,
                travel_time=travel_time
            )
        else:
            for src, dst in [(n1, n2), (n2, n1)]:
                graph.add_edge(
                    src, dst,
                    distance=distance,
                    speed_limit=speed_limit,
                    travel_time=travel_time
                )

    # Nodes with no edges cannot participate in routing
    isolated = [n for n in graph.nodes() if graph.degree(n) == 0]
    graph.remove_nodes_from(isolated)

    return graph


def _euclidean_distance(graph: nx.DiGraph, u: int, v: int) -> float:
    """Euclidean distance between nodes u and v."""
    return float(np.hypot(
        graph.nodes[u]['x'] - graph.nodes[v]['x'],
        graph.nodes[u]['y'] - graph.nodes[v]['y']
    ))


def _ensure_strongly_connected(
    graph: nx.DiGraph,
    rng: np.random.Generator,
) -> nx.DiGraph:
    """Connect the closest SCC pairs until the whole graph is strongly connected."""
    while not nx.is_strongly_connected(graph):
        # Largest SCC first so we grow the main component on every iteration
        components = sorted(
            nx.strongly_connected_components(graph),
            key=len,
            reverse=True
        )
        main_component = components[0]

        min_dist = float('inf')
        closest_pair: tuple[int, int] | None = None

        for other in components[1:]:
            for u in main_component:
                for v in other:
                    dist = _euclidean_distance(graph, u, v)
                    if dist < min_dist:
                        min_dist = dist
                        closest_pair = (u, v)

        if closest_pair is None:
            break

        u, v = closest_pair
        speed_limit = 30.0
        graph.add_edge(
            u, v,
            distance=min_dist,
            speed_limit=speed_limit,
            travel_time=min_dist / speed_limit
        )
        graph.add_edge(
            v, u,
            distance=min_dist,
            speed_limit=speed_limit,
            travel_time=min_dist / speed_limit
        )

    return graph


def _insert_bus_stops(
    graph: nx.DiGraph,
    stop_density: float,
    rng: np.random.Generator,
    next_stop_id: int,
) -> nx.DiGraph:
    """Annotate a fraction of edge pairs with a bus stop at their midpoint.

    Each selected undirected pair gets one stop, shared by both travel
    directions. The stop is stored as a ``'stop'`` attribute on each relevant
    directed edge rather than as a separate node.
    """
    # Collect undirected pairs so a two-way street gets one shared stop
    # rather than separate stops at the same location
    undirected_pairs: set[tuple[int, int]] = set()
    for u, v in graph.edges():
        undirected_pairs.add((min(u, v), max(u, v)))

    pair_list = list(undirected_pairs)
    n_stops = min(
        max(1, int(len(pair_list) * stop_density)),
        len(pair_list)
    )
    selected_indices = rng.choice(
        len(pair_list), size=n_stops, replace=False
    )
    selected_pairs = [pair_list[i] for i in selected_indices]

    for u, v in selected_pairs:
        sx = (graph.nodes[u]['x'] + graph.nodes[v]['x']) / 2
        sy = (graph.nodes[u]['y'] + graph.nodes[v]['y']) / 2
        stop_data = {'id': next_stop_id, 'x': sx, 'y': sy, 'pos': 0.5}
        next_stop_id += 1

        # Annotate whichever directed edges exist between u and v
        for src, dst in [(u, v), (v, u)]:
            if graph.has_edge(src, dst):
                graph[src][dst]['stop'] = stop_data

    return graph
