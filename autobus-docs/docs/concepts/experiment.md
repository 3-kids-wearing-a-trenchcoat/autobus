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
| `tick_count` | `int` | Number of per-tick snapshots recorded. Always populated, even when `tick_history` is dropped. |
| `tick_history` | `list[TickSnapshot]` | Per-tick occupancy and queue snapshots. Empty unless `keep_tick_history=True` was passed to `run_trial`. |
| `wait_times` | `list[float]` | Wait time (seconds) for each fulfilled request. |
| `ride_times` | `list[float]` | Ride time (seconds) for each fulfilled request. |
| `animation_frames` | `list[AnimationFrame]` | Per-stop-arrival snapshots for animation. Empty unless `record_animation=True` was passed to `run_trial`. |
| `transit_graph` | `TransitGraph \| None` | The graph used during the run; `None` unless `record_animation=True`. |
| `backend_stats` | `dict[str, float]` | Diagnostic counters reported by the backend; empty when it publishes none. |
| `policy_stats` | `dict[str, float]` | Diagnostic counters reported by the policy; empty when it publishes none. |

`wait_times` and `ride_times` expose the per-request distributions that
`KPISnapshot` only summarises, making it possible to do full distributional
comparisons across policies.

### Diagnostic counters

`backend_stats` and `policy_stats` answer a different question from the KPIs:
not how well the service performed, but whether the run was *healthy*. A CARLA
trial that served few passengers means one thing on its own and quite another
alongside forty re-routes, and a campaign running unattended overnight has no
other way to tell those apart afterwards.

Both are discovered by duck typing. If the backend or the policy has a callable
`stats()`, `run_trial` copies what it returns; if not, the field is an empty
dict rather than an error. Neither `SimulationBackend` nor `DispatchPolicy`
declares the method, because most implementations have nothing to report —
today `CarlaBackend` (distance driven, arrivals, re-routes, walker spawn
failures, red-light ticks) and `ReorderingInsertionPolicy` (relocation attempts
and acceptances) are the only two that do.

The counters are read **after** the run has finished, and therefore after
`SimulationRunner` has closed the backend from its `finally` block. Closing must
not reset them: a backend that cleared its counters on `close` would hand a
campaign a file full of zeros with nothing raised. `CarlaBackend.close` releases
its actors and walker maps but deliberately leaves `_stats` alone, and
`tests/test_trial.py` pins the ordering.

They also replace a hack worth naming. Before the field existed, the only way to
reach these numbers was to keep the component in a module-level dict or list and
read it after the run — which works in one process and silently reports zeros in
several, since each worker mutates its own copy. Counters that travel home on
the result survive being run anywhere.

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

### Simulating unreliable driving

`travel_noise` perturbs the travel times the bus **actually experiences**,
while the policy keeps planning against the unperturbed graph:

```python
result = run_trial(..., travel_noise=0.2, noise_seed=1)
```

It is the sigma of a lognormal factor with median 1.0, so it reads roughly as
a proportional spread. `0.0` — the default — returns the planning model itself
to the backend, so estimate and ground truth remain the same object and every
existing result is reproduced exactly.

`noise_seed` seeds a generator private to the travel model, which never touches
the global one `run_trial` seeds for demand. It defaults to `seed`; setting it
independently resamples execution luck while holding the request stream fixed.

See [Travel Model](travel_model.md) for why the distribution is lognormal and
what failure mode this opens up.

### Running somewhere other than the graph

`backend_factory` mirrors `policy_factory`: it takes the `TransitGraph` and
the bus's starting stop ID, and returns the world the bus drives in.

```python
from src.simulation.ScriptedBackend import ScriptedBackend

result = run_trial(
    policy_factory=lambda g: FixedRoutePolicy(g),
    graph=graph,
    backend_factory=lambda g, start: ScriptedBackend(
        start, leg_durations=(90.0,)
    ),
)
```

It defaults to `None`, which builds a `MockBackend` driving against this
trial's own travel model — closed over the model rather than rebuilt from the
graph, so at `travel_noise=0.0` the backend and the policy share one object
and every existing result reproduces exactly.

`travel_noise`, `noise_seed` and `dwell_s` describe that default world only.
A caller who brings their own backend owns its physics.

The example above is not a toy: `ScriptedBackend` has no graph, no travel
model and no geometry, and a full trial still completes against it. That is
the test the seam has to pass, and it is what made `CarlaBackend` a matter of
writing one class rather than of rewiring the harness.

### Running on a map other than a planar graph

`provider` takes any [`MapProvider`](map.md) and is used instead of `graph`,
which then need not be given:

