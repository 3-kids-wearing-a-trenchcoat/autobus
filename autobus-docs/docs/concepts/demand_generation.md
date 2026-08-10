# Demand Generation

## Overview

In a real deployment, passengers submit requests directly. During the
proof-of-concept phase there are no real passengers, so the `simulation`
package provides a synthetic source of demand: `DemandGenerator`.

There are two main concepts:

1. **`GenerationPolicy`** — decides *when* and *how many* requests to
   create on each simulation tick.
2. **`DemandGenerator`** — owns stop selection and adds completed
   `Request` objects to the `RequestPool`.

## GenerationPolicy

`GenerationPolicy` is an abstract base class with two methods:

```python
should_generate(sim_time: float) -> int
is_exhausted() -> bool
```

`should_generate` receives the current simulation time (in seconds) and
returns the number of new requests that should be created at this tick.
Returning `0` means "do nothing this tick."

`is_exhausted` says whether the policy will *ever* emit another request. It
exists for [`SimulationRunner.is_done`](simulation_runner.md#run-loop-and-lifecycle),
which needs to tell "the bus has caught up with its demand for a moment"
apart from "the run is over". Without it, an empty pool looks like the end of
a run, and a bus that finishes its only request before the next one arrives
ends the trial early — reporting perfect service over the handful of requests
that happened to exist.

A policy that generates forever answers `False` forever, which is correct:
such a run ends on `max_time` instead.

Implementations are responsible for tracking their own internal state
(e.g. the last time a request was generated, a running total).

### PeriodicPolicy

The only built-in policy. It emits exactly one request every
`interval_s` simulation-seconds until `max_requests` have been
generated in total.

- The first tick always fires (the interval counter starts at −∞).
- Once the cap is reached, all subsequent ticks return `0`.

## DemandGenerator

`DemandGenerator` wires a policy, a stop list, and a `RequestPool`
together. On each call to `update(sim_time)` it:

1. Asks the policy how many requests to generate.
2. For each request, selects a **pickup** stop and a **dropoff** stop
   (guaranteed to be different).
3. Creates a `Request` with a unique ID and adds it to the pool via
   `RequestPool.add()`.

### Stop selection modes

**Uniform random** (default) — pickup and dropoff are drawn uniformly
at random from the available stops.

**Biased random** — the caller supplies a `weights` dict mapping stop
ID → relative weight. Stops with higher weights are more likely to be
chosen for both pickup and dropoff. Stops absent from the dict receive
weight `0` and are never selected. Weights do not need to sum to any
specific value; `random.choices` normalises them internally.

In both modes the same weight distribution is used for pickup and
dropoff selection; the pickup stop is excluded from the dropoff
candidate pool to guarantee they differ.

### Request IDs

`DemandGenerator` assigns sequential IDs of the form `req_0`,
`req_1`, … The counter is per-instance and starts at `0`.

### Relationship to other components

`DemandGenerator` sits entirely on the input side of the system.
It writes to `RequestPool` and has no knowledge of the routing
algorithm. In a non-simulation context this component would be
replaced by a real passenger-request intake mechanism.
