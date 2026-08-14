# Navigating in CARLA

## Overview

CARLA has no "drive to this place" API. Getting a bus from one stop to another
is therefore something this project implements, in `src/simulation/carla_routing.py`,
and it is the single component that has cost the most debugging effort. This
page exists so the next person does not rediscover any of it.

The chain is three steps:

```
plan_route(map, origin, destination)   ->  waypoints along lanes
turns_along(route)                     ->  ["Left", "Straight", "Right", ...]
TrafficManager.set_route(bus, turns)   ->  CARLA drives it
```

The bus stays an ordinary autopilot vehicle throughout. We never write throttle
or steering — we only tell the traffic manager which way to go at each
decision point, and it does the driving.

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
tree and not in the installed package. Using it would mean copying ~1200 lines
into this repository. `plan_route` was validated against it and reproduces it,
in a fraction of the code and with no vendored dependency.

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

### A junction is labelled if it branches *or* is signalised

From `InMemoryMap.cpp`, a waypoint gets a directional label when it branches,
**or** when it is a non-junction waypoint about to enter a junction — and in
that second case only if `GetAllLandmarksInDistance(15.0)` finds a landmark of
type `1000001` (traffic light), `206` (stop sign) or `205` (yield sign).
Otherwise it stays `LaneFollow`, and no entry is spent there.

`carla_routing._is_labelled` mirrors this rule exactly. It is the whole reason
the module reaches for landmarks at all.

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

## Known remaining difference

`turns_along` and CARLA name the turns slightly differently:

| | span measured | straight threshold |
|---|---|---|
| `turns_along` | approach heading vs exit heading | `TURN_THRESHOLD_DEG = 35.0` |
| CARLA (`InMemoryMap`) | first vs last waypoint *inside* the junction | `STRAIGHT_DEG = 19.0` |

Audited across two full tours this disagreed on **1 junction in 146**, so it
was left alone. It is the first place to look if drops reappear on a town
where the labelling rule is satisfied.

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

CARLA's complaints go to **file descriptor 1 from C++**, never through
`sys.stdout`, and are block-buffered into clumps of about forty. Both problems
are handled by `src/simulation/carla_stdout.py`, which captures the descriptor,
disables the buffering, and tags every message with the bus's town, seed, leg,
road, lane and position. `CarlaBackend.stats()` carries the total as
`route_drops`.

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
