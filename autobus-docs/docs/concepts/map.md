# Map

## Overview

The `map` package is responsible for representing the world in a form the bus
routing algorithm can use. It has three main concerns:

1. **Abstracting the map source** — `MapProvider` defines a common interface
   for road data, so the rest of the system is decoupled from whether that
   data comes from a generated graph, the CARLA simulation, or any other source.
2. **Providing map data** — Concrete `MapProvider` implementations translate
   from a specific source into the common interface. During the proof-of-concept
   phase, `PlanarMapProvider` wraps `PlanarStreetGraph`, a randomly generated
   planar road network.
3. **Representing the world for routing** — `TransitGraph` consumes
   `MapProvider` output and converts it into the directed graph used by the
   bus routing algorithm at runtime.

## Map Provider

### The abstraction layer

The bus routing algorithm should not need to know whether its map data comes
from a `PlanarStreetGraph`, from a CARLA simulation, or from any other source.
`MapProvider` is the abstract class that enforces this separation.

It defines three methods:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_stops()` | `list[StopRecord]` | All bus stops on the map |
| `get_road_nodes()` | `list[RoadNodeRecord]` | All navigational nodes (intersections) |
| `get_road_edges()` | `list[RoadEdgeRecord]` | All directed road segments |

Anything that needs map data — most importantly [TransitGraph](#transitgraph) — depends
only on this interface. The concrete source of the data is invisible to it.

### Data records

The three return types are plain frozen dataclasses defined in `map_data`:

- **`StopRecord`** — a bus stop with a string ID, a position `(x, y, z)`, a
  reference to the `RoadEdgeRecord` it sits on, and a `pos_on_edge` value
  (0 = at the from-node, 1 = at the to-node).
- **`RoadNodeRecord`** — a road intersection with a string ID and a position
  `(x, y, z)`.
- **`RoadEdgeRecord`** — a directed road segment between two nodes, carrying
  `length_m` and `speed_limit_kph` (defaults to 130 kph).

All IDs are strings so that implementations backed by different sources (integer
node IDs from networkx, string actor IDs from CARLA, etc.) can be accommodated
without changing the interface.

Stops and road nodes are kept as **separate record types** because they play
different roles: road nodes form the navigational graph that describes how the
bus can move, while stops are the points at which passengers board or alight.
How the two are combined into a single routing structure is left entirely to
`TransitGraph`.

### PlanarMapProvider

`PlanarMapProvider` is the concrete `MapProvider` implementation for the
proof-of-concept phase. It wraps a `PlanarStreetGraph` and translates its
internal data into the provider interface:

- `get_road_nodes()` — one `RoadNodeRecord` per node in the graph.
- `get_road_edges()` — one `RoadEdgeRecord` per directed edge, preserving
  distance and speed limit.
- `get_stops()` — one `StopRecord` per directed edge that carries a stop.  A
  stop on a two-way road produces two records — one per direction — with IDs
  of the form `"{stop_id}_{from_node_id}_{to_node_id}"`.  `pos_on_edge` is
  always `0.5` because stops are placed at the midpoint by construction.

#### PlanarStreetGraph

`PlanarStreetGraph` is a randomly generated directed graph that stands in for
a real city map during the proof-of-concept phase. It is designed to be a
close enough approximation of urban street topology to meaningfully exercise
the bus routing algorithm, without requiring the full CARLA simulation stack.

##### Graph structure

The graph is a `networkx.DiGraph`. Every node is a road intersection carrying
a position `(x, y)`. Every directed edge carries a `distance`, `speed_limit`,
and derived `travel_time`. Edges that have a bus stop at their midpoint also
carry a `stop` attribute with the stop's coordinates and a unique integer ID.

##### Generation algorithm

The graph is generated from a [Voronoi diagram](https://en.wikipedia.org/wiki/Voronoi_diagram):

1. **Seed placement** — `n_seeds` random points are scattered inside the
   bounding box. Four extra seeds are placed far outside the box so that all
   Voronoi ridges within the box are finite.
2. **Intersection nodes** — each Voronoi vertex that falls inside the bounding
   box becomes an `INTERSECTION` node.
3. **Road edges** — each Voronoi ridge with both endpoints inside the box
   becomes a road. A random `speed_limit` is assigned; some roads are made
   one-way.
4. **Connectivity** — if the graph is not strongly connected, the closest pair
   of disconnected components is bridged with a bidirectional edge, and this
   is repeated until the whole graph is one strongly connected component.
5. **Bus stop insertion** — a fraction (`stop_density`) of edge pairs are
   annotated with a bus stop at their midpoint. On a two-way street both
   directed edges share the same stop. The stop is stored as metadata on the
   edge, not as a separate node.

###### Why Voronoi?

Voronoi cells correspond naturally to city blocks, and the ridges between them
correspond to streets. This gives the generated map an organic, non-grid
topology with irregular block sizes — closer to a real city than a simple
perturbed grid, and simpler to implement than a full procedural growth model.

The main limitation is that all interior intersections have degree 3
(T-junctions) rather than the degree-4 crossroads common in real cities.

### StaticMapProvider

`StaticMapProvider` is a hand-authored `MapProvider` intended for unit tests
and debugging. Rather than wrapping a generated or simulation-backed graph, it
accepts road nodes as plain records, directed edges as `(from_id, to_id)`
string pairs, and stops as `StopInput` records.

Edge lengths are computed automatically from the node coordinates using
Euclidean distance.  Stop coordinates are interpolated from the referenced
edge's endpoints and the supplied `pos_on_edge` value, so callers only need
to specify connectivity and fractional position — no manual coordinate or
length arithmetic required.  All z-values are fixed at `0.0`.

Because the resulting map is fully deterministic and human-readable, it is
easy to reason about expected `TransitGraph` outputs by hand and use them as
the basis for assertions in tests.

## TransitGraph

`TransitGraph` is a representation of the world map used to make routing decision in real time.  
This representation reduces the world data (as provided by [MapProvider](#map-provider)) into a simple directed graph 
where each node represents an **edge** in the real world (that is, a route from one road node to another) and each edge 
represents a possible transition from one route to another.  
For example, the following graph
```
A  ←  B
  ↘  ↗
   C
