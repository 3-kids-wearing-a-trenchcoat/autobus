# Experiment Helpers

## Overview

The `experiment` package provides utilities for comparing routing policies
systematically in JupyterLab notebooks. It handles the boilerplate of wiring
up the simulation stack, running multiple trials, and producing summary plots,
so notebooks can focus on what differs between strategies rather than on
infrastructure.

The package has three layers:

1. **`run_trial`** — runs a single simulation and returns structured results.
2. **`compare_policies`** — runs every policy for *n* trials on the same map.
   **`summarize_comparison`** wraps it and returns a `ComparisonSummary`
   holding a Markdown table of each property's mean across trials with its
   margin of error (as a percentage of the mean), plus the raw results for
   the plot helpers.
3. **Plot helpers** — turn the comparison results into charts.

## TrialResult

`TrialResult` is a dataclass that collects everything produced by one
simulation run:

| Field | Type | Meaning |
|-------|------|---------|
| `kpi` | `KPISnapshot` | Aggregate KPIs at the end of the run. |
| `tick_history` | `list[TickSnapshot]` | Per-tick occupancy and queue snapshots. |
| `wait_times` | `list[float]` | Wait time (seconds) for each fulfilled request. |
| `ride_times` | `list[float]` | Ride time (seconds) for each fulfilled request. |
| `animation_frames` | `list[AnimationFrame]` | Per-stop-arrival snapshots for animation. Empty unless `record_animation=True` was passed to `run_trial`. |
| `transit_graph` | `TransitGraph \| None` | The graph used during the run; `None` unless `record_animation=True`. |

`wait_times` and `ride_times` expose the per-request distributions that
`KPISnapshot` only summarises, making it possible to do full distributional
comparisons across policies.

## run_trial

`run_trial` builds and runs the complete simulation stack from a policy
factory and a pre-built street graph:

```python
from src.experiment import run_trial
from src.algorithm.FixedRoutePolicy import FixedRoutePolicy
from src.map.graph import PlanarStreetGraph

graph = PlanarStreetGraph.generate(n_seeds=20, bounds=(0, 0, 5000, 5000), seed=42)

result = run_trial(
    policy_factory=lambda g: FixedRoutePolicy(g),
    graph=graph,
    seed=0,
)
print(result.kpi.mean_wait_time)
```

The `policy_factory` argument is a callable that accepts a `TransitGraph` and
returns a `DispatchPolicy`. Using a factory (rather than a policy instance)
lets `run_trial` construct fresh policy state for every call while keeping the
same underlying street graph.

`seed` controls the demand sequence: it is passed to Python's `random` module
before any demand is generated, making the pickup/dropoff stop selections
deterministic. It has no effect on map generation, which is seeded separately
in `PlanarStreetGraph.generate`.

Passing `silent=True` suppresses the per-run summary printed by
`SimulationRunner`; this is the default when called from `compare_policies`.

