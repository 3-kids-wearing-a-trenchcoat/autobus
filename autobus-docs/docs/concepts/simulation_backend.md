# Simulation Backend

## Overview

`SimulationBackend` is the abstract controller that sits between
`SimulationRunner` and the individual simulation components. It owns the
per-tick sequencing and the bus movement model.

The class is abstract so that `SimulationRunner` can be written against a
single interface and work equally with a planar-graph backend, a test mock, or
eventually the CARLA backend — without knowing or caring which one it has.

## Per-tick sequence

Every call to `step(dt)` advances simulation time by `dt` seconds and executes
the following sequence in order:

```
step(dt)
    │
    ├─ sim_time += dt
    ├─ demand_generator.update(sim_time)   # new passengers become available
    ├─ _process_movement(dt)               # fire on_arrival for each stop reached
    └─ metrics_tracker.record_tick(sim_time)
```

Demand is generated before movement so that passengers created in this tick can
board at stops the bus visits within the same tick.
`record_tick` is called last so that the snapshot reflects the final pool state
after all boardings and alightings.

## Movement model

`SimulationBackend` tracks how far the bus has travelled toward the next stop
using two fields:

- `_time_to_next_stop` — the total travel time for the current leg, computed
  once by calling `_travel_time(current_position, next_stop)`.
- `_elapsed_toward_next` — seconds accumulated across ticks toward that leg.

When `_elapsed_toward_next` reaches `_time_to_next_stop`, the bus has arrived.
`on_arrival` is fired with the **exact** arrival timestamp rather than the tick
boundary, keeping KPI timestamps accurate when `dt` overshoots a stop.

### Multiple arrivals in one step

If `dt` is large enough to span more than one stop, `_process_movement` loops
until the remaining time budget is exhausted or the route runs out:

```
remaining = dt
while remaining > 0:
    if remaining >= time_left_to_next:
        fire on_arrival at exact arrival_time
        recompute time_to_next_stop for new next stop
    else:
        accumulate partial progress, done
```

## The abstract method: `_travel_time`

```python
_travel_time(from_id: str, to_id: str) -> float
```

This is the only method subclasses must implement. It returns the travel time in
seconds between two TransitGraph node IDs and encapsulates all
environment-specific movement physics:

- **Planar backend** — calls `TransitGraph.best_path_cost(from_id, to_id)`,
  which computes travel time from road lengths and speed limits.
- **CARLA backend** — would query the simulator to obtain the actual travel time
  for the vehicle on that road segment.

Everything else — tick sequencing, movement accounting, termination — is
implemented once in the base class.

## Termination

`is_done()` returns `True` when the `RequestPool` contains no `PENDING` and no
`ON_BUS` requests. If the demand generator will eventually create more requests
they will appear in the pool; once it is exhausted and the bus has dropped off
all passengers, `is_done()` becomes `True`.

## Relationship to other components

```
SimulationRunner
      │
      │ step(dt)
      ▼
SimulationBackend (abstract)
      │
      ├─ demand_generator.update()    ──►  DemandGenerator  ──►  RequestPool
      ├─ agent.on_arrival()           ──►  BusAgent         ──►  RequestPool
      └─ metrics_tracker.record_tick() ──►  MetricsTracker  ◄──  RequestPool
```

`SimulationBackend` is the only component that knows about simulation time
advancement. `SimulationRunner` only needs to call `step(dt)` in a loop and
check `is_done()` to decide when to stop.
