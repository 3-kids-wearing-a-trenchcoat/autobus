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
        if not pool.get_pending():
            break
        agent.replan_if_idle(sim_time - remaining)   # wake a parked bus
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

Three things are worth reading carefully.

**The loop re-asks the agent after every arrival.** That is why `advance` may
report only one arrival at a time — see
[the contract](simulation_backend.md#advance-returns-one-arrival-at-most).

**An empty route does not mean an idle world.** `BusAgent` re-plans on
arrival, so a bus whose route runs out never arrives anywhere again, never
re-plans, and keeps an empty route — sleeping through every request that
follows. Measured with a 200 s demand interval against 10 s legs: the bus
served the first request and then sat still for the remaining 20,000 s with
four passengers waiting. So the loop gives a parked bus a chance to notice
work that arrived while it sat, gated on there being pending requests so an
genuinely idle bus does not re-run the policy every tick. See
[`replan_if_idle`](bus_agent.md#waking-a-parked-bus).

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

`is_done()` requires two things: an empty `RequestPool` — no `PENDING`, no
`ON_BUS` — **and** a demand generator with nothing left to emit. `max_time` is
a safety ceiling, so a generator that never stops still terminates the loop.

!!! danger "Both halves are necessary, and the second was learned the hard way"
    An empty pool on its own is the ordinary state of a bus between requests,
    not the end of the run. Treating it as the end stops a trial the moment it
    catches up with its own demand.

    Measured on a CARLA pilot before `is_exhausted()` was required: at a 150 s
    demand interval, **2 of 8 trials ended having served 1 of 12 requests**,
    because the bus completed that first trip in under 150 s and the second
    request did not exist yet.

    Both reported a **served fraction of 1.0**. That metric is
    `fulfilled / (fulfilled + cancelled + pending + on_bus)`, and the eleven
    requests never created cannot count against it — so a trial that did not
    happen scored perfect service on one of the project's success criteria.

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
run_trial  (called by main.py and the notebooks)
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
