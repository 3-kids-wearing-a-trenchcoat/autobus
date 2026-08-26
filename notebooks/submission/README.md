# Notebooks

These ten notebooks reproduce every result quoted in the submitted documents.
They are meant to be read in order, but each one stands alone.

```bash
./jupyter-run.sh        # starts JupyterLab in the right conda environment
```

Use `jupyter-run.sh` rather than `jupyter lab`: it selects the conda
environment and pins `PYTHONHASHSEED`, without which seeded runs are refused.
Each notebook also has a bootstrap cell that finds the repository root itself,
so the imports work however the file was opened.

| Notebook | Subject | Needs a server | Quick | Full |
|---|---|---|---|---|
| `00-overview.ipynb` | The whole system in one run | no | 10 s | 10 s |
| `01-the-map.ipynb` | Map sources and the transit graph | no | 5 s | 5 s |
| `02-demand-and-kpis.ipynb` | Requests, patience, and how the KPIs mislead | no | 3 min | 4 min |
| `03-policy-shootout.ipynb` | All nine routing policies compared | no | 4 min | 20 min |
| `04-tuning.ipynb` | The knobs, and two ideas that failed | no | 33 min | 2 h+ |
| `05-campaign.ipynb` | The headline result and its statistics | no | 25 min | ~2 h |
| `06-scope-condition.ipynb` | Where routing stops paying | no | 20 min | 1 h+ |
| `07-carla-driving.ipynb` | How the bus drives a real town | **yes** | 1 min | 1 min |
| `08-carla-campaign.ipynb` | The headline result on real geometry | **yes** | 40 min | hours |
| `09-harness.ipynb` | Reproducibility, storage, parallelism, latency | no | 2 min | 10 min |

These are what a **first** run costs on a 16-core desktop with an empty
results store. Most are wall-clock from the executed copies in this folder;
`05` and `08` are scaled up from theirs, because those two reused trials
already recorded and so finished far quicker than an examiner's first run will.

A second run of any of them costs almost nothing — every trial is found in the
store rather than repeated. `04` and `05` are the two that take real time on
the graph: `04` sweeps six service windows across two policies, and `05` runs
480 trials over six maps.

## Two sizes of everything

Every notebook that runs experiments (`02` onward) has a `SCALE` switch:

* `"quick"` — the default. Minutes rather than hours; same conclusions, wider
  error bars.
* `"full"` — the sample sizes behind the figures in the submitted documents.
* `"smoke"` — a tiny end-to-end check that the notebook runs at all.

Set it in the notebook, or from the environment:

```bash
AUTOBUS_SCALE=full ./jupyter-run.sh
```

**The saved outputs were produced at `"full"`.** Re-running gives `"quick"`
unless you change it.

## Results are regenerated, not shipped

`results/runs/` is empty in this copy, so nothing below is pre-baked. Runs are
filed there as they complete, identified by a hash of everything that affects
their outcome, which means:

* an interrupted notebook resumes where it stopped;
* raising `SCALE` afterwards runs only what is missing;
* re-running a finished notebook executes nothing.

To keep them somewhere else, set `AUTOBUS_RUNS_DIR`.

## The CARLA notebooks

`07` and `08` need a CARLA server:

```bash
docker compose up -d
```

Every cell that needs it checks first and skips itself with a message, so both
notebooks can be read end to end without one.
