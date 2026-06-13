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

Calling `snapshot(sim_time)` at any point during or after a run returns a
`KPISnapshot` with the following fields:

| Field | Meaning | `None` when |
|-------|---------|-------------|
| `fulfilled_count` | Total fulfilled requests so far. | — |
| `cancelled_count` | Total cancelled requests so far. | — |
| `mean_wait_time` | Average time from `created_at` to `boarded_at` (seconds). | No fulfilled requests yet. |
| `mean_ride_time` | Average time from `boarded_at` to `fulfilled_at` (seconds). | No fulfilled requests yet. |
| `mean_journey_time` | Average time from `created_at` to `fulfilled_at` (seconds). | No fulfilled requests yet. |
| `max_wait_time` | Longest wait time across all fulfilled requests (seconds). | No fulfilled requests yet. |
| `p90_wait_time` | 90th-percentile wait time across fulfilled requests (seconds). | No fulfilled requests yet. |
| `fulfillment_rate` | `fulfilled / (fulfilled + cancelled)` — fraction of terminal requests served. | No terminal requests yet. |
| `throughput` | Fulfilled requests per simulation minute (`fulfilled / sim_time * 60`). | `sim_time` is 0. |

### In-flight exclusion

**Only fulfilled requests contribute to the timing means.** Requests still
`PENDING` or `ON_BUS` are excluded — they have incomplete journeys and
including them would undercount the true latency. `None` is returned for all
means when no requests have been fulfilled yet.

`fulfillment_rate` covers all *terminal* requests (fulfilled + cancelled), so it
is also unaffected by in-flight state.

`snapshot()` is safe to call at any point mid-run.

## Expected call pattern

```python
metrics = MetricsTracker(pool)

# Inside the simulation loop:
bus_agent.on_arrival(stop_id, sim_time)
metrics.record_tick(sim_time)

# At any point — inspect KPIs to decide whether to continue:
kpi = metrics.snapshot(sim_time)
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
