# Autobus

Autobus is an autonomous bus routing system that adapts its route in real time
based on passenger demand.

The routing logic runs against a world it knows nothing about. Two worlds
exist: a synthetic directed graph, which is fast enough to run the statistical
campaigns on, and the [CARLA](https://carla.org/) autonomous driving simulator,
where a real vehicle drives a real town. The same agent, policy, demand
generator and metrics run over both — only the backend differs.

## Project structure

```
src/
├── map/
│   ├── MapProvider.py         # Abstract MapProvider interface
│   ├── PlanarMapProvider.py   # MapProvider backed by PlanarStreetGraph
│   ├── StaticMapProvider.py   # Hand-authored MapProvider for tests/debugging
│   ├── CarlaMapProvider.py    # MapProvider for a CARLA town; imports no carla
│   ├── carla_topology.py      # Road topology schema and IO
│   ├── carla_stops.py         # Bus stop schema and IO, addressed by OpenDRIVE
│   ├── carla_dump.py          # Dumps a town's topology; the only part needing a server
│   ├── carla_maps/            # Per-town topology and hand-authored stops (JSON)
│   ├── TransitGraph.py        # Routing graph
│   ├── graph.py               # PlanarStreetGraph and supporting types
│   └── map_data.py            # Data records: StopRecord, RoadNodeRecord, RoadEdgeRecord
├── algorithm/
│   ├── RequestPool.py                 # Passenger request data model and pool
│   ├── DispatchPolicy.py              # Dispatch policy interface and bus state
│   ├── FixedRoutePolicy.py            # Baseline: fixed repeating cycle
│   ├── SkipStopFixedRoutePolicy.py    # Fixed cycle, skipping stops with no demand
│   ├── DeviatedFixedRoutePolicy.py    # Skip-stop backbone with detours (see caveat)
│   ├── NearestUnservedPolicy.py       # Greedy: nearest demanded stop
│   ├── MarginalCostFilterPolicy.py    # Greedy with a pickup-cost filter
│   ├── RideBoundedGreedyPolicy.py     # Greedy with an onboard ride-slack gate
│   ├── ConstrainedInsertionPolicy.py  # Cheapest-insertion with rejection
│   ├── WindowInsertionPolicy.py       # Service-window insertion
│   ├── ReorderingInsertionPolicy.py   # Window insertion plus relocate-repair (L1)
│   ├── Schedule.py                    # Ordered visits and their feasibility
│   ├── TravelModel.py                 # What a policy believes about travel times
│   └── BusAgent.py                    # Bus agent: state management and re-planning
├── metrics/
│   └── MetricsTracker.py      # Per-tick snapshots and aggregate KPIs
├── simulation/
│   ├── SimulationBackend.py   # Abstract adapter: the seam to a world
│   ├── MockBackend.py         # Dead reckoning over a TransitGraph
│   ├── ScriptedBackend.py     # A timetable; no map at all
│   ├── CarlaBackend.py        # A real vehicle in a CARLA town
│   ├── carla_routing.py       # Which turns lead to a stop; imports carla
│   ├── SimulationRunner.py    # Owns the clock, tick order and orchestration
│   ├── AnimationRecorder.py   # Per-tick frames for replaying a trial
│   └── DemandGenerator.py     # Synthetic demand: GenerationPolicy and DemandGenerator
└── experiment/
    ├── trial.py               # TrialResult dataclass and run_trial()
    ├── comparison.py          # compare_policies(): multi-strategy runner
    ├── campaign.py            # run_campaign(): runs what is not yet recorded
    ├── store.py               # Completed runs on disk, keyed by identity
    ├── summary.py             # summarize_comparison/_results, ComparisonSummary
    ├── plots.py               # KPI bars, distributions, occupancy, parameter sweeps
    └── animation.py           # animate_trial(), show_animation(), show_animations()

results/
└── runs/                      # One JSON file per completed run; tracked in git
```

## Running the project

See [Running the Project](running.md) for setup and execution instructions.

## Browsing these docs locally

From the project root (with the Conda environment active):

```bash
mkdocs serve -f autobus-docs/mkdocs.yml
```

Then open `http://127.0.0.1:8000` in a browser.
