# Autobus

Autobus is an autonomous bus routing system that adapts its route in real time
based on passenger demand.

The final target is the [CARLA](https://carla.org/) autonomous driving
simulator. The current implementation is a proof of concept in which the world
is reduced to a directed graph, allowing the routing logic to be developed and
tested independently of the simulation environment.

## Project structure

```
src/
├── map/
│   ├── MapProvider.py         # Abstract MapProvider interface
│   ├── PlanarMapProvider.py   # MapProvider backed by PlanarStreetGraph
│   ├── StaticMapProvider.py   # Hand-authored MapProvider for tests/debugging
│   ├── TransitGraph.py        # Routing graph
│   ├── graph.py               # PlanarStreetGraph and supporting types
│   └── map_data.py            # Data records: StopRecord, RoadNodeRecord, RoadEdgeRecord
└── algorithm/
    └── RequestPool.py         # Passenger request data model and pool
```

## Running the project

Use `run.sh` from the project root to ensure the correct Conda environment is
active:

```bash
bash run.sh main.py
```

## Browsing these docs locally

From the project root (with the Conda environment active):

```bash
mkdocs serve -f autobus-docs/mkdocs.yml
```

Then open `http://127.0.0.1:8000` in a browser.
