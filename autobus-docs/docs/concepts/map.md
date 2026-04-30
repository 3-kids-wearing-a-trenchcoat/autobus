# Map

## Overview

`PlanarStreetGraph` is a randomly generated directed graph that stands in for
a real city map during the proof-of-concept phase. It is designed to be a
close enough approximation of urban street topology to meaningfully exercise
the bus routing algorithm, without requiring the full CARLA simulation stack.

## Graph structure

The graph is a `networkx.DiGraph`. Every node carries a position `(x, y)` and
a `NodeType`; every directed edge carries a `distance`, `speed_limit`, and
derived `travel_time`.

### Node types

| Type | Role |
|------|------|
| `INTERSECTION` | Road junction — where the bus chooses its next direction |
| `BUS_STOP` | Passenger boarding/alighting point — mid-block on a road segment |

Bus stops are placed at the midpoint of selected road segments, so they always
sit between two intersections rather than at a junction. A stop on a two-way
street is shared by both travel directions.

Each bus stop carries a `Zone` label (residential, commercial, or hospital)
intended to drive demand modelling in later phases.

## Generation algorithm

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
5. **Bus stop insertion** — a fraction (`stop_density`) of intersection-to-intersection
   edges are split: edge `A → B` becomes `A → S → B` where `S` is a new
   `BUS_STOP` node at the midpoint.

### Why Voronoi?

Voronoi cells correspond naturally to city blocks, and the ridges between them
correspond to streets. This gives the generated map an organic, non-grid
topology with irregular block sizes — closer to a real city than a simple
perturbed grid, and simpler to implement than a full procedural growth model.

The main limitation is that all interior intersections have degree 3
(T-junctions) rather than the degree-4 crossroads common in real cities.

---

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

Anything that needs map data — most importantly `TransitGraphBuilder` — depends
only on this interface. The concrete source of the data is invisible to it.

### Data records

The three return types are plain frozen dataclasses defined in `map_data`:

- **`StopRecord`** — a bus stop with a string ID, a position `(x, y, z)`, and an
  optional human-readable name.
- **`RoadNodeRecord`** — an intersection or other navigational decision point with
  a string ID and a position `(x, y, z)`.
- **`RoadEdgeRecord`** — a directed road segment between two nodes, carrying
  `length_m` and an optional `speed_limit_kph`.

All IDs are strings so that implementations backed by different sources (integer
node IDs from networkx, string actor IDs from CARLA, etc.) can be accommodated
without changing the interface.

Stops and road nodes are kept as **separate record types** because they play
different roles: road nodes form the navigational graph that describes how the
bus can move, while stops are the points at which passengers board or alight.
How the two are combined into a single routing structure is left entirely to
`TransitGraphBuilder`.

### PlanarMapProvider

`PlanarMapProvider` is the concrete `MapProvider` implementation for the
proof-of-concept phase. It wraps a `PlanarStreetGraph` and translates its
internal data into the provider interface:

- `get_stops()` — one `StopRecord` per `BUS_STOP` node, preserving position.
- `get_road_nodes()` — one `RoadNodeRecord` per `INTERSECTION` node. Bus stop
  nodes are intentionally excluded; they are exposed through `get_stops()` only.
- `get_road_edges()` — one `RoadEdgeRecord` per directed edge in the graph,
  preserving distance and speed limit.
