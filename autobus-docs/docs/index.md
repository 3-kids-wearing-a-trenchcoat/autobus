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
├── algorithm/
│   ├── RequestPool.py         # Passenger request data model and pool
│   ├── DispatchPolicy.py      # Dispatch policy interface and bus state
│   └── BusAgent.py            # Bus agent: state management and re-planning
├── metrics/
│   └── MetricsTracker.py      # Per-tick snapshots and aggregate KPIs
├── simulation/
│   ├── SimulationBackend.py   # Abstract simulation controller
│   ├── MockBackend.py         # SimulationBackend for non-CARLA map sources
│   ├── SimulationRunner.py    # Tick loop driver and result reporter
│   └── DemandGenerator.py     # Synthetic demand: GenerationPolicy and DemandGenerator
└── experiment/
    ├── trial.py               # TrialResult dataclass and run_trial()
    ├── comparison.py          # compare_policies(): multi-strategy runner
    ├── summary.py             # summarize_comparison(), ComparisonSummary
    ├── plots.py               # plot_kpi_bars(), plot_wait_distribution(), plot_occupancy()
    └── animation.py           # animate_trial(), show_animation(), show_animations()
```

## Running the project

See [Running the Project](running.md) for setup and execution instructions.

## Browsing these docs locally

From the project root (with the Conda environment active):

```bash
mkdocs serve -f autobus-docs/mkdocs.yml
```

Then open `http://127.0.0.1:8000` in a browser.
