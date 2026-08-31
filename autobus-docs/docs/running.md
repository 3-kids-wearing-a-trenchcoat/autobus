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
place the project itself invokes Docker**, and it does so to fetch a Python
package rather than to run anything. Running the CARLA server usually means
Docker too, but that is your choice and outside this project — see
[The CARLA server](#the-carla-server).

!!! note "Why it is installed rather than copied into the repository"
    Vendoring it would put ~1,800 lines of someone else's code under this
    project's ownership for no benefit. Taking it from the **pinned image**
    means it cannot drift out of step with the version the client wheel is
    pinned to, and it stays a dependency like any other. The cost is this
    extra setup step.

## Running the demo

`main.py` is the entry point for *seeing* the system work. It runs a single
simulation — by default in a CARLA town, with a chase-camera window following
the bus.

It prints the parameters it is about to run under, then a live progress line
while it runs, then a summary. The progress line bars requests that have
reached a final state and carries the clock beside them, because those are
the two ways a run can end; the closing block says which of them actually
stopped it. **The progress line goes to stderr**, so redirecting stdout to
capture the report leaves it on screen (and vice versa).

Use `autobus.sh`, which runs the demo inside the correct Conda environment:

```bash
./autobus.sh
```

It is `run.sh` aimed at `main.py` and passes every flag straight through, so
`bash run.sh main.py` is the same command written the long way. It resolves
its own directory rather than assuming one, so it can be run from anywhere.

`python main.py` works too, as long as the Conda environment is already
active — the demo does not insist on a launcher the way the notebooks and
campaigns do. The difference is reproducibility: `autobus.sh` pins
`PYTHONHASHSEED` by way of `run.sh`, and without it another process may serve
the same passengers in a different order. The demo says so and runs anyway, which is
the right trade for a demonstration and the wrong one for a measurement.

That on its own is a complete demo. Every option has a default defined at the
top of `main.py`, and every default can be overridden by a flag; a flag you do
not pass keeps its default, so this changes the routing policy and nothing
else:

```bash
./autobus.sh --policy fixed
```

`./autobus.sh --help` lists every flag with its default. The ones worth
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
the repository. Notebooks live in the `notebooks/` directory, and each carries a
bootstrap cell that finds the repository root itself, so the imports work however
the file was opened.

Two conventions hold across all of them:

* **A notebook that produces runs files them in the store and resumes.** Each
  completed trial is written to `results/runs` as it goes, so a campaign can be
  stopped whenever you like and re-running continues rather than restarting. A
  notebook that only reads the store runs no simulation, so it works while a
  campaign is still going.
* **Only the ones that drive a bus need a live server.** Everything on the
  graph — and everything that reads a CARLA town from its committed JSON — runs
  with nothing up. See [The CARLA server](#the-carla-server).

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

## Running on Windows

Everything above is written for a shell, because every entry point in this
project is one. **Git Bash or WSL runs them unchanged** and is the shortest
route. From PowerShell or `cmd`, the launchers have to be unpacked by hand —
they are thin, and the only part that really matters is the hash seed:

In PowerShell:

```powershell
$env:PYTHONHASHSEED = "0"
$env:PYTHONUNBUFFERED = "1"
conda run --no-capture-output -n autobus python main.py
conda run --no-capture-output -n autobus python -m pytest
conda run -n autobus jupyter lab --no-browser --port=8889
```

In `cmd`, the same three variables are set with `set NAME=value`:

```bat
set PYTHONHASHSEED=0
set PYTHONUNBUFFERED=1
conda run --no-capture-output -n autobus python main.py
```

The ten submission notebooks find the repository root themselves, so
`PYTHONPATH` is only needed for the older notebooks in `notebooks/`; set it
to the project root as `jupyter-run.sh` does.

!!! danger "Never satisfy the hash-seed check from inside Python"
    Setting `os.environ["PYTHONHASHSEED"]` in a notebook cell makes
    [the refusal](#reproducibility-set-pythonhashseed) go away without making
    anything reproducible. The interpreter reads that variable once, at
    startup; assigning to it later changes no hashing at all. That converts a
    loud, correct refusal into a silently wrong answer — which is precisely
    the failure the check was built to prevent. Set it **before** the
    interpreter starts, or use `AUTOBUS_ALLOW_UNPINNED_HASH=1` and do not
    file the results.

`install-carla-agents.sh` needs bash and `docker cp`, so on Windows do its job
directly — the `agents` directory also ships inside a local CARLA install:

```bat
copy /E /I "<CARLA_ROOT>\PythonAPI\carla\agents" "<site-packages>\agents"
```

`conda run -n autobus python -c "import site; print(site.getsitepackages()[0])"`
prints where that second path is.

### What is degraded, and why

Two things do not work on Windows. Both fail *softly* and by design; neither
touches a KPI.

* **Campaigns run on one core.** `run_campaign`'s worker pool requires the
  `fork` start method, which Windows does not offer. `spawn` cannot replace
  it: the workers inherit their whole context through the fork instead of
  being handed a picklable payload, and the policy factories are lambdas,
  which do not pickle. So a request for `n_jobs > 1` is downgraded to 1 with
  a `RuntimeWarning`. Because `n_jobs` is a scheduling choice that cannot
  reach a result, the campaign still produces every run it was asked for and
  the numbers are identical — it just takes longer.

* **`route_drops` is not measured.** Attributing the traffic manager's
  diagnostics means capturing file descriptor 1 through glibc — see
  [navigating in CARLA](concepts/carla_navigation.md) — and there is no
  Windows equivalent: the UCRT does not export `stdout` as data, and
  `select` there accepts sockets alone. `carla_stdout` reports itself
  unavailable and the messages reach the terminal untagged, exactly as they
  would have without the module. The counter is then **absent** from the
  run's `backend_stats` rather than reported as zero, so a table shows a gap
  rather than claiming a clean drive nobody measured.

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

A fuller survey is worth doing before authoring stops in a town that has not
been used before: load every installed town and report its road length, junction
share, speed signs and — decisively — whether it has Shoulder lanes to author
stops against.

**Whatever town the server starts on is almost certainly not the one you
want.** That matters for more than convenience — see the traffic manager
warning below.

Most of the project needs no server at all. `CarlaMapProvider` reads a town's
roads and stops from version-controlled JSON, so a CARLA town can be routed,
tested and experimented on with nothing running:

```python
run_trial(policy_factory=..., provider=CarlaMapProvider("Town01"))
```

Only four things need a live server: driving the bus with
[`CarlaBackend`](concepts/simulation_backend.md#carlabackend), re-dumping a
town's roads and speed limits with `src.preparation.carla_dump`, fitting a
town's `traffic_efficiency` with `src.preparation.carla_calibrate` — which
measures it *by driving*, so it must be re-run after any change to how the bus
is driven — and re-deriving a generated town's scenery with
`src.preparation.osm_buildings`.

The scenery step is a one-off, and its output is committed:

```bash
./run.sh -m src.preparation.osm_buildings --town TAU \
    --osm map_osm/TelAvivUniversity.osm --write
```

That writes `src/map/carla_maps/TAU.props.json`, which the demo reads with no
server involvement beyond the one it is already driving in. **Nothing needs
re-running to see the scenery** — `./autobus.sh --town TAU` picks it up
as it stands. Pass `--no-scenery` to turn it off.

To watch the bus drive, run the demo: `./autobus.sh` runs a single trial
with the [viewer](concepts/watching_a_run.md) on, which is its default.

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
Putting it back into asynchronous mode and clearing the actors the interrupted
run stranded is safe at any time:

```bash
./run.sh -c "
import carla
world = carla.Client('localhost', 2000).get_world()
settings = world.get_settings()
settings.synchronous_mode = False
settings.fixed_delta_seconds = None
world.apply_settings(settings)
for pattern in ('sensor.*', 'vehicle.*', 'walker.*'):
    for actor in world.get_actors().filter(pattern):
        actor.destroy()
"
```

Remember that there is one server per machine — this affects whatever else is
using it, so check nobody is mid-run first.
