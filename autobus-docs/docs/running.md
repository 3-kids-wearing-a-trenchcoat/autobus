# Running the Project

## Prerequisites

The project runs inside a Conda environment named `autobus`. Install
[Miniforge](https://github.com/conda-forge/miniforge) or Anaconda, then create
the environment from the project root:

```bash
conda env create -f environment.yml
```

### CARLA's `agents` package — a second setup step

Anything that drives a bus in CARLA also needs `agents.navigation`, which
CARLA does **not** publish to PyPI. The `carla==0.9.16` wheel is client
bindings only — its entire contents are `__init__.py`, `libcarla.so` and two
`.pyi` stubs. The `agents` package ships inside the CARLA container image
instead, which is why CARLA's own documentation shows a plain
`from agents.navigation.basic_agent import BasicAgent`: their examples run
from inside the source tree, where the package is already on the path.

So `conda env create` alone does not finish the environment. Run this once:

```bash
bash install-carla-agents.sh
```

It copies `agents/` out of the pinned image into the environment's
`site-packages` and then imports it to check. Re-running replaces the copy.

!!! note "Why it is installed rather than copied into the repository"
    Vendoring it would put ~1,800 lines of someone else's code under this
    project's ownership for no benefit. Taking it from the **pinned image**
    means it cannot drift out of step with the server that runs alongside
    it, and it stays a dependency like any other. The cost is this extra
    step, which is fair given the image is required to run CARLA anyway.

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

Four of them are the entry points — two that produce runs, two that read them:

| notebook | what it does |
|---|---|
| `synthetic-campaign.ipynb` | Sweeps the policies and their service windows on the campus map, filing every run in the store. Stop it whenever you like; re-running continues rather than restarting. |
| `carla-campaign-towns.ipynb` | The same comparison driven in real CARLA towns. Needs a live server, and runs per seed so a hiccup costs one trial rather than the night. |
| `results-explorer.ipynb` | Reads **exactly the runs you ask for**, selected by the constants in its *Filters* cell and faceted by `GROUP_BY`. This is the one to reach for. |
| `results.ipynb` | Reads the store and draws the tables and graphs for whichever scenario has the most runs. Neither reader runs a simulation or needs a server, so both work while a campaign is still going. |

### Reproducibility: set `PYTHONHASHSEED`

!!! danger "`run_trial(seed=N)` is not reproducible across processes on its own"
    `run_trial` seeds the global RNG, which is enough *within* one process.
    But several policies take `min()` over a `set[str]`, and `min` breaks ties
    by iteration order, which depends on Python's per-process string hash
    randomisation. `FixedRoutePolicy` is the starkest case: with no explicit
    `stop_order` it **generates a different cycle** — a literally different
    bus route — from the same graph in a different process.

    Two identical runs in separate processes therefore disagree, and results
    recorded in one session cannot be reproduced exactly in another.

Both launchers pin it for you, so the ordinary way of launching is also the
reproducible one:

```bash
./run.sh my_experiment.py
bash jupyter-run.sh
```

Each exports `PYTHONHASHSEED=0` unless you exported a value of your own first,
in which case yours wins — any pinned value reproduces, and the store records
which one was used.

!!! warning "A seeded run refuses to start without it"
    `run_trial(seed=...)` and `run_campaign` raise `RuntimeError` when
    `PYTHONHASHSEED` is unset or `random`, so bypassing the launchers fails
    loudly instead of quietly producing a run nobody can reproduce. An
    unseeded `run_trial` claims nothing about reproducibility and is left
    alone.

    For throwaway exploration, `AUTOBUS_ALLOW_UNPINNED_HASH=1` runs anyway.
    Do not file the results of a run that used it.

Every run filed in the [results store](concepts/experiment.md#the-results-store)
records the value it ran under in `provenance.python_hash_seed`, so a record
showing `null` is one whose exact reproduction cannot be guaranteed. Averages
over many seeds remain meaningful either way; individual seeded numbers do
not.

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

Only three things need a live server: driving the bus with
[`CarlaBackend`](concepts/simulation_backend.md#carlabackend), re-dumping a
town's roads and speed limits with `src.map.carla_dump`, and fitting a town's
`traffic_efficiency` with `src.map.carla_calibrate` — which measures it *by
driving*, so it must be re-run after any change to how the bus is driven.

To watch the bus drive, use `notebooks/carla-single-run.ipynb`, which runs one
trial with the [viewer](concepts/watching_a_run.md) on.

!!! warning "Load the town before touching the traffic manager"
    A `carla.TrafficManager` caches the road network when it is first created
    and **never re-caches it** when a new map is loaded. One created while
    Town10HD_Opt is still loaded will steer vehicles around Town01 using
    Town10HD's waypoints — measured, 0 of 5 stops reached and off any lane for
    93% of the run, looking exactly like bad driving rather than a
    misconfiguration.

    `CarlaBackend` defends itself by building its own traffic manager, on its
    own port, after the map load. Any other client code — a notebook cell, a
    scratch script — must load the town *before* it calls
    `get_trafficmanager()`.

!!! warning "A traffic manager is owned by a process, so don't share one"
    It is an RPC service hosted by the client that created it, and
    `get_trafficmanager()` returns a remote proxy when another process owns
    the port. A kernel that ran a trial and was never restarted still owns
    port 8000, and a proxy to its traffic manager will register your vehicle
    and never drive it — stationary, zero throttle, zero brake.

    `CarlaBackend` avoids this by taking a free port of its own, so a script
    and a notebook can use the same server at once. If you write client code
    that drives vehicles, do the same rather than assuming port 8000 is
    yours.

### If the CARLA window looks frozen

The world has been left in synchronous mode with nobody ticking it, usually by
an interrupted run. The preflight cell in `notebooks/carla-demo.ipynb` restores
asynchronous mode and clears stray actors; it is safe to run at any time.