```
could be converted into the following _edge-node_ graph
```
(A -> C) → (C -> B)
    ↖       ↙
     (B -> A)
```
This representation fits our use-case, as the location and identity of road nodes is only needed to determine 
which edges are reachable after any given edge.  
This representation makes it easier to discriminate illegal moves (for example, U-turns in a T intersection).

The graph is build on top of [networkx](https://networkx.org/en/), and each node has certain data associated with it:  
1. **id** -- matching the edge id in the `RoadEdgeRecord` provided by `MapProvider`  
2. **stop** -- The id of a bus stop that sits on this edge (if it exists)  
3. **stop_pos** -- position of the stop (if it exists) along the route.  
    Gets a value in [0.0, 1.0], where `0.0` represents the stop is at the very start, `1.0` represents the stop is at the very end, and so on.  
4. **stop_name** -- The name of the bus stop (if the bus stop exists and it has the optional name property)  
5. **length** -- length of the route in meters  
6. **speed_limit** -- The speed limit in this route in km/h  
7. **start_node** -- id of start road node (matching `from_node_id` in `RoadEdgeRecord`)  
8. **stop_node** -- if of end node (matching `to_node_id` in `RoadEdgeRecord`)  
9. **metadata** -- dictionary of additional data (matching `metadata` in `RoadEdgeRecord`)  

Additionally, each edge in `TransitGraph` also has certain data associated with them:  
1. **weight** -- expected travel time in seconds, calculated as `length / speed_limit`  

`TransitGraph` defines the following methods:  
| Method | Returns | Description |  
|--------|---------|-------------|  
| `get_stops()` | `list[StopRecord]` | All bus stops present in the graph |  
| `successors(node_id)` | `list[str]` | Immediate successor node IDs of the given node |  
| `best_path(origin_id, destination_id)` | `list[str]` | Ordered list of node IDs for the most efficient route from origin to destination |  
| `best_path_cost(origin_id, destination_id)` | `float` | Total travel time in seconds for the most efficient route from origin to destination |  
| `best_tour(start_id, stop_ids)` | `list[str]` | Ordered list of node IDs (starting with `start_id`) representing the cheapest tour that visits all nodes in `stop_ids` |  