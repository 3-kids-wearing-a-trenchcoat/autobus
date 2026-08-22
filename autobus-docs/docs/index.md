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
│   ├── carla_dump.py          # Dumps a town's topology and speed limits; needs a server
│   ├── carla_calibrate.py     # Fits traffic_efficiency by driving; needs a server
│   ├── carla_maps/            # Per-town topology and stops (JSON), plus .xodr for generated maps
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
│   ├── carla_routing.py       # Plans a drivable route to a stop; imports carla
│   ├── carla_world.py         # Raises a world, whether the town is shipped or generated
│   ├── carla_viewer.py        # Chase-camera window with a 2D HUD, for watching a run
│   ├── carla_debug_text.py    # Stroke lettering for CARLA's world-space debug drawing
│   ├── carla_stdout.py        # Captures and attributes what the traffic manager prints
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
├── runs/                      # One JSON file per completed run
└── archive/                   # Runs retired because the code that produced them changed
```

Neither `results/` directory is tracked in git — the volume makes it
impractical, and archived runs are kept out of the store so `load_runs` cannot
pool them with current results. The notebooks are the record.

## Running the project

See [Running the Project](running.md) for setup and execution instructions.

## Browsing these docs locally

From the project root (with the Conda environment active):

```bash
mkdocs serve -f autobus-docs/mkdocs.yml
```

Then open `http://127.0.0.1:8000` in a browser.