```python
from src.map.CarlaMapProvider import CarlaMapProvider

result = run_trial(
    policy_factory=lambda g: WindowInsertionPolicy(g, max_wait_s=900.0),
    provider=CarlaMapProvider("Town01"),
)
```

`provider` and `backend_factory` are independent, which is the useful part.
A CARLA town with the default mock backend is a real road network with no
server running and no vehicle — the town's geometry at full simulation speed.
Supplying both is what drives an actual bus.

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

### Comparing on another map, or in another world

`provider` and `backend_factory` mean here what they mean on `run_trial`, and
are shared by every trial. `graph` may be omitted entirely when a provider is
given:

```python
from src.map.CarlaMapProvider import CarlaMapProvider

results = compare_policies(
    policies=[...],
    provider=CarlaMapProvider("Town01"),
    n_trials=10,
    base_seed=0,
)
```

Both were reachable through keyword forwarding before they were named
parameters, so this is a documented and tested capability rather than a new
one — but nothing pinned it, and `graph` used to be a required positional, so
running on a provider meant passing `None` for a map you were not using.

**The same backend factory serves every trial.** That is right for a
deterministic world and wrong for one with luck of its own to resample: a
CARLA campaign wants each trial's backend seeded differently, so that traffic
and spawn placement vary across the sample rather than being held fixed. Such
a campaign builds a factory per trial instead of sharing one here.

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
`n_trials`, `base_seed`, `progress`, `provider`, `backend_factory`, and any
forwarded `run_trial` keywords), plus a `confidence` level (default `0.95`)
for the margin of error.

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

## The results store

`compare_policies` holds its runs in memory: close the notebook and they are
gone. That is fine for a synthetic-map sweep that re-runs in a minute, and
useless for CARLA, where a campaign is hours of wall time. The store is where
completed runs live.

### A run is one (parameters, seed) pair

This is the idea everything else follows from. Repeating a seed against the
same parameters is not a new observation — it is the *same* observation — so
re-running one is **skipped or overwritten, never appended**. Nothing else
prevents a single lucky run being counted twice into the sample it belongs to,
which would bias exactly the mean and confidence interval it was run to
establish.

Seeds are therefore how repeated measurements are distinguished from repeated
work. Twenty seeds against one configuration is a sample of twenty; the same
seed run twice is one measurement done twice.

### One write-once file per run

```
results/runs/3f9a1c04e8b27d51.json
```

The name is `sha256` of the run's identity, truncated to 16 hex characters, and
the identity is everything that determines the outcome:

