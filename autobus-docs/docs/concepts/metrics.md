# Metrics

## Overview

`MetricsTracker` is a passive observer that collects simulation data and
computes aggregate KPIs. It never writes to any shared state — it only reads
from `RequestPool`.

Two kinds of data are collected:

- **Per-tick snapshots** — a `TickSnapshot` recorded once per simulation tick,
  capturing queue depth and occupancy at that moment.
- **On-demand KPI summaries** — a `KPISnapshot` computed from completed
  requests whenever `snapshot()` is called.

## Tick snapshots

A `TickSnapshot` records the following at a single simulation tick:

| Field | Meaning |
|-------|---------|
| `sim_time` | Simulation time in seconds when the snapshot was taken. |
| `pending_count` | Number of requests with state `PENDING` (waiting at a stop). |
| `occupancy` | Number of requests with state `ON_BUS` (riding the bus). |

`record_tick(sim_time)` should be called **once per tick, after
`BusAgent.on_arrival()`** has completed, so that boarding and alighting are
already reflected in the pool.

The full sequence of snapshots is accessible via `tick_history` and can be used
to plot how queue depth and occupancy evolved over the course of a run.

## KPI summaries

Calling `snapshot()` at any point during or after a run returns a `KPISnapshot`
with the following fields:

| Field | Meaning |
|-------|---------|
| `fulfilled_count` | Total number of fulfilled requests so far. |
| `cancelled_count` | Total number of cancelled requests so far. |
| `mean_wait_time` | Average time from `created_at` to `boarded_at` (in seconds). |
| `mean_ride_time` | Average time from `boarded_at` to `fulfilled_at` (in seconds). |
| `mean_journey_time` | Average time from `created_at` to `fulfilled_at` (in seconds). |

### In-flight exclusion

**Only fulfilled requests contribute to the KPI means.** Requests that are
still `PENDING` or `ON_BUS` at the time of the call are excluded entirely —
they have incomplete journeys and including them would undercount the true
latency. `None` is returned for all means when no requests have been fulfilled
yet.

This means `snapshot()` is safe to call at any point mid-run. The KPIs reflect
the performance of completed journeys only, which is a stable and unbiased
view of how the system has performed so far.

## Expected call pattern

```python
metrics = MetricsTracker(pool)

# Inside the simulation loop:
bus_agent.on_arrival(stop_id, sim_time)
metrics.record_tick(sim_time)

# At any point — inspect KPIs to decide whether to continue:
kpi = metrics.snapshot()
if kpi.mean_wait_time is not None and kpi.mean_wait_time > threshold:
    ...  # terminate or adjust

# After the run — access time-series data:
history = metrics.tick_history
```

## Relationship to other components

```
RequestPool ──► MetricsTracker
                    │
                    ├─ record_tick()  →  TickSnapshot list
                    └─ snapshot()     →  KPISnapshot
```

`MetricsTracker` depends on `RequestPool` (read-only). Nothing in the system
depends on `MetricsTracker` — it sits at the end of the dependency chain.
