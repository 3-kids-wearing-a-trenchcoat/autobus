# Requests

## Overview

The `algorithm` package contains the logic that decides what the bus does.
The first building block of that logic is passenger request management:
capturing what passengers want, tracking their progress through the journey,
and making that information available to the rest of the system.

There are three main concepts:

1. **`Request`** — a single passenger journey from a pickup stop to a dropoff
   stop.
2. **`RequestState`** — the lifecycle stage of a request.
3. **`RequestPool`** — the central store that holds all requests and is queried
   by the routing algorithm at planning time.

## Request

A `Request` represents a single passenger wanting to travel from one bus stop
to another. It records:

- **`pickup_stop_id`** — the stop where the passenger is waiting. Matches a
  `StopRecord` ID from the map.
- **`dropoff_stop_id`** — the stop where the passenger wants to get off.
- **`state`** — the current lifecycle stage (see [Request state](#request-state)
  below).
- **`created_at`** — simulation time in seconds when the request was submitted.
  Supplied by the caller (typically `DemandGenerator`); no default.
  Used by `MetricsTracker` to compute wait times.
- **`boarded_at`** — simulation time when the passenger boarded the bus.
  `None` until the request transitions to `ON_BUS`. Set by `BusAgent`.
- **`fulfilled_at`** — simulation time when the passenger alighted.
  `None` until the request transitions to `FULFILLED`. Set by `BusAgent`.

Each request has a unique string `id` assigned by whatever component creates
it (typically `DemandGenerator`).

## Request state

A request moves through the following lifecycle:

```
PENDING ──► ON_BUS ──► FULFILLED
   │
   └──────────────────► CANCELLED
```

| State | Meaning |
|-------|---------|
| `PENDING` | The passenger is waiting at the pickup stop. Both pickup and dropoff still need to be planned. |
| `ON_BUS` | The passenger has boarded. Only the dropoff needs to be planned. |
| `FULFILLED` | The passenger has alighted at the destination. The request is complete. |
| `CANCELLED` | The passenger gave up waiting. The request will not be served. |

`FULFILLED` and `CANCELLED` requests are **retained** in `RequestPool` (they
are no longer returned by active queries) so that `MetricsTracker` can access
historical data later.

## RequestPool

`RequestPool` is the central registry of all passenger requests. It is
written to by `DemandGenerator` (which adds new requests as passengers arrive)
and read by `DispatchPolicy` and `RoutePlanner` (which query active requests to
decide where the bus should go next).

### Active vs. historical requests

`RequestPool` distinguishes between *active* requests (`PENDING` or `ON_BUS`)
and *inactive* ones (`FULFILLED` or `CANCELLED`):

- **Active queries** — `get_all()`, `get_pending()`, and `get_on_bus()` return
  only active requests.
- **Historical queries** — `get_fulfilled()` and `get_cancelled()` return all
  completed requests of each kind. These are the primary interface for
  `MetricsTracker` to compute aggregate KPIs.
- **Individual lookup** — `get(id)` retrieves any request by ID regardless of
  state.

### Why separate `get_pending` and `get_on_bus`?

The routing algorithm treats the two active states differently:

- A `PENDING` request requires the bus to plan both a **pickup** and a
  **dropoff**.
- An `ON_BUS` request only needs a **dropoff** — the pickup is already done.

Having separate query methods avoids filtering at every call site and makes the
intent at the call site explicit.
