# Simulation Runner

## Overview

`SimulationRunner` orchestrates a simulation. It owns the clock and the tick
order, and it mediates between the bus agent and the world: it reads the
agent's next stop, commands the backend to drive there, observes the arrival,
and hands it back to the agent.

Nothing in it is world-specific, which is exactly why it lives above
[`SimulationBackend`](simulation_backend.md) rather than inside it.

## Per-tick sequence

Every call to `step(dt)` advances simulation time by `dt` seconds and runs
this sequence:

```
step(dt)
    │
    ├─ sim_time += dt
    ├─ demand_generator.update(sim_time)   # new passengers become available
    ├─ _expire_pending()                   # impatient passengers give up
    ├─ backend.sync_passengers(pool)       # mirror the pool onto the world
    ├─ _process_movement(dt)               # for each stop reached:
    │       ├─ agent.on_arrival()          #   board/alight, then re-plan
    │       └─ recorder.record_arrival()   #   (if attached) capture a frame
    └─ metrics_tracker.record_tick(sim_time)
```

Demand is generated before movement so that passengers created in this tick
can board at stops the bus visits within it. Expiry runs before movement for
the same reason in reverse: a passenger who has just run out of patience must
not be picked up by a bus that reaches their stop later in the same tick.
`record_tick` is last so the snapshot reflects the final pool state.

!!! warning "The tick order has a known wart"
    Because demand is generated before movement, a request created in this
    tick can be boarded at an `arrival_time` computed to fall slightly
    *earlier* than it was created — so a small number of wait times come out
    negative. Measured on the campus map this affects 0.05% of requests, with
    the worst at −0.49 s, and it is bounded by `dt`.

    It was left alone through the Phase 2 refactor deliberately: fixing it
    there would have shown up as a golden-master failure and made it harder
    to tell a genuine regression from an intended change.

## The movement pass

```python
remaining = dt
while remaining > 0.0:
    next_stop = agent.next_stop()
    if next_stop is None:
        break
    backend.set_destination(next_stop)
    result = backend.advance(remaining)
    remaining -= result.consumed
    if result.arrived_at is None:
        break
    arrival_time = sim_time - remaining
    agent.on_arrival(result.arrived_at, arrival_time)
```

Two things are worth reading carefully.

**The loop re-asks the agent after every arrival.** That is why `advance` may
report only one arrival at a time — see
[the contract](simulation_backend.md#advance-returns-one-arrival-at-most).

**The arrival timestamp comes from the leftover budget, not the tick
boundary.** A stop reached mid-tick is stamped at the moment it was actually
reached, which keeps KPI timestamps accurate when `dt` overshoots a stop.

## Why the runner mediates

The design review draws the arrow the other way: `BusAgent → SimulationBackend`,
with the agent commanding the world. Implemented literally, `BusAgent` would
hold a backend reference and `src/algorithm` would depend on `src/simulation`,
breaking the requirement that the routing algorithm stay fully independent of
the CARLA API.

Routing the command through the runner gives the same decoupling with better
layering, and `BusAgent` needs no changes at all: its entire public surface is
`next_stop()`, `on_arrival(stop_id, sim_time)` and `position`.

## Initialisation

The caller constructs the components and hands them to the runner, along with
whichever world it should drive:

```python
pool = RequestPool()
agent = BusAgent(pool, policy, travel_model, capacity=30, position=start, ...)
demand_gen = DemandGenerator(pool, PeriodicPolicy(...), stops)
metrics = MetricsTracker(pool)
backend = MockBackend(position=start, provider=provider)

runner = SimulationRunner(
    backend=backend,
    agent=agent,
    pool=pool,
    demand_generator=demand_gen,
    metrics_tracker=metrics,
    dt=1.0,
    max_time=3600.0,
)
kpi = runner.run()
```

Swapping `MockBackend` for a `ScriptedBackend` or a CARLA backend changes
nothing else on this page.

## Run loop and lifecycle

```
run()
  │
  ├─ backend.start()
  ├─ while not is_done() and sim_time < max_time:
  │       step(dt)
  ├─ backend.close()          # in a finally block
  │
  └─ snapshot KPIs → print summary → return KPISnapshot
```

`is_done()` reads the `RequestPool`: no `PENDING` and no `ON_BUS` requests
means the run is over. `max_time` is a safety ceiling, so a demand generator
that never stops still terminates the loop.

`close()` runs from a `finally` block, so it fires even when a tick raises.
That matters for CARLA, where leaked actors are the classic cross-trial
contaminant.

## Output

After the loop, `run()` prints a summary to stdout and returns the
`KPISnapshot` for programmatic use:

```
=== Simulation Complete ===
Sim time:         3600.00 s
Fulfilled:        142
Cancelled:        3
Mean wait time:   45.30 s
P90 wait time:    65.80 s
Max wait time:    92.40 s
Mean ride time:   120.80 s
Mean journey:     166.10 s
Fulfillment rate: 97.9 %
Throughput:       2.37 req/m
```

`run_trial` suppresses this with `silent=True` for bulk comparison runs.

## Relationship to other components

```
run_trial / main.py
  │  constructs components, picks a backend
  ▼
SimulationRunner ──► DemandGenerator ──► RequestPool
  │  ├──────────────► MetricsTracker  ◄── RequestPool
  │  ├──────────────► AnimationRecorder ◄ RequestPool
  │  │
  │  ├─ next_stop() / on_arrival() ──►  BusAgent  ──►  DispatchPolicy
  │  │
  │  └─ set_destination() / advance() ──►  SimulationBackend
  ▼
```
