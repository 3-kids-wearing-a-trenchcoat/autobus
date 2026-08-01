# Travel Model

## Overview

`TravelModel` is how a policy estimates two things: how long it takes to drive
between two nodes, and how long the bus is held at a stop.

```python
travel_time(from_id: str, to_id: str) -> float
dwell_time(stop_id: str, n_boarding: int, n_alighting: int) -> float
```

Both return seconds.

## Why this exists separately from the backend

The simulation already knows how long driving takes —
`SimulationBackend._travel_time` is the ground truth, supplied per backend. So
why add a second source for the same quantity?

Because they are not the same quantity. One is **what happens**; the other is
**what the policy believes will happen**.

Today they coincide: `MockBackend` computes travel via
`TransitGraph.best_path_cost`, and so does `GraphTravelModel`. The agreement is
exact, which is precisely why a projected schedule in the mock simulation is
not an approximation — it is what the bus will do, to the second.

Under CARLA that stops being true. Real driving times vary with traffic, so a
policy's projection becomes an estimate that can be wrong. Without the split
that divergence would be invisible: the policy would keep calling
`best_path_cost` and quietly plan against a world that no longer exists.

Keeping the two apart makes the divergence a first-class thing rather than a
silent bug, and yields a rule policies can be held to:

> A policy must never treat its own projections as fact. At every planning call
> it re-anchors on the real position, the real clock and the real passenger
> set, and recomputes forward.

## `GraphTravelModel`

The only concrete implementation. Travel time is the shortest-path cost through
the `TransitGraph`, whose edge weights are already in seconds. Dwell is a
single constant charged once per stop **visit**, independent of how many
passengers board or alight.

```python
model = GraphTravelModel(graph)                 # dwell-free (the default)
model = GraphTravelModel(graph, dwell_s=30.0)   # 30s at every stop
```

The passenger counts are part of `dwell_time`'s signature even though the
constant model ignores them, so a passenger-proportional dwell model can be
dropped in without new plumbing. `Schedule` already derives and passes the real
counts.

## Wiring

`run_trial` builds one model and hands it to both `BusAgent` and `MockBackend`,
so in the mock simulation estimate and ground truth are literally the same
object. A CARLA backend is what would eventually break that identity.

!!! warning "Keep the policy's dwell in step with the simulation's"
    A policy constructs its own `GraphTravelModel` by default, with **zero**
    dwell. If a trial runs with non-zero `dwell_s`, pass a matching model to
    the policy explicitly, or its projections will run ahead of the bus.

## Testing with deliberately wrong estimates

Because `TravelModel` is an ABC, tests can inject a model that lies — reporting
travel times that do not match what the backend will do. That is what makes the
"estimate diverged from reality" branch of a policy reachable in phase 1, where
the real models are exact and it would otherwise be dead, untested code.
