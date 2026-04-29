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
