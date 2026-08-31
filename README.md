# Autobus

An autonomous bus that decides **which stops to visit, and in what order**,
from passenger requests as they arrive — rather than driving a fixed
timetable.

Requests appear over the course of a run, each naming a pickup and a
dropoff. A dispatch policy re-plans the route whenever the bus reaches a
stop, subject to a seat capacity and a promised service window. The same
agent, policy, demand generator and metrics run over two very different
worlds:

* a **planar graph**, which needs nothing installed and runs thousands of
  trials in minutes; and
* **[CARLA](https://carla.org/)**, where a real vehicle drives a real town
  and the travel times are whatever the driving actually costs.

Only the backend differs between them, which is what makes it possible to
develop against the fast world and check the conclusions against the slow
one.

---

## Requirements

| | |
|---|---|
| OS | Linux throughout. Windows works with two documented degradations — see [On Windows](#on-windows) |
| Python | 3.12 (installed by conda, below) |
| Conda | [Miniforge](https://github.com/conda-forge/miniforge) or Anaconda |
| Docker | to install CARLA's `agents` package, and to run the server if that is how you run it |
| CARLA | 0.9.16, reachable on `localhost:2000` — however you choose to run it |
| GPU | only for CARLA. The graph world needs no graphics at all |

The graph world needs conda alone. Docker and a GPU only matter once you
want to watch a bus drive.

## Installation

```bash
conda env create -f environment.yml   # creates the "autobus" environment
bash install-carla-agents.sh          # CARLA's agents package
```

**Both steps are needed.** The `carla` wheel on PyPI is client bindings
only — `agents.navigation`, which contains the `BasicAgent` that actually
drives the bus, exists only inside the CARLA Docker image. The second script
extracts it from the pinned image into the environment and verifies the
import. It is idempotent, needs Docker, and is deliberately used instead of
vendoring ~1,800 lines of someone else's code into this repository.

**Without Docker**, the same directory ships inside a local CARLA
installation, at `PythonAPI/carla/agents`. Copying it into the environment's
site-packages does the script's job by hand:

```bash
./run.sh -c "import site; print(site.getsitepackages()[0])"   # where it goes
cp -r <CARLA_ROOT>/PythonAPI/carla/agents <that directory>/
```

Nothing else needs installing. Do not `pip install` into the environment by
hand; add to `environment.yml` instead so the next person gets it too.

### Checking it worked

```bash
./run.sh -m pytest         # the whole suite, seconds, no server needed
```

If that passes, the graph world is fully working. The suite deliberately
needs no CARLA server: the map layer reads dumped town data from JSON, so a
CARLA town can be routed, tested and experimented on with nothing running.

## Running things

Everything goes through **`./run.sh`**, which runs inside the right conda
environment and pins two variables that matter:

* `PYTHONHASHSEED=0` — several policies break ties by iterating a `set[str]`,
  so without a pinned hash seed the *same seed* produces a different route in
  a different process. Seeded runs refuse to start without it.
* `PYTHONUNBUFFERED=1` — otherwise a long run prints nothing until it
  finishes, and a crash loses everything it had to say.

Pass it whatever you would pass `python`:

```bash
./run.sh main.py                       # the demo
./run.sh -m pytest                     # the tests
./run.sh -m src.preparation.carla_dump --town Town01
```

### On Windows

Every entry point here is a shell script, so the shortest route is **Git Bash
or WSL**, where `./run.sh` and the rest work unchanged. Running them from
PowerShell or `cmd` means doing by hand the two things `run.sh` does for you:

| | PowerShell | `cmd` |
|---|---|---|
| pin the hash seed | `$env:PYTHONHASHSEED = "0"` | `set PYTHONHASHSEED=0` |
| see output as it happens | `$env:PYTHONUNBUFFERED = "1"` | `set PYTHONUNBUFFERED=1` |
| then | `conda run --no-capture-output -n autobus python main.py` | same |

**The first row is not optional.** Several policies break ties by iterating a
`set[str]`, so an unpinned hash seed makes the same seed produce a different
route in a different process. `run_trial` *refuses to start* without it rather
than return a number it cannot reproduce, so a run launched any other way
stops with a `RuntimeError`. JupyterLab needs it set the same way before
launching, and `jupyter lab --no-browser --port=8889` is the rest of
`jupyter-run.sh`. The ten submission notebooks put the repository root on the
import path themselves, so `PYTHONPATH` matters only for the older notebooks
in `notebooks/`.

⚠ **Do not set `PYTHONHASHSEED` from inside Python** to get past the check.
The value is read once when the interpreter starts; assigning to
`os.environ` afterwards changes nothing about hashing and only silences the
guard — which turns a loud refusal into a quietly wrong answer, the exact
failure it exists to prevent.

`install-carla-agents.sh` needs bash and `docker cp`. The manual route in
[Installation](#installation) is the same job done by hand:

```
copy /E /I "<CARLA_ROOT>\PythonAPI\carla\agents" "<site-packages>\agents"
```

Two things genuinely do not work on Windows, both by design rather than
oversight:

* **`n_jobs` runs sequentially.** The worker pool needs the `fork` start
  method, which Windows does not have. `run_campaign` warns and runs on one
  core; the results are identical, because `n_jobs` cannot reach an answer —
  notebooks `05`, `06` and `09` simply take longer.
* **`route_drops` is not measured.** Attributing CARLA's traffic-manager
  diagnostics means capturing file descriptor 1 through glibc, which has no
  Windows equivalent. The counter is *absent* from the run's stats rather
  than reported as zero, and the messages still reach the terminal untagged.
  Nothing else about a CARLA run changes.

### The demo

`main.py` runs one trial and prints how the service did. By default it opens
a chase-camera window and drives in CARLA, so it needs a running server
(see below):

```bash
./autobus.sh                                   # Town01, L1 policy
./autobus.sh --town TAU --policy fixed         # a different town, baseline
./autobus.sh --backend mock                    # no server needed at all
./autobus.sh --no-viewer --max-requests 8      # headless and quick
./autobus.sh --help                            # every flag
```

`autobus.sh` is `run.sh` aimed at `main.py`, so `./run.sh main.py --policy
fixed` is the same command written the long way. It resolves its own
directory, so unlike the long form it can be run from anywhere.

| flag | what it changes |
|---|---|
| `--backend {carla,mock}` | drive a real bus, or simulate travel on the graph |
| `--town {TAU,Town01,Town02,Town03,Town05}` | which town, CARLA only |
| `--policy {fixed,nearest,window,reordering}` | how the bus chooses where to go |
| `--capacity`, `--window`, `--patience`, `--dwell` | the service being offered |
| `--no-viewer`, `--no-markers`, `--no-scenery` | what you see while it runs |

**The demo is a demonstration, not a measurement.** It calls `run_trial`
directly rather than `run_campaign`, so it records nothing and cannot collide
with a campaign's run identities. It is seeded — `--seed` drives demand,
CARLA traffic and the mock map — but a CARLA drive does not reproduce to the
last digit regardless, so its numbers vary run to run. Reproducible figures
come from the notebooks.

### Experiments

```bash
./jupyter-run.sh          # JupyterLab on port 8889, hash seed pinned
```

The ten notebooks under `notebooks/` reproduce every result quoted in the
submitted documents. They are meant to be read in order, but each one stands
alone:

| notebook | subject | needs a server |
|---|---|---|
| `00-overview` | the whole system in one run | no |
| `01-the-map` | map sources and the transit graph | no |
| `02-demand-and-kpis` | requests, patience, and how the KPIs mislead | no |
| `03-policy-shootout` | all nine routing policies compared | no |
| `04-tuning` | the knobs, and two ideas that failed | no |
| `05-campaign` | the headline result and its statistics | no |
| `06-scope-condition` | where routing stops paying | no |
| `07-carla-driving` | how the bus drives a real town | **yes** |
| `08-carla-campaign` | the headline result on real geometry | **yes** |
| `09-harness` | reproducibility, storage, parallelism, latency | no |

Every notebook that runs experiments (`02` onward) carries a `SCALE` switch.
`"quick"` is the default: minutes rather than hours, the same conclusions
with wider error bars. `"full"` is the sample size behind the submitted
figures, and on `04`, `05` and `08` that is hours. A second run of any of
them costs almost nothing — every trial is found in the results store rather
than repeated.

Completed runs are written once each to `results/runs/`, keyed by a hash of
what produced them, so re-running a campaign skips what is already recorded.
**That directory is not in git** — it is large and regenerable, and the
notebooks are the record.

### Documentation

```bash
./docs.sh                 # mkdocs, http://127.0.0.1:8000
```

The concept pages are where the reasoning lives; the API reference is
generated from docstrings.

## Running CARLA

The client talks to a CARLA **0.9.16** server on `localhost:2000`. **How you
start that server is up to you.** The code only ever opens a connection to
that address — it neither launches the server nor knows how it was launched —
so any 0.9.16 server reachable there should do. The one thing it asks is that
ports **2000** and **8000** are reachable, so the client and the traffic
manager land where every default looks for them; a container wants host
networking, or those two ports mapped.

Everything here was developed and measured against a **containerised** server.
A local installation run straight from `CarlaUE4.sh` ought to serve just as
well — the client cannot tell the difference — but it was never tried here, so
that route is yours to test.

Two things are worth knowing once it is up:

* **The server is shared.** There is one per machine, and loading a town
  replaces whatever world is currently up. Check nobody else is mid-run
  before starting, stopping or loading anything.
* **It must be able to render, but need not show you anything.** The chase
  camera is a CARLA sensor, so it renders server-side and ships frames to the
  client; `-RenderOffScreen` is fine and a headless server is fine. A server
  started with rendering disabled entirely produces no frames at all.

### Preparing a town

Three tools need a live server, and each is a one-off whose output is
committed:

```bash
./run.sh -m src.preparation.carla_dump      --town Town01
./run.sh -m src.preparation.carla_calibrate --town Town01 --tours 3 --write
./run.sh -m src.preparation.osm_buildings   --town TAU \
    --osm map_osm/TelAvivUniversity.osm --write
```

They produce `<Town>.topology.json` (the road network), the town's
`traffic_efficiency` (how much slower a real drive is than the free-flow
estimate) and `<Town>.props.json` (scenery for a generated town). None of
them run at trial time.

⚠ **`traffic_efficiency` is fitted by driving**, so it belongs to the way the
bus was driven when it was measured. Change how the bus drives and every
town must be re-calibrated, and the `steering` tag in the run identity bumped
so old and new results cannot pool.

## Layout

```
src/map/            the world: providers, the routing graph, town data
src/algorithm/      the routing: policies, the bus agent, the request pool
src/simulation/     the worlds: backends, the runner, demand, the viewer
src/metrics/        KPIs
src/experiment/     trials, campaigns, the results store, plots
src/preparation/    offline tools; need a live server, nothing imports them
main.py             the demo
notebooks/          the ten notebooks behind the submitted results
autobus-docs/       the documentation site
map_osm/            OpenStreetMap source for the generated TAU map
```

Dependencies flow one way: `map ← algorithm ← metrics`, with `simulation`
and then `experiment` above, and `preparation` at the top depending on
everything and depended on by nothing.

## Notes worth knowing

* **Two kinds of town.** CARLA ships Town01–Town05 baked into the server.
  **TAU** is different: it is built from OpenStreetMap, exists only as an
  OpenDRIVE file, and is generated procedurally on every load. A town is
  "generated" when a `.xodr` sits beside its dump — no list in code.
* **A generated town has no scenery of its own** — no terrain, no buildings,
  and its road mesh is not even drawn unless asked for. The demo dresses it;
  measured runs do not, and cannot be affected by it.
* **CARLA results are not independent of server history.** A world carried
  from one trial into the next is the classic contaminant. When numbers
  matter, start from a fresh server.
* **Not everything here is settled.** `roadmap.md` tracks what is done, what
  is deliberately out of scope, and the traps that cost the most time.

## Documentation

Full documentation is in `autobus-docs/`, served by `./docs.sh`:

* **[Running the Project](autobus-docs/docs/running.md)** — setup and
  execution in more detail than this file
* **Concepts** — the map layer, requests, demand, dispatch policies, window
  insertion and route reordering, the travel model, the bus agent, metrics,
  the simulation runner and backend, the experiment harness, navigating in
  CARLA, and watching a run
* **API Reference** — generated from docstrings
