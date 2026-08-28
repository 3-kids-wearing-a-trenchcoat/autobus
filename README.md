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
| Python | 3.12 (installed by conda, below) |
| Conda | [Miniforge](https://github.com/conda-forge/miniforge) or Anaconda |
| Docker | for the CARLA server, and to install CARLA's `agents` package |
| CARLA | 0.9.16, pulled as a Docker image — nothing to install by hand |
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

Nothing else needs installing. Do not `pip install` into the environment by
hand; add to `environment.yml` instead so the next person gets it too.

### Checking it worked

```bash
./run.sh -m pytest         # 748 tests, a few seconds, no server needed
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

### The demo

`main.py` runs one trial and prints how the service did. By default it opens
a chase-camera window and drives in CARLA, so it needs a running server
(see below):

```bash
./run.sh main.py                                   # Town01, L1 policy
./run.sh main.py --town TAU --policy fixed         # a different town, baseline
./run.sh main.py --backend mock                    # no server needed at all
./run.sh main.py --no-viewer --max-requests 8      # headless and quick
./run.sh main.py --help                            # every flag
```

| flag | what it changes |
|---|---|
| `--backend {carla,mock}` | drive a real bus, or simulate travel on the graph |
| `--town {TAU,Town01,Town02,Town03,Town05}` | which town, CARLA only |
| `--policy {fixed,nearest,window,reordering}` | how the bus chooses where to go |
| `--capacity`, `--window`, `--patience`, `--dwell` | the service being offered |
| `--no-viewer`, `--no-markers`, `--no-scenery` | what you see while it runs |

**The demo is a demonstration, not a measurement.** It records nothing and
is not seeded end-to-end, so its numbers vary run to run. Reproducible
figures come from the notebooks.

### Experiments

```bash
./jupyter-run.sh          # JupyterLab on port 8889, hash seed pinned
```

35 notebooks under `notebooks/` hold the experiments and their conclusions.
`results.ipynb` and `results-explorer.ipynb` read the stored results and run
nothing, which is the place to start looking.

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
so any 0.9.16 server reachable there will do. A local installation run
straight from `CarlaUE4.sh` is as good as a container. The one thing it asks
is that ports **2000** and **8000** are reachable, so the client and the
traffic manager land where every default looks for them; a container wants
host networking, or those two ports mapped.

**This project was developed against a server in Docker**, and that is the
only reason the commands below are Docker commands. The configuration used is
`compose.yml`, which builds `docker/carla/Dockerfile`:

```bash
docker compose up -d              # build if needed, then start
docker compose logs -f carla      # watch it come up
docker compose down               # when finished
```

**GPU passthrough is the part that varies, and the part that fails.** It
depends on your hardware, not on this project: an NVIDIA host wants
`--gpus all`, others pass the render node through with `--device /dev/dri`
and may need the host's own Vulkan ICD and driver libraries mounted into the
container. If the server exits or segfaults within seconds of starting, that
is almost always what is missing — it is not a problem with the simulation.
That is what the Dockerfile above is for: the same CARLA 0.9.16 payload on a
base whose Mesa is new enough for the GPUs this was developed on. A local
installation sidesteps the question entirely, since it uses the host's own
driver.

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
notebooks/          the experiments and their conclusions
autobus-docs/       the documentation site
map_osm/            OpenStreetMap source for the generated TAU map
```

Dependencies flow one way: `map ← algorithm ← metrics`, with `simulation`
and then `experiment` above, and `preparation` at the top depending on
everything and depended on by nothing.

## Notes worth knowing

* **Two towns, two kinds.** CARLA ships Town01–Town05 baked into the server.
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
* **Concepts** — the map layer, requests, demand, dispatch policies, the
  travel model, the bus agent, metrics, the simulation backend, navigating in
  CARLA, and watching a run
* **API Reference** — generated from docstrings
