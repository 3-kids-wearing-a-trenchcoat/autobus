# Navigating in CARLA

## Overview

CARLA has no "drive to this place" API. Getting a bus from one stop to another
is therefore something this project implements, in `src/simulation/carla_routing.py`
and `src/simulation/carla_steering.py`, and it is the single component that has
cost the most debugging effort. This page exists so the next person does not
rediscover any of it.

!!! success "Current design: the bus drives the route itself"
    Since 2026-08-16 `CarlaBackend` **defaults to `direct_drive=True`**. The
    planned waypoints go straight to CARLA's own
    `agents.navigation.BasicAgent`, which follows them and handles red lights
    and vehicles ahead:

    ```
    plan_route(map, origin, destination)  ->  waypoints along lanes
    agent.set_global_plan(waypoints)      ->  CARLA's agent follows them
    bus.apply_control(agent.run_step())   ->  every tick
    ```

    There is **no turn list, no RoadOption to predict, no labelling or
    consumption rule, and nothing to correct mid-drive**. The planned
    waypoints *are* the trajectory. Measured over 112 legs across four towns
    at 0 and 20 NPCs: **56/56 legs arriving both times, zero dropped
    routes**, and 55% less simulated time than the turn list — which loses
    three legs entirely.

    Everything below documents the **superseded** `set_route` scheme. It is
    kept because it is still reachable (`direct_drive=False`), because the
    campaign results recorded before that date were produced by it, and
    because its failure modes took a month to understand and should not be
    rediscovered. See [Direct drive](#direct-drive) at the foot of this page.

There are two ways to do it. The original hands over the whole leg up front:

```
plan_route(map, origin, destination)   ->  waypoints along lanes
turns_along(route)                     ->  ["Left", "Straight", "Right", ...]
TrafficManager.set_route(bus, turns)   ->  CARLA drives it
```

The other, `CarlaBackend(closed_loop=True)`, hands over nothing up front and
corrects one junction at a time as the bus drives:

```
plan_route(map, origin, destination)   ->  waypoints along lanes
junction_turns(route)                  ->  [(861, "Right"), (20, "Straight"), ...]
                                              ^ keyed by junction, not position
each tick:
  TrafficManager.get_all_actions(bus)  ->  what CARLA intends just ahead
  disagrees with the plan?             ->  set_route(bus, [turn], True)
```

The rest of this page explains why the second exists: the first is only ever as
good as a prediction that turned out not to be derivable. **Start at
[Closed-loop steering](#closed-loop-steering) if you only want the current
design**; the sections before it are the history that justifies it, and two of
their conclusions are now marked refuted.

The bus stays an ordinary autopilot vehicle throughout, under either scheme. We
never write throttle or steering — we only tell the traffic manager which way to
go at each decision point, and it does the driving.

## Why not just give CARLA the destination

Because the API that looks like it takes a destination does not.

`TrafficManager.set_path` accepts a list of locations, and it reads like
waypoint navigation. It is really a **junction-decision** API: at each junction
the traffic manager picks whichever exit best advances towards the next point
it holds. Given a single point half a town away, that choice is greedy, and on
a grid it can cycle forever.

Measured on Town01, a bus told only where its stop was drove a ten-road loop
indefinitely. It came within 78 m, and in 600 seconds of driving it covered
3 km without ever entering the road the stop was on — still 78 m away, exactly
the distance it had reached in the first 150 seconds. The same leg with a turn
list takes 142 s.

!!! warning "Do not revisit whole-path `set_path`"
    Handing `set_path` the *entire* planned route, rather than one distant
    point, is the obvious repair and it was measured: 6/13 legs arrived
    against `set_route`'s 13/13, and it spent 99.6% of a 3000 s budget
    off-route on a leg `set_route` drives in 150 s.

CARLA does ship a route planner — `agents.navigation.GlobalRoutePlanner` — but
the pip wheel contains client bindings only, so it exists in CARLA's source
tree and not in the installed package. `plan_route` was validated against it
and reproduces it, in a fraction of the code.

!!! note "The `agents` package is available, and not by vendoring"
    An earlier version of this page treated using it as equivalent to copying
    ~1200 lines into the repository. It is not: `install-carla-agents.sh`
    installs the package from the pinned CARLA image into the environment's
    `site-packages`, so it is a dependency like any other and no CARLA code
    lives here. See [Running the Project](../running.md).

## How CARLA consumes the turn list

This is the part that must be understood exactly, because everything that has
gone wrong here has gone wrong through it. Both rules below are read from
CARLA 0.9.16's own source.

### An entry is spent at a label transition

Every waypoint in the traffic manager's internal map carries a `RoadOption`:
`LaneFollow`, `Left`, `Right`, `Straight`. From `LocalizationStage.cpp`:

```cpp
if (latest_road_option != next_wp_selection->GetRoadOption() &&
    next_road_option == next_wp_selection->GetRoadOption()) {
  imported_actions.erase(imported_actions.begin());
}
```

An entry is consumed when the waypoint buffer **crosses into a stretch whose
label equals the entry currently being requested**. Note what this is *not*:

- it is **not** one entry per junction;
- it is **not** one entry per branch;
- it can happen **silently**, at a junction where nothing branches.

The familiar complaint is a separate branch of the same function, and fires
only where the buffer genuinely forks and no fork carries the requested label:

```
We couldn't find the RoadOption you were looking for.
This route might diverge from the one expected.
```

When that happens CARLA takes the last candidate anyway, so the bus keeps
driving — briskly, and in the wrong direction.

### A junction is labelled if it branches *or* is signalised — **refuted**

!!! warning "The rule is right; an earlier reading of the measurement was not"
    This page briefly claimed `_is_labelled` "asks about the wrong object".
    **That was wrong**, and it is corrected below: the gate really is
    evaluated on the approach waypoint, exactly as `_is_labelled` does. What
    the measurement established is where the resulting label is *stored*,
    which is a different question.

The rule as read: a waypoint gets a directional label when it branches, **or**
when it is a non-junction waypoint about to enter a junction and
`GetAllLandmarksInDistance(15.0)` finds a landmark of type `1000001` (traffic
light), `206` (stop sign) or `205` (yield sign).

What sampling `get_all_actions` every tick, over 600 s of free driving per town
with no imported route, actually shows:

| town | non-junction waypoints | of those, labelled | junction waypoints | labelled |
|---|---|---|---|---|
| Town01 | 477 | **0** | 119 | 119 |
| Town03 | 308 | **0** | 324 | 213 |
| Town05 | ~185 | **0** | ~112 | 112 |

**CARLA never labels a non-junction waypoint**, and labelling is uniform per
connection `(road_id, lane_id)` — zero mixed connections in any town measured.

Both facts follow from the source, and neither contradicts `_is_labelled`:

```cpp
if (found_landmark || next_swp_size > 1) {          // decided on the APPROACH
  ...
  for (auto &twp : traversed_waypoints) {           // stored on the INTERIOR
    twp->SetRoadOption(ro);
  }
}
```

`next_swp_size` is `swp->GetNextWaypoint().size()` and `found_landmark` comes
from `swp->GetWaypoint()->GetAllLandmarksInDistance(15.0)`, both on the
**approach** waypoint `swp` — which is precisely what `_is_labelled` asks. The
label is then written to *every* interior waypoint, which is why it is found
there and why connections are uniformly labelled. So the signal clause is
real, and `SIGNAL_LANDMARK_TYPES` / `SIGNAL_RANGE_M` are CARLA's own criteria.

Nor is the real predicate recoverable. Scoring all 36 observed Town03
connections on full-connection geometry separates nothing:

```
     turn: labelled [0..91.5]   unlabelled [0..90.5]    <- total overlap
  signals: labelled [0..2]      unlabelled [0..0]       <- most labelled have 0
 siblings: labelled [1..5]      unlabelled [1..5]
   length: labelled [14..42]    unlabelled [2..38]
```

Labelled connections include 0.0° turns; unlabelled ones include 90.5° turns.
Five predicates have been tried and failed — branching; branching-or-
signalised; every junction; interior turn > 19°; full-connection geometry.

That search was, in hindsight, aimed at the wrong thing. The labelling
predicate was never missing: it is `found_landmark || next_swp_size > 1`, and
`_is_labelled` implements it. What geometry cannot recover is why a list built
on the correct predicate still slipped — and the likeliest answer is the
*consumption* rule rather than the labelling rule. An entry is spent at a
label **transition**, and because the label is written to every interior
waypoint, a junction is one long run of identical labels; two junctions meeting
with no ordinary road between them are a single run. Counting labelled
junctions is therefore not the same as counting consumption sites.

This weakens the original case for closed-loop steering without touching its
results: it still measures better, but "no predicate exists" was not the
reason.

## Why this bit us, and why only on some towns

`turns_along` originally asked *"does this lane branch?"* — which is not the
question CARLA asks. The two give the same answer **only when every junction
branches**, and that is true of a single-lane grid and false of a multi-lane
map: a dedicated turn lane has exactly one continuation through a junction
that is nevertheless signalised, and therefore labelled, and therefore spends
an entry.

Audited per junction over a full tour of every stop:

| town | junctions on tour | non-branching **and** signalised |
|---|---|---|
| Town01 | 52 | **0** |
| Town03 | 94 | **15** |

Checked a second way, over **every ordered stop pair** rather than the tour —
210 per town, which is every leg a policy could ask for:

| town | pairs | turn lists identical under both rules |
|---|---|---|
| Town01 | 210 | **210** |
| Town02 | 210 | **210** |
| Town03 | 210 | 85 (322 entries were missing) |
| Town05 | 210 | 102 (129 entries were missing) |

So Town01 and Town02 were correct by accident, and Town03 and Town05 lost an
entry at every such junction. Because `set_route` consumes in order, **one
missing entry throws every later turn onto the wrong junction** — the list
silently slips by one and the bus is refused at junction after junction.

Measured on Town03's full tour, seed 0, with replanning switched off so the
routing is the only variable:

| turn list built on | legs arrived | dropped routes | simulated time |
|---|---|---|---|
| branching | 9/14 | 4 | 7 395 s |
| **labelling** | **14/14** | **3** | **3 948 s** |

!!! danger "An entry too many is as fatal as an entry too few"
    The tempting over-correction — emit a turn at *every* junction the route
    enters — was measured at **15× the dropped routes**, 7/14 legs arriving
    and 64% more driving. Junctions that are neither branching nor signalised
    keep `LaneFollow` and never consume the entry aimed at them, so it is
    spent later at the wrong place. The list must match CARLA's labelling
    exactly: not more, not less.

## Closed-loop steering

`CarlaBackend(closed_loop=True)` removes the prediction rather than improving
it, in `src/simulation/carla_steering.py`. Three properties, and each one
kills a specific failure above:

* **Keyed by junction, not by list position.** `junction_turns` returns
  `(junction id, turn)` pairs, so an entry can only ever be spent at the
  junction it was computed for. A missing or extra one costs nothing instead of
  throwing every later turn onto the wrong junction. It also expresses
  something a positional list cannot at all: the Town03 leg `s05 -> s11`
  crosses **junction 861 twice** and turns differently each time.
* **CARLA is the oracle.** A junction its action buffer labels `Left`, `Right`
  or `Straight` is a decision point; one left at `LaneFollow` is not, and is
  left alone. Nothing has to guess which junctions spend an entry.
* **One entry outstanding at a time**, aimed at a junction currently in the
  buffer, so a mistake cannot compound into the rest of the leg.

A junction the traffic manager is deciding at that the plan does not contain
means the bus has left its route. That is reported so the backend can re-plan
from where the bus actually is — an **off-route trigger for free**, firing on
the divergence rather than 232 s later, which is where `replan_every_s` got to
it. `STEER_REPLAN_COOLDOWN_S` bounds how often it can fire, because an
unroutable destination would otherwise start a whole-town search twenty times a
second.

### The mechanic this depends on

`set_route(bus, [turn], True)` **re-labels a junction the buffer has already
decided on**, taking effect on the *next* world tick — the traffic manager
re-decides when it ticks, so a same-tick re-read shows no change and reads as a
false null. Verified 4/4 on Town03 with a positive control that forced a label
CARLA had not chosen.

The lead available is small: `get_all_actions` returns 1–3 entries spanning
about 19 m, and costs 0.003 ms, so it is affordable every tick. On an open
approach a junction appears 21–25 m ahead; in a cluster of close junctions the
buffer is already inside the next one and it appears 0.1–5.2 m ahead.
**Corrections at 4 m of lead or more took; one at 2.5 m did not.** That is the
design's known ceiling, and the off-route trigger is what covers it.

### Measured, four towns, 56 legs

Every leg driven from a **fresh spawn at its own origin** on a **fresh server**,
seed 0, no traffic, `replan_every_s=None` so steering is the only variable:

| town | arm | arrived | simulated | driven | legs identical |
|---|---|---|---|---|---|
| Town01 | list | 14/14 | 1891 s | 11 986 m | |
| Town01 | **closed loop** | **14/14** | 1891 s | 11 986 m | **14/14** |
| Town02 | list | 14/14 | 1063 s | 6 735 m | |
| Town02 | **closed loop** | **14/14** | 1063 s | 6 735 m | **14/14** |
| Town03 | list | 11/14 | 10 558 s | 45 311 m | |
| Town03 | **closed loop** | **14/14** | **2 471 s** | **12 310 m** | 10/14 |
| Town05 | list | 14/14 | 2928 s | 10 777 m | |
| Town05 | **closed loop** | **14/14** | 2928 s | 10 777 m | **14/14** |

**52 of 56 legs come out bit-identical.** Town01, Town02 and Town05 are
untouched to the decimal, which is the property that matters most: those towns
were already correct, so any scheme that perturbed them would be wrong whatever
it did elsewhere. All four changed legs are on Town03 — three that never
arrived now do, and one that regressed.

The three Town03 legs that the positional list loses, and what they cost:

| leg | list | closed loop |
|---|---|---|
| `s05 -> s06` | never arrives, 11 044 m | **193 s, 831 m** |
| `s10 -> s11` | never arrives, 12 369 m | **114 s, 823 m** |
| `s11 -> s12` | never arrives, 12 852 m | **303 s, 888 m** |

!!! note "Two open items"
    `s02 -> s03` on Town03 **regressed**, 218 s to 520 s, with 12 corrections
    and 3 re-plans — it still arrives, but worse. And the Town03 closed-loop
    arm raises **4 route drops** where the list arm raises none. Under closed
    loop a drop is informative rather than a slip: it means the turn we asked
    for does not exist at that junction, most likely the naming difference
    below.

## Naming a turn the way CARLA names it

Both readers classify a manoeuvre through `_turn_name`, which uses **CARLA's
own rule**: the heading change from the **first to the last waypoint inside
the junction**, with anything under `STRAIGHT_DEG = 19.0` counted straight.
That is what `InMemoryMap.cpp` does, and matching it matters most under
closed-loop steering, which hands CARLA the word and compares it against the
label CARLA reports.

Measured per junction connection while the bus is inside the junction — which
is the only honest way to compare, see the warning below — this agrees with
CARLA's own runtime label on **173 of 173 connections across all four towns**.

!!! danger "Do not transcribe CARLA's arithmetic literally"
    CARLA computes `(int16)back_yaw - (int16)front_yaw % 360` and tests
    asymmetric bands. That was copied verbatim on 2026-08-16, wrap-around
    quirk and all, on the reasoning that a connection CARLA labels `Left`
    must be *asked for* as `Left`. **It is wrong for us**, because `% 360`
    only rescues magnitudes above 360: a raw difference of −358 — a 2°
    bend across the yaw wrap — falls outside both the straight band and the
    right band and drops through to `Left`. And **CARLA's yaws are not
    normalised**; values measured on our sampled waypoints include −270.21
    and −539.91, so it fires often.

    Shipped, it renamed nine junctions and **every one went `Straight` → a
    direction**, asking CARLA for turns it could not supply:

    | town | arm | before | after |
    |---|---|---|---|
    | Town05 | list | 14/14, **0** drops | 12/14, **170** drops |
    | Town05 | closed loop | 14/14, 0 drops | 13/14, 55 drops |
    | Town03 | closed loop | 14/14, 4 drops | 12/14, 48 drops |

    Wrap the angle first — which is what CARLA's arithmetic is trying and
    failing to do — then apply its threshold.

!!! warning "Measure naming agreement per *connection*, not per intention"
    Comparing our planned turn against whatever the buffer reports ahead is
    **not** a naming measurement. Before a correction lands, the buffer
    describes the exit *CARLA* intends, which is a different connection from
    the one we want, so it disagrees for reasons that have nothing to do
    with naming. That confusion made "29 corrections on Town01" look like a
    naming fault when Town01 in fact agrees on every connection.

Two limits are known and accepted:

* CARLA samples junction interiors at `MAP_RESOLUTION = 5.0` and we sample at
  `STEP_M = 2.0`. Five Town03 junctions name differently under the two
  resolutions; every connection actually compared agrees, so this is a latent
  risk rather than an observed fault.
* CARLA walks a junction with `temp.front()`, taking the first successor even
  at an internal fork, so its span can follow a different connection from the
  one the vehicle drives. Exact matching is not achievable in general.

## The safety net: re-issuing the route

Even with a correct list, a leg can drift — a lane change, a nudge from
traffic, an unlucky junction. `CarlaBackend(replan_every_s=...)` re-issues the
route periodically from wherever the bus actually is. It corrects no
individual turn; it re-establishes the *alignment*, so drift is discarded
instead of compounding.

This matters because drift is invisible to everything else. `_track_stall`
reacts only to a bus that has **stopped**, and exempts red lights and queues —
so a bus driving briskly in the wrong direction trips no alarm, and the
`repaths` counter reads zero while it wanders.

Two rules for the period, both measured:

- **Longer than a healthy leg.** At 300 s every shorter leg came out
  bit-identical to leaving it alone, so a leg going well is never disturbed.
  At 30 s and 120 s the same tour turned a 233 s leg into 778 s.
- **Never pass `empty_buffer=False`.** It looks like the polite way to
  re-issue without disturbing the vehicle, and it measured **28% worse than
  doing nothing**: the stale buffer's junctions consume entries from the new
  list before the bus reaches the junctions they were computed for.

## Diagnosing a town that misbehaves

Everything below is about reading the log after the fact. To watch it happen
instead — the route drawn on the road, a marker where each drop fired, and
the plan and request table on a HUD — see
[Watching a Run](watching_a_run.md).

CARLA's complaints go to **file descriptor 1 from C++**, never through
`sys.stdout`, and are block-buffered into clumps of about forty. Both problems
are handled by `src/simulation/carla_stdout.py`, which captures the descriptor,
disables the buffering, and tags every message with the bus's town, seed, leg,
road, lane and position. `CarlaBackend.stats()` carries the total as
`route_drops`.

To read the messages **from code** rather than off the terminal — a notebook
picking one bad run apart — register a callback with
`carla_stdout.add_listener`. It is handed the counter name and the tagged
line for every message the module attributes, and never the unrelated
pass-through output; `remove_listener` takes it off again. `drain` reports
only counts, because its caller on the tick path has no use for the text.

When adding or debugging a town:

1. **`route_drops` should be 0 or near it.** A leg that drops routes is a leg
   whose turn list has slipped.
2. **Check the tagged line, not the counter.** `next_junction` names the
   junction the traffic manager was refused at, and `issued` says how many
   turns the leg was given.
3. **Compare junction counts.** For each leg, the entries `turns_along` emits
   should equal the junctions on the route that CARLA would label. A
   systematic shortfall is the bug this page is about, returning.
4. **A clean run proves nothing on its own.** Silence and a broken capture
   look identical, so confirm the capture works before trusting a zero.

## Relationship to other components

- [Simulation Backend](simulation_backend.md) — `CarlaBackend` owns the actor
  and calls into this module; nothing above the backend knows CARLA exists.
- [Map](map.md) — `CarlaMapProvider` supplies the stops and the
  `traffic_efficiency` factor. Changing how the bus drives invalidates that
  factor, because it is fitted by driving.
- [Travel Model](travel_model.md) — what the policy *believes* about travel
  time, which is a separate quantity from what the bus does.


## Direct drive

The scheme in force. `CarlaBackend(direct_drive=True)`, the default.

Autopilot is switched off, `plan_route`'s waypoints are handed to
`agents.navigation.BasicAgent` via `set_global_plan`, and the backend applies
`agent.run_step()` on every tick. The agent obeys traffic lights and brakes
for vehicles ahead; `follow_speed_limits(True)` makes it target the posted
limit rather than its own 20 km/h default, which is what `traffic_efficiency`
is defined against.

`agents` is **not vendored** — it is installed from the pinned CARLA image by
`install-carla-agents.sh`. See [Running the Project](../running.md).

### Why this replaced the turn list

Every failure documented above lived in the *translation* between our route
and the traffic manager's re-derivation of it: our road model is sampled at
`STEP_M = 2.0` over `carla.Map`, CARLA's is sampled at `MAP_RESOLUTION = 5.0`
over `InMemoryMap` with RoadOptions precomputed on **that** graph. Four
separate bugs, one cause — turn naming, the yaw-wrap arithmetic, the
labelling predicate, and where entries get consumed. Each fix relocated the
failure rather than removing it.

Direct drive deletes the translation instead of aligning it.

| arm | arrived | simulated | driven | dropped routes |
|---|---|---|---|---|
| positional list | 53/56 | 16 440 s | 74 808 m | 0 |
| closed loop | 56/56 | 8 352 s | 41 808 m | 4 |
| **direct drive** | **56/56** | **7 434 s** | **40 035 m** | **0** |

### What it gave up, and what that cost

The bus is no longer a traffic-manager vehicle, so **it cannot change lanes
or overtake**. It queues behind slow traffic instead of going around. That is
visible in the calibration as within-leg spread rising from 0.028 to between
0.04 and 0.19 at the same 20 NPCs: a leg's duration now depends on the
traffic it happens to meet. It is policy-neutral — every arm meets the same
traffic — but it widens confidence intervals, which §3.2 constrains.

!!! warning "The plan is the trajectory, so *where* it was computed matters"
    A turn list did not care where it was planned from. A waypoint list does.
    The route for the next leg is planned the instant the bus **arrives**,
    while it is still doing ~8 m/s, and a dwell then carries it several
    metres past those opening waypoints — beyond the local planner's purge
    radius, so they are never popped and the agent steers at a target 180°
    astern. The bus pirouettes at full lock.

    Measured before the fix on a Town01 trial: **306 m driven in 3360 s, 330
    stall re-paths, every request cancelled.** `_replan_after_dwell` re-plans
    once the bus is stationary, which removes it by construction.

    Leg-level validation never caught this, because a leg starts from a fresh
    stationary spawn with `dwell_s=0`. It is why a smoke *trial* is worth
    running before any campaign.

### The stall detector still earns its place

`_track_stall` is not a routing crutch — under direct drive it is the net for
a bus **physically displaced**, which re-planning genuinely fixes. It fires
once in 56 legs, on a Town02 corner where the bus clips the geometry at
8 m/s and sits wedged at full throttle until re-planned out. That costs 0.16%
of total simulated time.

Its red-light exemption had to change: `BasicAgent` brakes
`_base_tlight_threshold` short of a light, which is **outside** the trigger
volume, so `bus.is_at_traffic_light()` is False and a bus waiting ~28 s at a
Town01 red read as stalled against a 10 s threshold — 234 phantom re-paths
across four towns. `_agent_holds_for_hazard` asks the agent instead, taking
it to 1. `get_traffic_lights_from_waypoint` does **not** substitute: the
agent latches its light and holds, so the public query returns nothing.

`_track_replan` — the periodic re-issue — is inert under direct drive. It
existed only because a turn list is handed over once per leg and never
refreshed.
