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

start() -> None                                   # no-op by default
close() -> None                                   # no-op by default
set_planned_route(stop_ids: Sequence[str]) -> None  # no-op by default
sync_passengers(pool: RequestPool) -> None        # no-op by default

stats() -> dict[str, float]                 # optional; not declared at all
```

Only three methods are abstract, because only three questions genuinely need
the world to answer them. The rest have inert defaults so a world with no
setup and nothing to render does not have to say so — `set_planned_route`
and `sync_passengers` exist purely for worlds that *show* things, and
steering never consults either.

`stats()` is not on the base class in any form. `run_trial` looks for it by
duck typing and records whatever it returns as
[`TrialResult.backend_stats`](experiment.md#diagnostic-counters); a backend
without one simply reports an empty dict. That is the opposite of the
[`params()`](dispatch_policy.md#declaring-parameters) decision on policies,
and deliberately so: diagnostics are optional because most worlds have
nothing to report, whereas an undeclared *parameter* would corrupt a run's
identity in the results store.

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

Steering is CARLA's own driving, as the design review expected. The bus is
**not** an autopilot vehicle: `plan_route` searches `waypoint.next()` for a
lane-level, travel-time-shortest route to the stop, those waypoints go to
`agents.navigation.BasicAgent` via `set_global_plan`, and the backend applies
`agent.run_step()` every tick. The agent obeys traffic lights and brakes for
vehicles ahead. See [Navigating in CARLA](carla_navigation.md#direct-drive).

!!! warning "The review predicts this; it does not require it"
    §4 lists **CARLA vehicle control** as a risk — *"stop sequences must be
    translated into smooth low-level vehicle commands"* — mitigated by
    *"CARLA's built-in autonomous driving **should** be sufficient for a
    PoC"*, with that "should" hedged in the original. It also scopes the
    concern as *"contained within SimulationBackend, does not affect
    algorithm correctness"*. Nothing in §1.1 or §1.2 mandates the traffic
    manager, so replacing how the bus is driven is in scope.

**CARLA ships an equivalent planner** — `agents.navigation.GlobalRoutePlanner`
— but the pip wheel is client bindings only and does not include it, so it
would have to be copied into this repository either way. Ours was measured
against it and reproduces it exactly, in a fraction of the code and with no
extra dependency.

Until 2026-08-16 the bus was instead an autopilot vehicle handed a positional
list of junction manoeuvres through `TrafficManager.set_route`, kept fresh by
a periodic re-issue. That scheme and its failure modes were deleted on
2026-08-18; [Navigating in CARLA](carla_navigation.md) keeps the account,
because the runs under `results/archive/` were produced by it.

#### Re-planning after a dwell

The route for a leg is planned the instant the bus *arrives* at the previous
stop, while it is still doing ~8 m/s, and the dwell then brakes for its full
length. The bus rolls several metres past the point that route was planned
from, which puts the opening waypoints *behind* it by more than the local
planner's purge radius: they are never popped, the agent steers at a target
180 degrees astern, and the bus pirouettes at full lock. Measured on a Town01
trial before the fix: 306 m driven in 3360 s, 330 stall re-paths, every
request cancelled.

Re-planning once the dwell ends costs one search per stop and removes the
failure by construction, because the bus is stationary when it happens.

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

The backend watches for a bus making no progress and **re-plans its route**
from wherever it actually is. That is the whole of the response, and it moves
nothing, so it cannot manufacture a result.

It never fires with the default vehicle — 15 of 15 legs over 10 km without a
single re-plan, and zero across eight trials with traffic — which is the
reason to keep it rather than to remove it. The counter in `stats()` is how a
run tells you it needed help, and keeping that counter honest is why the two
exemptions below exist.

#### When the road itself is at fault

Re-planning moves nothing, which is its virtue and also its limit: it cannot
answer a bus the geometry has trapped. There is therefore a second, cruder
response — lifting the bus bodily back onto its lane — and the history of that
response is worth knowing, because it has been removed once and reinstated.

It was **removed** when each of its original justifications turned out to be
something else: red lights, a stale traffic manager and a `set_path` loop.
Deleting it left the driven distances identical to the metre.

It was **reinstated** for a case none of those explain. On the TAU campus map
the bus entered one junction, ended up rolled 16 degrees onto its side edge
and sunk 19 cm into the surface, and sat there at 75% throttle for the rest of
the leg. Twenty-one consecutive legs failed from that single spot, because
nothing ever got it out again. No amount of re-planning helps a bus that
cannot move.

!!! warning "Where the bus is put back matters, and the obvious choice is wrong"
    Projecting a beached bus onto the road with `get_waypoint` returns a point
    on the road it is *standing on*, which inside a junction is that road's
    beginning — measured, **51 m behind** the bus. Recovering to there sent it
    forward into the same trap and round again.

    So the bus is placed along its **planned route** instead, far enough on to
    clear whatever caught it: on a real lane by construction, and forward.
    Velocity is zeroed too, or the momentum it had while beached carries into
    the new position.

    The reach escalates — `UNWEDGE_AHEAD_M` (8 m) times the number of attempts
    on this leg, capped at `UNWEDGE_MAX_AHEAD_M` (60 m) — because one TAU trap
    is a whole junction wide, and at a fixed 8 m the bus landed back inside it
    and beached again on the next tick.

#### Falling out of the world

A generated map is a bare ribbon of road (see [Map](map.md)), so a lane
departure does not scrape a kerb — it drops the bus into nothing. Anything
below `FALLEN_BELOW_M` (−5 m) counts as fallen and is recovered the same way.

This is checked **before** the no-progress test, because a bus in free fall is
moving faster than any driving it will ever do and the movement test would
never notice. Left alone it never comes back and every subsequent leg starts
already lost: measured on TAU, one fall cost the twenty legs after it.

!!! danger "Both of these are counted, and a campaign must read the counters"
    A leg reached by teleporting is a KPI that did not happen. Neither repair
    is hidden: `stats()` reports `unwedges` and `falls_recovered`, and a run
    carrying either is a run to look at before trusting. They are simulator
    guards against a map defect, not driving.

!!! warning "A bus at a red light looks exactly like a wedged one"
    Any tick covering less than 0.05 m counts as no progress — 1 m/s at the
    default step — which describes a bus waiting at a light just as well as
    one stuck against a kerb. Town01's lights cycle in groups of three, so an
    approach waits about 28 s of red, and the clock starts while the bus is
    still braking. Before this was accounted for, **121 of 124 re-plans, and
    every one of the 20 lifts that then existed, fired on a stationary bus at
    a red light on a proper driving lane**.

    The stall clock is now *frozen* while a red light holds the bus, rather
    than reset, so a bus genuinely jammed inside a junction still carries its
    accumulated time into the green.

!!! warning "Nor does the red-light exemption cover a bus queued behind one"
    CARLA reports `is_at_traffic_light()` only inside the light's **trigger
    volume** — which is to say, at the *head* of the queue. Everyone behind
    the first vehicle reads as a bus stopped on green for no reason.

    In an empty town the bus is always at the head, so this could not arise,
    and it went unnoticed until traffic was tested: at 20 NPCs, one trial
    logged 3 re-plans where the same seed with an empty town logged none.
    Instrumenting them, **4 of 5 firings had the bus away from any light with
    a stationary vehicle directly ahead**.

    `_is_queued_behind_traffic` now exempts that case and counts
    `queued_ticks` instead, so the wait is still reported. Its thresholds —
    20 m ahead, 3 m across, 0.5 m/s — are the ones that measurement used, not
    guesses. It reads a single world snapshot and short-circuits when no NPCs
    were spawned, so it costs nothing in an empty town.

    The general lesson outlived the bug: **a fix validated only in conditions
    where the failure cannot occur has not been validated.** The red-light
    exemption was correct and incomplete, and only traffic could show it.

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
[Running the Project](../running.md#the-carla-server).

#### A clean world per trial

`start()` loads the town when the wrong one is up. `reload_world=True` makes
it load unconditionally, so the trial begins in a world nothing has touched.

`close()` destroys the actors this backend created, so an ordinary sequence
of trials does not leak — and neither does a broken one any more. `start()`
spawns before it can fail, and every caller reaches it *before* its own
`try`/`finally`, so a failure part-way through used to leave everything
behind. Measured: a viewer that could not open left the bus standing in the
road, and a bus that could not be placed stranded every NPC already spawned,
so the next trial drove among five vehicles in a world its identity called
`npc_vehicles=0`. Worse, the failed `start()` had already switched the world
to synchronous mode, which blocks every later client and makes the *next*
backend record synchronous as the setting to "restore" — one failure poisoned
the rest of the server session. `start()` now closes what it opened before
re-raising, on `BaseException`, because a Ctrl-C mid-start leaves the same
mess.

Leaked actors remain the classic cross-trial contaminant — a stray vehicle
parked across a junction changes the next trial's driving and nothing reports
it — so a campaign still reloads between trials and pays the map load —
seconds — for a world with a known state.

#### Scenery that blocks a lane

CARLA's own towns are not all clear of their own scenery. Measured on Town02:
a tree standing 4.8 m off the carriageway has a collision volume reaching
1.0 m *into* a 4.00 m lane. A vehicle centred in that lane and wider than
about 2 m overlaps it and is thrown — at any speed from 10 to 40 kph, driving
dead straight, with no steering input at all — while the default 1.77 m
shuttle clears it by 0.1 m and never notices it is there.

`carla_world.OBSTRUCTIONS` names those objects per town, and `start()`
switches them off through `World.enable_environment_objects` on every run.
This is damage in the town rather than a parameter of an experiment, so it is
not left to the caller to remember. `stats()` reports
`obstructions_removed`, so a run says what it stripped.

A disabled object still appears in `world.get_environment_objects()` — only
its collision and its rendering go away — so that list is not how to check
the removal worked.

It defaults to `False` because a single interactive run wants the opposite:
re-running a notebook cell would otherwise pay the load every time for
nothing.

The load happens **before** the traffic manager is created, which is the same
ordering constraint as above and for the same reason.

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
