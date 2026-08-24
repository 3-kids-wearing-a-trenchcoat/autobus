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

Three methods are abstract — the questions no map source can decline:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_stops()` | `list[StopRecord]` | All bus stops on the map |
| `get_road_nodes()` | `list[RoadNodeRecord]` | All navigational nodes (intersections) |
| `get_road_edges()` | `list[RoadEdgeRecord]` | All directed road segments |

Two more are concrete, with defaults chosen so that a source which does not
care about them behaves exactly as it did before they existed:

| Method | Default | Description |
|--------|---------|-------------|
| `traffic_efficiency()` | `1.0` | Fraction of the posted limit a bus actually averages here |
| `get_lane_changes()` | `[]` | Directed `(from_edge_id, to_edge_id)` pairs a bus may move sideways between |

Those defaults are load-bearing, not laziness. A planar graph has no lanes to
change between and its mock backend drives exactly what it predicts, so every
synthetic result the project rests on keeps producing identical numbers.

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
  `length_m` and an optional `speed_limit_kph` (`None` when unspecified;
  `TransitGraph` falls back to 130 kph when computing travel times).

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
   box becomes a node, carrying only its `x` and `y`. Nodes are untyped:
   every one of them is an intersection, so there is nothing to distinguish.
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

### CarlaMapProvider

`CarlaMapProvider` turns a CARLA town into a routable map. It reads two files
from `src/map/carla_maps/`, and **imports no `carla`** — a CARLA town can be
routed, tested and experimented on with no server running.

| File | Origin | Regenerated by |
|---|---|---|
| `<Town>.topology.json` | machine-dumped roads | `src.preparation.carla_dump` |
| `<Town>.stops.json` | hand-authored stops | `notebooks/carla-stop-authoring.ipynb` |
| `<Town>.xodr` | OpenDRIVE, generated maps only | converted from OpenStreetMap |

The first two are deliberately separate. Re-dumping a town's roads must never
be able to destroy hours of stop authoring, and re-authoring stops must never
need a server.

#### Two kinds of town

CARLA ships its towns baked into the server, and `load_world` names one. A map
built from OpenStreetMap has no such presence: it exists only as an OpenDRIVE
file, and the server builds the road procedurally from it every time.

Which applies is decided by the **data, not by a list in code** — a town is
generated when a `<Town>.xodr` sits beside its dump in `carla_maps`.
`src/simulation/carla_world.py` owns that decision (`is_generated`) and the
loading (`load`). Adding another campus is a matter of dropping three files
in. The one in the repository is **TAU**, a Tel Aviv University campus crop.

A generated world is rebuilt on every load rather than reused. CARLA reports
the same map name — `OpenDriveMap` — for every one of them, so there is no way
to ask the server which OpenDRIVE is currently up, and a stale world carried
into the next trial is exactly the contamination this prevents. The rebuild
measures about 2.3 s.

!!! warning "Two generation parameters are load-bearing, and neither is CARLA's default"
    A generated map is a bare ribbon of road with nothing beside it, which
    makes the defaults actively dangerous:

    * **`wall_height=0.0`** (CARLA defaults to 1.0). Walls hem the road in and
      the bus wedges against them — 2 of 8 legs completed with walls, 6 of 8
      without.
    * **`additional_width=4.0`** (CARLA defaults to 0.6). With nothing beyond
      the lane, any lane departure drops the bus out of the world. Widening
      took one campus from 6/8 legs to 12/12.

    Both live in `OPENDRIVE_PARAMETERS` in `carla_world`, with the
    measurements beside them.

#### Speed limits come from two different places

A shipped town publishes type-274 landmarks, which `carla_dump` reads and
combines per road by a length-weighted harmonic mean. A generated town
publishes none, so the limits come from the `<speed>` elements in its
OpenDRIVE, fastest lane winning — and those are mostly **not surveyed**. The
Tel Aviv extract carries 29 `maxspeed` tags against the 479 roads in its
OpenDRIVE; the rest are the converter's per-highway-type defaults, which are
motorway numbers. 9.2 km of the campus was priced at 100 km/h and a further
7.6 km at 80 — 70% of its length.

So every limit read out of a generated map is capped at
`GENERATED_SPEED_CEILING_KPH`, **50 km/h**, the urban default the invented
values should have been. Roads whose limit is genuinely lower keep it; only
the fiction is capped.

Because these are two different procedures rather than two sources of the same
thing, a run records **which one produced its limits** —
`landmark-harmonic-v1` or `xodr-capped-50-v1` — in its world identity, so
results from the two can never pool. The tag is built from the constant, so
changing the cap separates runs either side of the change automatically.

#### Stops are an input, not a derivation

In a real deployment you name the map *and* its stops rather than letting a
program guess where a bus should pull over. So stops are hand-authored and
version-controlled, addressed by their OpenDRIVE identity — `road_id`,
`lane_id`, `s` — which `Map.get_waypoint_xodr` resolves back exactly (worst
drift measured on Town01: 0.0006 m). World coordinates are cached alongside
so the file can be plotted and reviewed offline.

The sign of `lane_id` carries the driving direction, so a stop serves **one
direction** of its road. That falls out of the existing design rather than
being a compromise: `TransitGraph` nodes are directed road edges already.

#### Why segments are split

A `TransitGraph` node **is** a road edge, and stops are keyed by edge — so an
edge carries at most one stop and a second would be silently overwritten.
CARLA's topology segments run junction to junction and are routinely hundreds
of metres long: four of Town01's fifteen stops sit in pairs on a single
segment.

So a segment carrying stops is split at them, giving every stop its own edge.
This is what `RoadNodeRecord` already describes — *"any point where the bus
has a decision to make or otherwise needs to change its behavior"*.

!!! warning "A stop belongs to the edge that *begins* at its node"
    `TransitGraph` weights edge `A → B` by `length(A)`, so a shortest path
    sums every edge except its destination. Arriving at node `B` means
    arriving at the **start** of road-edge `B`.

    ```
    j1 ─── e1 ──► n_s08 ─── e2 ──► n_s13 ─── e3 ──► j2
          134 m           162 m            10 m

    cost to reach e2 == length(e1) == 134 m along the road, which is s08
    ```

    Attaching the stop to `e1` — the edge that *ends* at its node — is the
    intuitive reading and is wrong by exactly one edge length on every
    estimate.

#### Speed limits are read from the town

!!! warning "This section said the opposite until 2026-08-11"
    It claimed CARLA 0.9.16 published no speed limits at all — no type-274
    landmarks, no actors, no waypoint attribute — and that a town-level
    default was the best available. **That was wrong.** Town01 publishes 22
    type-274 landmarks at 30, 60 and 90, and a driving vehicle reads them
    back. Its arterials are 37.7% of the town's length and were being priced
    at 30, which made the whole travel model 1.75× optimistic. Every result
    recorded before that date was discarded.

`carla_dump.read_speed_limits` reads the type-274 landmarks. A road's limit
changes along its length — Town01's road 8 runs 30, 90, 60, 30 — but a road is
a single graph edge, so the sections are combined into one number by a
**length-weighted harmonic mean**, the average that preserves total travel
time. Roads with no sign fall back to the town-level
`default_speed_limit_kph` recorded in the dump.

Two approximations are known and quantified by `carla_calibrate`: sign
orientation is ignored (both directions of a road share a road ID here), and
the declared `mph` unit is ignored, because `get_speed_limit()` returns the
bare number and the traffic manager drives it as km/h — the number is what
governs the driving, so the number is what is recorded.

#### What the bus achieves is not what the sign says

A posted limit is an upper bound the bus never averages: about a third of
every CARLA run is spent stationary at a red light. That is a **bias, not
noise** — execution noise on the same runs measures 0.034 — so it belongs in
the estimate rather than in a safety margin, and it is reported by the map
source rather than configured on a policy.

`traffic_efficiency` carries it: a multiplier in (0, 1] that `TransitGraph`
applies to every edge weight. It is stored in the town's dump, fitted **by
driving** with `src.preparation.carla_calibrate`, and therefore belongs to the way
the bus was driven when it was measured — recalibrate after any change to
that. Measured 2026-08-17 under direct drive: Town01 0.476, Town02 0.448,
Town03 0.627, Town05 0.367. It decisively does not transfer between towns.

With both in place the model predicts Town01's legs with bias 1.000× and
residual sigma 0.278, against 1.75× and 0.408 before.

#### Checking a map

`validate()` returns one line per problem and an empty list when the map is
healthy. It covers the failures that are otherwise silent: an authored stop
that matched no segment or could not be given its own edge, an edge naming a
node that does not exist, an edge carrying metadata (which would make the
record unhashable), and a node the bus could never leave.

```python
provider = CarlaMapProvider("Town01")
assert provider.validate() == []
```

#### Running a trial on it

`run_trial` takes a `provider` in place of a planar graph, and nothing above
that line knows the difference:

```python
run_trial(policy_factory=..., provider=CarlaMapProvider("Town01"))
```

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
2. **stop** -- The `StopRecord` of the bus stop that sits on this edge (if it exists)  
3. **stop_pos** -- position of the stop (if it exists) along the route.  
    Gets a value in [0.0, 1.0], where `0.0` represents the stop is at the very start, `1.0` represents the stop is at the very end, and so on.  
4. **stop_name** -- The name of the bus stop (if the bus stop exists and it has the optional name property)  
5. **length** -- length of the route in meters  
6. **speed_limit** -- The speed limit in this route in km/h, or `None` if not specified by the source edge  
7. **start_node** -- id of start road node (matching `from_node_id` in `RoadEdgeRecord`)  
8. **stop_node** -- id of end node (matching `to_node_id` in `RoadEdgeRecord`)  
9. **metadata** -- dictionary of additional data (matching `metadata` in `RoadEdgeRecord`)  

Additionally, each edge in `TransitGraph` also has certain data associated with them:  
1. **weight** -- expected travel time in seconds.

There are two kinds of edge, priced differently:

* A **transition** between two road edges is weighted by the *origin* node's
  own travel time, `length / (speed_limit × traffic_efficiency)`, with
  `speed_limit` falling back to `DEFAULT_SPEED_LIMIT`'s 130 kph when the
  source edge specifies none. Weighting by the origin is what makes a
  shortest path sum every edge except its destination — see the warning about
  which edge a stop belongs to, above.
* A **lane change**, one per pair returned by `MapProvider.get_lane_changes()`,
  is weighted `LANE_CHANGE_RAMP_M / speed + LANE_CHANGE_PENALTY_S`: 15 m of
  forward travel while moving sideways, plus a flat 3 s. A lane change is a
  diagonal rather than a teleport, and the penalty stops a shortest-path
  search weaving between lanes to save a metre. `map_data` holds both
  constants, and `carla_routing` charges the same figures, or the bus would
  beat its own estimates. Raising `LANE_CHANGE_PENALTY_S` to a large number
  prices lane changes out entirely and recovers the behaviour from before
  they existed.

`traffic_efficiency` comes from the provider and is validated to be in
(0, 1]; at the default 1.0 the weights are exactly what they were before it
was introduced.

`TransitGraph` defines the following methods:  
| Method | Returns | Description |  
|--------|---------|-------------|  
| `get_stops()` | `list[StopRecord]` | All bus stops present in the graph |  
| `successors(node_id)` | `list[str]` | Immediate successor node IDs of the given node |  
| `best_path(origin_id, destination_id)` | `list[str]` | Ordered list of node IDs for the most efficient route from origin to destination |  
| `best_path_cost(origin_id, destination_id)` | `float` | Total travel time in seconds for the most efficient route from origin to destination |  
| `best_tour(start_id, stop_ids)` | `list[str]` | Ordered list of node IDs (starting with `start_id`) representing the cheapest tour that visits all nodes in `stop_ids` |

`best_path_cost` caches its work: the first query from a given origin runs a
single-source Dijkstra and stores the distances to every node, so subsequent
queries from that same origin — the common case when a policy scores many
candidate stops from one position — are O(1) lookups. This is valid only
because the graph is immutable after construction; the cache would need
invalidating if edge weights ever change (e.g. future congestion modelling).

> **Note:** The current `best_tour` implementation is a brute-force O(n!) permutation
> search. It is only practical for small stop counts (up to ~8–10 stops) and should
> be replaced with a more efficient algorithm (nearest-neighbor, dynamic programming,
> etc.) before being used in performance-sensitive contexts.