Passing `record_animation=True` enables animation recording (see
[`animate_trial`](#animate_trial) below). This has no effect on the KPI or
tick-history data.

## compare_policies

`compare_policies` runs every policy for a fixed number of trials on the same
map and returns the results grouped by policy label:

```python
from src.experiment import compare_policies
from src.algorithm.FixedRoutePolicy import FixedRoutePolicy
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy

results = compare_policies(
    policies=[
        ("Fixed route",       lambda g: FixedRoutePolicy(g)),
        ("Nearest unserved",  lambda g: NearestUnservedPolicy(g)),
    ],
    graph=graph,
    n_trials=5,
    base_seed=42,
)
```

The return value is `dict[str, list[TrialResult]]`, mapping each label to its
list of trial results.

While running, `compare_policies` displays one tqdm progress bar per policy that
fills as trials complete. Pass `progress=False` to run silently.

### Seed strategy

Fair comparison requires that all policies face the same demand within a given
trial. `compare_policies` achieves this by using the same seed for every
policy at a given trial index: trial *i* uses `base_seed + i` as the demand
seed, regardless of which policy is being run.

```
trial 0: seed = base_seed + 0  →  all policies see the same 100 requests
trial 1: seed = base_seed + 1  →  all policies see a different but shared sequence
...
```

When `base_seed` is `None`, all trials are non-deterministic.

### Forwarding parameters to run_trial

Any keyword argument not listed in `compare_policies`'s own signature is
forwarded to `run_trial`. This covers simulation parameters such as
`max_requests`, `capacity`, `dt`, and `max_time`:

```python
results = compare_policies(
    policies=[...],
    graph=graph,
    n_trials=10,
    base_seed=0,
    max_requests=200,
    capacity=8,
)
```

## summarize_comparison

`summarize_comparison` runs the same multi-trial experiment as
`compare_policies` and packages the outcome for quick reading. It returns a
`ComparisonSummary` dataclass with two attributes:

- `markdown` — a Markdown table with one row per measured property and one
  column per policy. Each cell is `mean unit (±X%)`, where `X%` is the **margin
  of error as a percentage of the mean** (the relative half-width of the 95%
  confidence interval, from the Student *t*-distribution). A smaller percentage
  means a more reliable estimate.
- `results` — the raw `dict[str, list[TrialResult]]` (exactly what
  `compare_policies` returns), so the plot helpers can visualise the same runs
  without re-running the simulation.

It takes the same arguments as `compare_policies` (`policies`, `graph`,
`n_trials`, `base_seed`, `progress`, and any forwarded `run_trial` keywords),
plus a `confidence` level (default `0.95`) for the margin of error.

```python
from IPython.display import Markdown
from src.experiment import summarize_comparison

summary = summarize_comparison(
    policies=[
        ("Fixed route",      lambda g: FixedRoutePolicy(g)),
        ("Nearest unserved", lambda g: NearestUnservedPolicy(g)),
    ],
    graph=graph,
    n_trials=200,
    base_seed=0,
    max_requests=100,
)

Markdown(summary.markdown)                 # render the comparison table
plot_kpi_bars(summary.results, fields=["mean_wait_time", "throughput"])
```

## Plot helpers

All three plot functions accept the `dict[str, list[TrialResult]]` returned
by `compare_policies` and return a `matplotlib.figure.Figure`.

### plot_kpi_bars

Bar chart comparing one or more aggregate KPI fields across policies. Each
field gets its own subplot, and subplots wrap into a grid (at most `max_cols`
columns) so the figure keeps a sensible aspect ratio instead of one very wide
row. Error bars show ±1 standard deviation and are omitted when only one trial
was run. Pass `title` to add a figure-level heading.

```python
from src.experiment import plot_kpi_bars

fig = plot_kpi_bars(
    results,
    fields=["mean_wait_time", "throughput", "fulfillment_rate"],
)
fig.show()
```

Valid field names are the attribute names of `KPISnapshot`:
`mean_wait_time`, `p90_wait_time`, `max_wait_time`, `mean_ride_time`,
`mean_journey_time`, `fulfillment_rate`, `throughput`, `fulfilled_count`,
`cancelled_count`.

### plot_wait_distribution

Box plot of per-request wait times. All trials for a policy are pooled into a
single distribution before plotting, so the boxes represent the combined
sample across all runs.

```python
from src.experiment import plot_wait_distribution

fig = plot_wait_distribution(results)
fig.show()
```

### plot_occupancy

Line plot of bus occupancy and pending request count over simulation time. One
subplot pair (occupancy / pending) is drawn per policy. When multiple trials
are available, the per-tick mean across trials is drawn as a solid line with a
shaded band spanning ±1 standard deviation (the run-to-run spread); with a
single trial only its line is shown. This keeps the plot readable at large
trial counts, where overplotting every run would be an unreadable blur.

The mean and band are computed over the shortest common tick prefix: if trials
end at different times they are trimmed to the length of the shortest trial.

```python
from src.experiment import plot_occupancy

fig = plot_occupancy(results)
fig.show()
```

## animate_trial

`animate_trial` builds a `matplotlib.animation.FuncAnimation` from the
per-stop-arrival frames recorded by `run_trial`.  Each frame in the animation
corresponds to one stop arrival; the view never changes during transit. It's
the lower-level builder underneath `show_animation` and `show_animations`
below; use it directly when you want the raw `FuncAnimation` object, e.g. to
export a static video/HTML file rather than display it live.

```python
from src.experiment import run_trial, animate_trial
from IPython.display import HTML

result = run_trial(
    policy_factory=lambda g: NearestUnservedPolicy(g),
    graph=graph,
    seed=42,
    record_animation=True,
)

anim = animate_trial(result.animation_frames, result.transit_graph)
HTML(anim.to_jshtml())
```

The animation displays:

- **Road network** — all road edges drawn as light gray lines.
- **Bus stops** — circles whose size and fill colour encode the number of
  waiting passengers (small/white = none, large/steelblue = many). A numeric
  count label appears above each stop when the count is non-zero.
- **Destination rings** — hollow orange rings drawn around stops that are
  the dropoff target for at least one onboard passenger, showing the bus's
  current obligations at a glance.
- **Bus marker** — a black dot at the stop the bus just reached, with an
  arrow indicating the direction of travel.
- **HUD** — text in the top-left corner showing onboard passenger count and
  elapsed simulation time in minutes.

`interval_ms` (default 300) controls playback speed.  To save the animation
as a video file, use `anim.save("run.mp4")` (requires `ffmpeg`).

## show_animation

`show_animation` is a one-liner for the common case: build and display a
single trial's animation live, right in the notebook cell:

```python
from src.experiment import run_trial, show_animation

result = run_trial(
    policy_factory=lambda g: NearestUnservedPolicy(g),
    graph=graph,
    seed=42,
    record_animation=True,
)

show_animation(result)
```

It raises `ValueError` if `result` wasn't produced with
`record_animation=True` (i.e. `animation_frames`/`transit_graph` are empty).
If running under IPython, it switches to the `ipympl` (`%matplotlib widget`)
backend itself so playback is interactive without needing a separate
`%matplotlib widget` cell first. Below the animation it draws Pause/Resume and
0.5x/1x/2x speed buttons (plain `matplotlib.widgets.Button`s, wired to the
underlying `FuncAnimation`'s `pause()`/`resume()` and `event_source.interval`)
so you can slow down, speed up, or freeze playback to inspect a moment.

Unlike the plot helpers, `show_animation` displays directly and returns
`None` — it's meant to be the last statement in a cell, not assigned to a
variable.

## show_animations

`show_animations` shows several trials' animations side by side,
**synchronized**: every panel is driven by the same shared frame index, so
they always show the same step at the same instant and share one set of
Pause/Resume and speed controls.

```python
from src.experiment import run_trial, show_animations

fixed_result = run_trial(
    policy_factory=lambda g: FixedRoutePolicy(g),
    graph=graph, seed=0, record_animation=True,
)
nearest_result = run_trial(
    policy_factory=lambda g: NearestUnservedPolicy(g),
    graph=graph, seed=0, record_animation=True,
)

show_animations({
    "Fixed route": fixed_result,
    "Nearest unserved": nearest_result,
})
```

It takes a `dict[str, TrialResult]` — the same shape `compare_policies`
groups results by, so a natural use is picking one representative trial per
policy label out of a comparison run. Each entry becomes one titled panel.
Since different policies can produce different numbers of recorded stop
arrivals, playback runs for as many frames as the *longest* trial; a panel
whose trial is shorter simply freezes on its last frame rather than cutting
the other panels off early.

## Relationship to other components

```
PlanarStreetGraph  ──────────────────────────────────────────┐
                                                             │ (shared)
                  policy_factory ──► run_trial ──► TrialResult
                                         ▲
                              compare_policies
                                         │
                              dict[label, list[TrialResult]]
                                         │
                        ┌────────────────┼──────────────────┐
                        ▼                ▼                   ▼
                plot_kpi_bars  plot_wait_distribution  plot_occupancy

TrialResult.animation_frames ──► animate_trial ──────► FuncAnimation
                              │                             ▲
                              ├──► show_animation ──────────┘ (displayed live)
                              │
                              └──► show_animations (dict[label, TrialResult])
```

`run_trial` depends on the full simulation stack (`BusAgent`, `MockBackend`,
`SimulationRunner`, etc.) but hides that wiring from the caller.
`compare_policies` depends only on `run_trial`. `summarize_comparison` wraps
`compare_policies`, returning its results alongside a formatted Markdown table.
The plot helpers depend only on `TrialResult` and `matplotlib`. `animate_trial`, `show_animation`, and
`show_animations` all depend on `TrialResult.animation_frames` and
`TrialResult.transit_graph`, so they're only usable after a
`run_trial(..., record_animation=True)` call. `show_animation` and
`show_animations` build their own `FuncAnimation` internally (via the same
per-panel logic `animate_trial` uses) rather than calling `animate_trial`
directly, since they need direct figure access to attach playback controls.
