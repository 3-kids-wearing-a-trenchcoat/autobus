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

It pulls the pinned `carlasim/carla:0.9.16` image if it is not already
present, copies `agents/` out of it into the environment's `site-packages`,
and imports it to check. Re-running replaces the copy. **This is the one
place the project needs Docker**, and it needs it to fetch a Python package
— not to run anything.

!!! note "Why it is installed rather than copied into the repository"
    Vendoring it would put ~1,800 lines of someone else's code under this
    project's ownership for no benefit. Taking it from the **pinned image**
    means it cannot drift out of step with the version the client wheel is
    pinned to, and it stays a dependency like any other. The cost is this
    extra setup step.

## Running the demo

`main.py` is the entry point for *seeing* the system work. It runs a single
simulation — by default in a CARLA town, with a chase-camera window following
the bus — and prints a summary when the bus is done.

Use `run.sh` from the project root, which runs the script inside the correct
Conda environment:

```bash
bash run.sh main.py
```

`python main.py` works too, as long as the Conda environment is already
active — the demo does not insist on `run.sh` the way the notebooks and
campaigns do. The difference is reproducibility: `run.sh` pins
`PYTHONHASHSEED`, and without it another process may serve the same
passengers in a different order. The demo says so and runs anyway, which is
the right trade for a demonstration and the wrong one for a measurement.

That on its own is a complete demo. Every option has a default defined at the
top of `main.py`, and every default can be overridden by a flag; a flag you do
not pass keeps its default, so this changes the routing policy and nothing
else:

```bash
bash run.sh main.py --policy fixed
```

`bash run.sh main.py --help` lists every flag with its default. The ones worth
knowing:

| flag | what it does |
|---|---|
| `--backend carla\|mock` | `carla` drives a real bus and needs a live server; `mock` simulates travel times on the graph and needs neither a server nor the CARLA client |
| `--town` | which town to drive in; `--list-towns` prints the ones with authored stops |
| `--policy` | how the bus decides where to go next; `--list-policies` prints them with a one-line description each |
| `--no-viewer` | skip the chase-camera window, which is faster but leaves nothing to watch |
| `--npc-vehicles` | how much other traffic shares the town |
| `--max-requests`, `--demand-interval` | how many passengers appear, and how often |

With `--backend carla` the server must already be up — see
[The CARLA server](#the-carla-server) below. If it is not, the demo says so and
prints the command to start it rather than raising a traceback.

**The demo records nothing.** It calls `run_trial` directly rather than
`run_campaign`, so it writes nothing to `results/runs` and cannot collide with
a campaign's run identities. It is a demonstration, not a measurement:
reproducible numbers come from the notebooks below.

`run.sh` is a thin wrapper around `python`, so it works for any script in the
project, not just this one — anything that would normally follow `python` on
the command line goes after the script name.

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

## The CARLA server

**How the server is started is out of scope for this project.** Nothing here
launches, configures or manages it: the code takes a host and a port and
connects. Any 0.9.16 server reachable on that address will do, however you
choose to run it.

!!! note "What this was developed against"
    CARLA **0.9.16**, run in Docker. The version matters — the client wheel
    is pinned to it, and the `agents` package is taken from the matching
    image — but nothing below assumes Docker, or any particular way of
    bringing the server up.

To confirm a server is live and usable before blaming the code:

```bash
./run.sh -c "import carla; c = carla.Client('localhost', 2000); \
c.set_timeout(10.0); print(c.get_server_version(), \
c.get_world().get_map().name)"
```

Section 1 of `notebooks/carla-campaign-towns.ipynb` does the fuller version —
it loads every installed town and reports road length, junction share, speed
signs and, decisively, whether the town has Shoulder lanes to author stops
against.

**Whatever town the server starts on is almost certainly not the one you
want.** That matters for more than convenience — see the traffic manager
warning below.

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
    and **never re-caches it** when a new map is loaded. One created before
    the town you want is loaded will steer vehicles around the *previous*
    map's waypoints — measured, 0 of 5 stops reached and off any lane for
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

### If the server looks frozen

The world has been left in synchronous mode with nobody ticking it, usually by
an interrupted run — the server is waiting for a tick that is never coming.
The preflight cell in `notebooks/carla-demo.ipynb` restores asynchronous mode
and clears stray actors; it is safe to run at any time.
