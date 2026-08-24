# Navigating in CARLA

## Overview

CARLA has no "drive to this place" API. Getting a bus from one stop to another
is therefore something this project implements, in
`src/simulation/carla_routing.py`, and it is the single component that has cost
the most debugging effort. This page exists so the next person does not
rediscover any of it.

```
plan_route(map, origin, destination)  ->  waypoints along lanes
agent.set_global_plan(waypoints)      ->  CARLA's own agent follows them
bus.apply_control(agent.run_step())   ->  every tick
```

The planned waypoints **are** the trajectory. There is no turn list, no
RoadOption to predict, no labelling or consumption rule, and nothing to
correct mid-drive. Measured over 112 legs across four towns at 0 and 20 NPCs:
56/56 legs arriving both times, zero dropped routes.

!!! note "The bus is not an autopilot vehicle"
    It was until 2026-08-16, and much of the difficulty recorded here comes
    from that period. It is not now: autopilot is off and the backend writes
    control every tick from `BasicAgent.run_step()`. The agent still handles
    red lights and vehicles ahead, so this is not hand-written driving — but
    the traffic manager is no longer choosing the route, and the bus can no
    longer change lanes or overtake.

Two earlier schemes handed the route to the traffic manager as a list of
turns. Both were deleted from the source on 2026-08-18, along with
`carla_steering.py`. [What was tried before](#what-was-tried-before) records
what they were and why they failed, because the campaign results archived
under `results/archive/` were produced by them.

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

## How the bus drives

Autopilot is switched off, `plan_route`'s waypoints are handed to
`agents.navigation.BasicAgent` via `set_global_plan`, and the backend applies
`agent.run_step()` on every tick. The agent obeys traffic lights and brakes
for vehicles ahead; `follow_speed_limits(True)` makes it target the posted
limit rather than its own 20 km/h default, which is what `traffic_efficiency`
is defined against.

`agents` is **not vendored** — it is installed from the pinned CARLA image by
`install-carla-agents.sh`. See [Running the Project](../running.md).

### The steering gain is ours, not CARLA's

One of `BasicAgent`'s defaults is overridden, in `_make_agent`.

`BasicAgent`'s lateral PID multiplies its gain by the **angle in radians**
between the bus's nose and the next un-retired waypoint. Because the command
is then clipped, the gain fixes an *angle at which the wheel goes to full
lock* — and CARLA's `K_P = 1.95` puts that at `0.8 / 1.95 = 0.41 rad`, or
**23.5°**. The planner retires waypoints within `3 + 0.5·v` metres, so on a
campus junction the target sits ~6 m ahead and 23.5° is crossed at once. The
bus slammed to full lock and, on the tightest corners, pirouetted.

`STEER_GAIN_K_P = 0.75` moves that threshold to 61°. Measured over a
four-leg tour against the default:

| town | full-lock ticks | off planned path |
|---|---|---|
| TAU | 37 → **16** | 0.10 → **0.06** m |
| Town01 | 1 → 1 | 0.13 → **0.07** m |
| Town03 | 0 → 0 | 0.15 → **0.08** m |

Path tracking gets **better**, which is the signature of a gain that was too
high: the old one overshot and corrected back, reversing the steering command
on 45% of ticks. The full-lock symptom is a *generated-map* one — the shipped
towns have wider junctions and barely saturate at all.

!!! danger "The steering ceiling is left at CARLA's 0.8, and must stay there"
    Capping it was tried and reverted. On its own, `max_steering = 0.45` cut
    peak yaw rate on TAU from 124 to 93 °/s — but it is also what the bus
    needs to get round a tight junction. At 0.45 it runs wide, leaves the
    4 m apron a generated map provides, and **falls out of the world**.

    Measured on TAU's leg 8 of 25, `n_s04_n0272 → n_s06_n0329`:

    | gain | ceiling | result |
    |---|---|---|
    | 1.95 | 0.80 | arrived 99 s, 0 falls |
    | 0.75 | 0.80 | arrived 100 s, 0 falls |
    | 1.95 | **0.45** | **failed, 99 falls** |
    | 0.75 | **0.45** | **failed, 99 falls** |

    0.60 survived that leg without traffic and still timed out with 20 NPCs.

    **A four-leg tour does not test this.** It never reaches leg 8. The
    change was validated on one, shipped, and the calibration that followed
    spent an hour timing out every leg after the bus was lost. Validate any
    steering change against a **full 25-stop TAU tour with NPCs** — 25/25
    legs, zero timeouts, is the bar.

!!! note "The gain does not make the bus slower"
    A four-leg Town01 tour came out 45 s longer, which reads as an 8% cost
    until it is charged to individual legs: three of the four were identical
    to within 0.25 s, and the fourth spent **34 s more stopped at red
    lights**. The bus reaches one junction a shade later and waits a phase.
    Calibration drives 3 tours of every stop, so a single light phase does
    not survive the average.

!!! warning "`lateral_control_dict` replaces the whole dict"
    Nothing puts `dt` back afterwards. Omitting it does not raise — the
    lateral PID quietly falls back to CARLA's 0.03 s and is mistuned against
    a 0.05 s world. `_make_agent` repeats `dt` inside the dict for exactly
    this reason.

Because both change how the bus drives, they change leg times, and
`traffic_efficiency` is fitted *by driving*. Changing either means
re-calibrating every town and bumping the `steering` tag in the run
identity — see [Running the Project](../running.md).

### Why this replaced the turn list

Every failure of the two schemes below lived in the **translation** between
our route and the traffic manager's re-derivation of it — four separate bugs
with one cause, described in [What was tried
before](#what-was-tried-before). Driving the waypoints deletes that
translation rather than trying to align it.

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

1. **`route_drops` should be 0.** Under direct drive the traffic manager is
   not steering, so a drop means something is still talking to it.
2. **`repaths` is effectively a collision counter.** The red-light and
   queueing exemptions cover every legitimate reason to sit still, so what is
   left is a bus physically stuck. Measured at about 1 per 56 legs.
3. **`unwedges` and `falls_recovered` should be 0**, and a run carrying either
   needs looking at before its KPIs are trusted — see
   [Simulation Backend](simulation_backend.md).
4. **A clean run proves nothing on its own.** Silence and a broken capture
   look identical, so confirm the capture works before trusting a zero.

## What was tried before

Kept as a record: nothing in the repository can drive this way any more, and
none of the identifiers below still exist.

Both removed schemes gave the traffic manager a **list of turns** rather than
a trajectory, and let it re-derive the route:

```
plan_route(map, origin, destination)   ->  waypoints along lanes
turns_along(route)                     ->  ["Left", "Straight", "Right", ...]
TrafficManager.set_route(bus, turns)   ->  CARLA drives it
```

The second variant (`closed_loop`) handed over nothing up front and corrected
one junction at a time, comparing `get_all_actions` against the plan as the
bus drove. It existed because the first was only ever as good as a prediction
that turned out not to be derivable.

**Every failure lived in the translation, not in either endpoint.** Our road
model is sampled at `STEP_M = 2.0` over `carla.Map`; CARLA's is sampled at
`MAP_RESOLUTION = 5.0` over `InMemoryMap`, with RoadOptions precomputed on
*that* graph. Four separate bugs came out of that single mismatch — how a turn
is named, yaw-wrap arithmetic, which junctions carry labels, and where a list
entry is consumed — and each fix relocated the failure rather than removing
it. An entry too many was as fatal as an entry too few, because every
subsequent junction then took the wrong one.

Three lessons outlived the code:

* **Never transcribe CARLA's own arithmetic literally.** Its turn naming
  compares yaw across a junction interior and the raw difference has to be
  wrapped to ±180° first; copying the expression without the wrap produces
  confident nonsense at the wrap boundary.
* **Measure agreement per *connection*, not per intention.** Aggregating by
  intended turn hides a systematic per-junction disagreement.
* **A fix validated only where the failure cannot occur has not been
  validated.** Several of these looked correct in an empty town.

The full account, with the measurements, is in this page's history at commit
`ffc9919` and earlier.

## Relationship to other components

- [Simulation Backend](simulation_backend.md) — `CarlaBackend` owns the actor
  and calls into this module; nothing above the backend knows CARLA exists.
- [Map](map.md) — `CarlaMapProvider` supplies the stops and the
  `traffic_efficiency` factor. Changing how the bus drives invalidates that
  factor, because it is fitted by driving.
- [Travel Model](travel_model.md) — what the policy *believes* about travel
  time, which is a separate quantity from what the bus does.
