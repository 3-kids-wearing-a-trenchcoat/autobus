# Simulation Backend

## Overview

`SimulationBackend` is the seam between the project and the world the bus
drives in. It answers questions and takes commands; it does not orchestrate.

That distinction is the whole point of the class. Everything that is *not*
world-specific — the clock, the tick order, demand, abandonment, metrics,
animation — belongs to [`SimulationRunner`](simulation_runner.md). What is
left below the seam is small enough that a new world is a short class:
`MockBackend` is dead reckoning over a graph, `ScriptedBackend` is a
timetable, and `CarlaBackend` is a vehicle and a proximity check.

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

### `CarlaBackend`

A real vehicle in a real town. It is the proof that the seam was worth
repairing: one class against the three abstract methods, and **nothing above
it changed** — the same agent, policy, demand generator, abandonment and
metrics that run on the synthetic map.

It is the only file under `src/simulation` that imports `carla`, apart from
the routing helper it uses, and nothing above the backend imports either.

#### Getting to a stop

Steering is CARLA's own driving, as the design review intended: the bus is an
ordinary autopilot vehicle that is told where to go next. *How* it is told is
the subtle part.

`TrafficManager.set_path` reads like a destination API and is not one. It is a
**junction-decision** API: at each junction the traffic manager picks the exit
that best advances towards the next point it has been given. Handed a single
point half a town away, that choice is greedy, and on a grid it can cycle
forever. Measured on Town01, a bus told only where its stop was drove a
ten-road loop indefinitely, came within 78 m, and never once entered the road
the stop was on.

So `carla_routing` plans the leg instead: a breadth-first search over
`waypoint.next()` finds a lane-level route, and the junction manoeuvres are
read off it and handed to `set_route`, which takes exactly that.

That loop is not merely slow — it is closed. Given 600 seconds the same bus
drove 3 km and still came no closer than 78 m, exactly the distance it
reached in the first 150. With turns it arrives in 142 s. For legs that do
not cycle, routing is a speed-up of roughly 16%, which matters mainly
because Town01 legs already run to minutes.

Two things fall out of this that are easy to get wrong:

- **Only junctions offering a choice count.** `set_route` consumes the list in
  order, so one spurious entry throws every later turn onto the wrong
  junction.
- **CARLA ships an equivalent** — `agents.navigation.GlobalRoutePlanner` — but
  the pip wheel is client bindings only and does not include it, so it would
  have to be copied into this repository either way. Ours was measured against
  it and reproduces it exactly, in a fraction of the code and with no extra
  dependency.

#### Knowing it has arrived

Proximity, never waypoint equality: the traffic manager routes *through* a
location rather than stopping on it, and the bus is about twelve metres long
with its origin at its centre. `arrival_radius_m` defaults to 6 m.

**Be patient with a leg.** Measured on Town01, legs run **45 to 399 seconds**
— the town has 36 traffic lights cycling in groups of three, so a single
approach can hold the bus for about 28 s. Anything that bounds a leg has to
be sized against 400 s, not against the tens of seconds a mock leg takes. A
short bound does not protect a trial; it manufactures the failures it was
meant to catch.

This is a genuine approximation. Arrivals are also observed at tick
boundaries, so unlike `MockBackend` the backend cannot report a fractional
`consumed`, and arrival times are quantised.

#### Passengers

`sync_passengers` diffs the pool each tick and mirrors waiting requests onto
walker actors — one appears when a request starts waiting, and is destroyed
when it stops, whether it was picked up or gave up. The pool stays
authoritative and nothing feeds back, so a failed spawn costs a visual and
never a result.

Walkers stand on the **shoulder**, not at the stop coordinate. Stops are
lane-centre positions, and a pedestrian standing in the road makes the traffic
manager brake for someone who will never move.

#### When the bus stops moving

The backend watches for a bus that is making no progress and re-issues its
route. It can also lift a jammed vehicle back onto the lane, but **that is off
by default** (`unwedge_after_s=0.0`), because it teleports: a leg reached that
way is a KPI that did not happen, and a genuine wedge is better seen as a
failed leg than hidden. Every use is counted in `stats()`.

!!! warning "A bus at a red light looks exactly like a wedged one"
    Any tick covering less than 0.05 m counts as no progress — 1 m/s at the
    default step — which describes a bus waiting at a light just as well as
    one stuck against a kerb. Town01's lights cycle in groups of three, so an
    approach waits about 28 s of red, and the clock starts while the bus is
    still braking. Before this was accounted for, **all 20 unwedges across
    five tours fired on a stationary bus at a red light on a proper driving
    lane**, each teleporting it through the junction against the light.

    The stall clocks are now *frozen* while a red light holds the bus, rather
    than reset, so a bus genuinely jammed inside a junction still carries its
    accumulated time into the green.

#### It builds its own traffic manager

`start()` creates a `carla.TrafficManager` of its own, on its own port, after
loading the town. Two separate hazards make this necessary.

A traffic manager **caches the road network when it is created and never
re-caches it**, so one made before the map load steers by the previous town's
waypoints — silently, and looking exactly like bad driving. There is no API
to ask a traffic manager which map it holds, so not inheriting one is the only
cheap defence.

It is also **hosted by the process that created it**, not by the server: it is
an RPC service bound to a port, and `get_trafficmanager()` hands back a remote
proxy when another process owns that port. A proxy to a traffic manager that
is no longer being ticked — a Jupyter kernel that ran a trial and was never
restarted is the common case — will accept the vehicle and then never command
it, leaving the bus stationary at zero throttle and zero brake. Shutting down
the holder is not the answer either: it disconnects that client, and
re-binding the port immediately afterwards either fails with a bind error or
yields a traffic manager that drives nothing.

So the backend searches upward from port 8000 for one nobody is using, and
`close()` shuts down only what it created. Pass `tm_port` to override, in
which case managing it becomes the caller's job. See
[Running the Project](../running.md#running-the-carla-server).

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