| Part | Where it comes from |
|------|---------------------|
| `policy` | The label it runs under |
| `policy_class` | `type(policy).__name__` |
| `policy_params` | [`policy.params()`](dispatch_policy.md#declaring-parameters) |
| `world` | Supplied by the caller — town, backend, traffic |
| `trial` | `run_trial`'s parameters, resolved against its defaults |
| `seed` | The demand seed |

Files are written once and never touched again, which is a deliberate choice
rather than an accident of simplicity. This directory is synchronised live, and
a sync conflict on a write-once file costs a single run and leaves a visible
`.sync-conflict-*` beside it; one shared file — a growing log, or a database —
would put every run behind a single writer. It is also the only shape that
stays correct if trials are ever run in parallel, since separate processes
write separate files and need no locking. Writes go through a temporary file
and `os.replace`, so a run is either absent or complete, never half-parsed.

If querying ever outgrows a directory scan, a SQLite index *derived* from these
files is a pure addition that changes nothing about how they are stored.

!!! danger "Changing simulation behaviour makes stored runs stale"

    The identity is what a run was *asked* to do, not the code that did it.
    That is deliberate — putting the commit in the identity would invalidate
    every stored run on every commit, including documentation-only ones — but
    it means a change to the backend, a policy, or the runner leaves records
    that look current and are not.

    **Purge or re-run the affected runs when you change simulation
    behaviour.** `run_campaign(..., overwrite=True)` re-runs and replaces
    them. Each record's `provenance.git_commit` says which commit produced
    it, so what is stale is at least identifiable after the fact.

    This has already bitten once: fixing the stall detector's
    queued-behind-traffic false positive changed the `repaths` counter, and
    four stored runs kept their pre-fix values until they were overwritten.

### Two details that would otherwise bite

**Numbers are normalised before hashing.** `dt=1` and `dt=1.0` are one
configuration; hashing them apart would split a sweep into two half-populated
copies that never dedupe against each other. Booleans are left alone, since
`bool` is a subclass of `int` and folding `True` into `1.0` would surprise.

**Trial parameters are resolved against `run_trial`'s defaults.** Naming
`capacity=16` and leaving it to the default are the same simulation. Recording
only what the caller passed would file them as two runs. Arguments that cannot
move a KPI — `silent`, `record_animation`, `keep_tick_history` — are excluded.

### Reading it back

```python
from src.experiment import load_frame, load_runs, to_results, summarize_results

frame = load_frame(DEFAULT_RUNS_DIR)         # one row per run, for plotting
records = load_runs(DEFAULT_RUNS_DIR)        # the records themselves
results = to_results(records)                # what every plot helper takes
summarize_results(results).markdown          # the comparison table
```

`load_frame` flattens nested keys into dotted columns —
`identity.trial.max_requests`, `kpi.mean_wait_time` — which is the shape both
grouping and plotting want.

`to_results` rebuilds `dict[str, list[TrialResult]]`, so stored runs feed
`plot_kpi_bars`, `plot_wait_distribution`, `plot_occupancy` and the summary
table with no changes to any of them.

!!! warning "`to_results(paired=True)` is the default, and it can drop runs"

    Every paired statistic here indexes two policies' lists together and
    assumes position *i* is the same seed in both. A campaign interrupted
    part-way leaves policies with different seed sets, and aligning those by
    position compares unrelated runs — **wrong p-values, and no error**.

    So only the seeds every policy has are kept. That can lose runs, but a
    smaller sample is visible in the table's counts and being wrong is not.
    Pass `paired=False` for a parameter sweep, which is not a paired
    comparison and should lose nothing.

## run_campaign

Runs the trials that are not already recorded, and files each one as it
finishes.

```python
from src.experiment import run_campaign

result = run_campaign(
    policies=[("Fixed route", lambda g: FixedRoutePolicy(g)),
              ("Window",      lambda g: WindowInsertionPolicy(g, max_wait_s=750))],
    seeds=range(30),
    world={"town": "Town01", "backend": "CarlaBackend", "npc_vehicles": 0},
    provider=CarlaMapProvider("Town01"),
    backend_factory_for=lambda seed: make_carla_backend(seed),
    max_requests=40,
)
print(result.ran, result.skipped)
summarize_results(result.results()).markdown
```

**It takes `seeds`, not a trial count.** The store already knows which
`(parameters, seed)` pairs exist, so extending a campaign to seeds 30–59 later
skips 0–29 and runs only what is missing — and restarting a crashed campaign
costs only what it had not finished.

**`world` is required and never guessed.** It is the part of an identity that
cannot be read off the arguments, and a thin one would let two genuinely
different worlds share a run. For a synthetic map it should name the map's own
parameters; for CARLA, the town, the backend and the traffic.

**`backend_factory_for` takes the seed** and returns that trial's backend
factory. This is why `run_campaign` does not call `compare_policies`: one
factory shared by every trial is right for a deterministic world and wrong for
CARLA, where traffic and spawn placement are luck of their own and holding them
fixed would under-sample the variance the campaign exists to measure.

A trial that raises stops the campaign rather than being swallowed. Everything
completed is already in the store, so restarting is cheap — which is precisely
what removes the argument for continuing quietly through errors.

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

### plot_parameter_sweep

The one helper that reads the store's table rather than `TrialResult`s,
because it answers a question the others cannot: how a KPI moves as a
*parameter* moves.

```python
from src.experiment import load_frame, plot_parameter_sweep

frame = load_frame(DEFAULT_RUNS_DIR)
fixed = frame[frame["identity.trial.max_requests"] == 40]

fig = plot_parameter_sweep(
    fixed,
    x="identity.policy_params.max_wait_s",
    y="kpi.mean_wait_time",
)
```

The band is a confidence interval computed across the repeated seeds at each
x — which is what those seeds were run for.

!!! warning "Filter to the parameters being held fixed first"

    Seaborn will happily average across a parameter nobody meant to vary, and
    the resulting line looks perfectly reasonable. The `fixed = frame[...]`
    step above is not decoration.

An unknown column raises `KeyError` rather than drawing an empty axes, since
a mistyped dotted path would otherwise read as a parameter that had no effect.

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

`run_trial` depends on the full simulation stack (`BusAgent`,
`SimulationRunner`, a `SimulationBackend`, etc.) but hides that wiring from
the caller.
`compare_policies` depends only on `run_trial`. `summarize_comparison` wraps
`compare_policies`, returning its results alongside a formatted Markdown table.
The plot helpers depend only on `TrialResult` and `matplotlib`. `animate_trial`, `show_animation`, and
`show_animations` all depend on `TrialResult.animation_frames` and
`TrialResult.transit_graph`, so they're only usable after a
`run_trial(..., record_animation=True)` call. `show_animation` and
`show_animations` build their own `FuncAnimation` internally (via the same
per-panel logic `animate_trial` uses) rather than calling `animate_trial`
directly, since they need direct figure access to attach playback controls.
