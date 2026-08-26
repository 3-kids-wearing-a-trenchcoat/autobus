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
| `00-overview.ipynb` | The whole system in one run | no | 30 s | 30 s |
| `01-the-map.ipynb` | Map sources and the transit graph | no | 1 min | 1 min |
| `02-demand-and-kpis.ipynb` | Requests, patience, and how the KPIs mislead | no | 2 min | 3 min |
| `03-policy-shootout.ipynb` | All nine routing policies compared | no | 3 min | 15 min |
| `04-tuning.ipynb` | The knobs, and two ideas that failed | no | 5 min | 30 min |
| `05-campaign.ipynb` | The headline result and its statistics | no | 10 min | ~1 h |
| `06-scope-condition.ipynb` | Where routing stops paying | no | 10 min | 1 h+ |
| `07-carla-driving.ipynb` | How the bus drives a real town | **yes** | 5 min | 5 min |
| `08-carla-campaign.ipynb` | The headline result on real geometry | **yes** | ~1 h | hours |
| `09-harness.ipynb` | Reproducibility, storage, parallelism, latency | no | 3 min | 10 min |

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
docker compose -f compose_software.yml up -d
```

Every cell that needs it checks first and skips itself with a message, so both
notebooks can be read end to end without one.
