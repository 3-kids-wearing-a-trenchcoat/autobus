# Simulation Backend

## Overview

`SimulationBackend` is the seam between the project and the world the bus
drives in. It answers questions and takes commands; it does not orchestrate.

That distinction is the whole point of the class. Everything that is *not*
world-specific — the clock, the tick order, demand, abandonment, metrics,
animation — belongs to [`SimulationRunner`](simulation_runner.md). What is
left below the seam is small enough that a new world is a short class:
`MockBackend` is dead reckoning over a graph, `ScriptedBackend` is a
timetable, and a CARLA backend would be a vehicle and a proximity check.

!!! note "This is a repair, not the original design"
    Until Phase 2 the backend owned the tick order and called *into*
    `BusAgent`, and its single abstract method was
    `_travel_time(from, to) -> float`. That signature encodes "a leg's
    duration is known before departure", which CARLA cannot answer — so no
    CARLA backend was implementable against it. The interface below replaces
    it.

## The interface

```python
set_destination(stop_id: str) -> None       # abstract
advance(dt: float) -> AdvanceResult         # abstract
current_location() -> str                   # abstract

start() -> None                             # no-op by default
close() -> None                             # no-op by default
sync_passengers(pool: RequestPool) -> None  # no-op by default
```

Only three methods are abstract, because only three questions genuinely need
the world to answer them. The rest have inert defaults so a world with no
setup and nothing to render does not have to say so.

### `advance` returns one arrival at most

```python
@dataclass(frozen=True)
class AdvanceResult:
    arrived_at: str | None   # stop reached, or None if still en route
    consumed: float          # seconds actually spent
```

**`advance(dt)` means: advance until `dt` is exhausted or one stop is
reached, whichever comes first.**

The single-arrival rule is the load-bearing part of the contract. Arrivals
within one tick are *sequentially dependent*: the agent boards, alights and
re-plans on each arrival, and the leg that follows is not known until it has.
A batch API — `poll_arrivals() -> list[...]` — would look tidier and would be
wrong, because the backend would be answering for a route the agent has not
chosen yet.

When no arrival occurs the whole budget must be consumed, so the runner's
clock and the world's stay in step. For a tick-based world such as CARLA that
means `dt` should be a whole multiple of the simulator's tick length.

There is deliberately no `now()`. The runner owns simulation time and derives
arrival timestamps from `consumed`; a second clock in the backend would only
be something to drift.

### `set_destination` is idempotent

The runner re-issues the destination on every pass of its movement loop
rather than tracking what it last said. Re-commanding the stop the bus is
already heading for must therefore leave the leg untouched — otherwise
partial progress would be discarded every tick and the bus would never
arrive.

The exception that matters: a destination equal to the bus's current position
is legal, because a policy may re-issue the stop the bus is standing on, and
it must start a fresh leg rather than being ignored as unchanged. `MockBackend`
handles both by clearing its cached leg duration on arrival as well as on a
change of destination.

### `current_location`

Between stops this reports the last stop reached, so it is exact only at
arrivals. That is enough today, because the bus is stationary whenever it
plans. A world that supported re-planning mid-leg would report a finer
position here — the method exists so that change would not need a new one.

## Implementations

### `MockBackend`

A world made of shortest paths. Travel times come from the `TransitGraph`
built off a `MapProvider`, so a leg's duration is known the moment it starts
and the bus covers it at a constant rate. No vehicle, no geometry, nothing to
spawn — which is what makes it the fast test bed.

`dwell_s` models the seconds a real bus spends pulled over while passengers
board and alight. It is folded into the **leading** edge of each leg: the bus
first spends `dwell_s` where it currently is, then drives. That is equivalent
to holding it at the stop it just reached, and keeps the whole calculation to
a single duration per leg. One consequence is that the bus also dwells at its
initial position, before it has served anyone — harmless, and arguably right,
since a real bus would be sitting at its first stop with the doors open.

`dwell_s` defaults to `0.0`, which reproduces the original dwell-free timing
exactly, so results recorded before dwell existed remain comparable.

This is *ground truth*: it is what the simulation does. What a policy
**believes** about travel and dwell times is a separate thing — see
[Travel Model](travel_model.md).

Leg durations are computed lazily, on the first `advance` toward a
destination rather than when it is set. That keeps construction free of
movement arithmetic, which matters for CARLA: there is no vehicle to measure
from until `start()` has run.

### `ScriptedBackend`

A world with no graph, no travel model and no geometry. Every leg takes
however long its timetable says, regardless of where the bus is or where it
was told to go, and the bus then "arrives" at whatever stop it was commanded
to. The timetable cycles, so a one-entry list means a constant leg time and a
trial never runs out of schedule.

Its purpose is to make the modularity claim testable rather than merely
asserted: a full `run_trial` runs against it and produces ordinary KPIs, with
no map anywhere below the runner. It doubles as a stand-in for CARLA while
the server is unavailable.

## Termination is not the backend's business

`is_done()` lives on the runner and reads the `RequestPool`: no `PENDING` and
no `ON_BUS` requests means the run is over. The world has no opinion about
when a simulation should stop.

## Relationship to other components

```
SimulationRunner
      │
      │  set_destination(stop) ─┐
      │  advance(dt) ───────────┤
      │  sync_passengers(pool) ─┤
      │  start() / close() ─────┘
      ▼
SimulationBackend (abstract)
      ├── MockBackend       — shortest paths over a TransitGraph
      ├── ScriptedBackend   — a timetable, no map at all
      └── CarlaBackend      — a vehicle, walkers, and world.tick()
```

Nothing above the backend knows which of these it holds.
