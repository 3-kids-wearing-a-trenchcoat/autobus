# Running the Project

## Prerequisites

The project runs inside a Conda environment named `autobus`. Install
[Miniforge](https://github.com/conda-forge/miniforge) or Anaconda, then create
the environment from the project root:

```bash
conda env create -f environment.yml
```

## Running the simulation

Use `run.sh` from the project root to execute Python scripts inside the correct
Conda environment:

```bash
bash run.sh main.py
```

The script is a thin wrapper around `python`, so any arguments that would
normally follow `python` on the command line can be passed after the script
name:

```bash
bash run.sh main.py --some-flag value
```

## Running experiments (Jupyter)

Use `jupyter-run.sh` to launch JupyterLab:

```bash
bash jupyter-run.sh
```

The script sets `PYTHONPATH` to the project root before starting Jupyter, so
`src.*` imports work correctly from any notebook regardless of where it lives in
the repository. Experiment notebooks are kept in the `notebooks/` directory.

## Running the CARLA server

The server runs from `compose.yml` in the project root and must be started by
hand:

```bash
docker compose up -d carla     # start
docker compose down            # stop
```

`tests/sanity_cehck_carla.py` confirms it is live: it connects, spawns a
vehicle, drives it on autopilot for three seconds and destroys it.

**The server starts on Town10HD_Opt**, not on the town the project uses. That
matters for more than convenience — see the warning below.

Most of the project needs no server at all. `CarlaMapProvider` reads a town's
roads and stops from version-controlled JSON, so a CARLA town can be routed,
tested and experimented on with nothing running:

```python
run_trial(policy_factory=..., provider=CarlaMapProvider("Town01"))
```

Only two things need a live server: driving the bus with
[`CarlaBackend`](concepts/simulation_backend.md#carlabackend), and re-dumping a
town's road topology with `src.map.carla_dump`.

To watch the bus drive, use `notebooks/carla-demo.ipynb`.

!!! warning "Load the town before touching the traffic manager"
    A `carla.TrafficManager` caches the road network when it is first created
    and **never re-caches it** when a new map is loaded. One created while
    Town10HD_Opt is still loaded will steer vehicles around Town01 using
    Town10HD's waypoints — measured, 0 of 5 stops reached and off any lane for
    93% of the run, looking exactly like bad driving rather than a
    misconfiguration.

    `CarlaBackend` defends itself by discarding any traffic manager it finds
    and building its own after the map load. Any other client code — a
    notebook cell, a scratch script — must load the town *before* it calls
    `get_trafficmanager()`.

### If the CARLA window looks frozen

The world has been left in synchronous mode with nobody ticking it, usually by
an interrupted run. The preflight cell in `notebooks/carla-demo.ipynb` restores
asynchronous mode and clears stray actors; it is safe to run at any time.
