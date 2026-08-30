# Bus Agent

## Overview

`BusAgent` is the decision-making component that sits between the simulation
runner and the routing policy. It owns the bus's mutable state — current
position, onboard passengers, and the planned route — and is the only component
that writes passenger state transitions back to the `RequestPool`.

The runner interacts with `BusAgent` through five methods/properties:

- **`position`** — read the bus's current TransitGraph node ID.
- **`next_stop()`** — read the next stop to drive to. Deliberately a pure
  read: it never triggers a re-plan.
- **`on_arrival(stop_id, sim_time)`** — notify the agent that the bus has
  arrived; the agent handles boarding/alighting and re-plans before returning.
  `sim_time` is the current simulation time in seconds, used to stamp
  `boarded_at` and `fulfilled_at` on the affected requests, and retained as the
  anchor for the projected planning clock.
- **`replan_if_idle(sim_time)`** — give a bus with an empty route a chance to
  notice work that arrived while it sat. See below.
- **`planned_route()`** — read the whole planned route, not just its head. The
  runner passes it to `SimulationBackend.set_planned_route`, which is a no-op
  everywhere except `CarlaBackend`, where it feeds the overlay and the
  viewer's minimap. Like `next_stop()`, a pure read.

### Waking a parked bus

Re-planning is otherwise triggered only by *arriving somewhere*, which leaves
a hole. A bus whose route runs out never arrives anywhere again, so it never
re-plans, so its route stays empty — and it sleeps through every request that
follows.

Measured with a 200 s demand interval against 10 s legs: the bus served the
first request, then sat still for the remaining 20,000 s with four passengers
waiting.

That went unseen for a long time because the runner used to end a trial the
moment the pool emptied — the same moment the bus would have parked — so the
deadlock was reachable only inside the window the old termination check
closed. The two defects concealed each other, and fixing
[`is_done`](simulation_runner.md#run-loop-and-lifecycle) exposed this one
immediately.

`replan_if_idle` is safe precisely because the bus is **stationary**: there is
no committed leg to contradict and no mid-leg position to argue about, which
is the condition under which re-planning was already established to be free. A
bus **in motion** still re-plans only on arrival.

`BusAgent` is also given a `TravelModel` at construction. It needs one to work
out *when* the bus will finish the committed prefix — projecting past those
stops means adding up travel and dwell times, which the agent has no other way
to measure.

## Re-planning cycle

Every time the bus arrives at a stop, `BusAgent` runs the following sequence:

```
arrive at stop
    │
    ├─ alight passengers whose destination == stop  →  FULFILLED
    ├─ board pending passengers waiting at stop     →  ON_BUS
    │
    └─ _replan()
           │
           ├─ lock committed prefix of current route
           ├─ simulate committed stops → projected BusState
           └─ policy.plan(projected_state, n_stops) → new tail
```

The new route is the committed prefix concatenated with the tail returned by
the policy.

## Commitment window

`commitment` (a constructor parameter) controls how many leading stops of the
current route are treated as locked when a re-plan fires. These stops are not
replaced by the new plan — only the stops beyond them are updated.

A commitment of `0` means the route is fully re-planned on every arrival
(maximum responsiveness). A larger commitment was intended to trade
responsiveness for stability: the bus would not abandon stops it is already
committed to reaching even when new requests arrive.

**Measurement did not support that trade.** Sweeping `commitment` over
`{0, 1, 2, 4, 8}` on the campus map found it monotonically harmful, with `0`
best on every KPI (20 trials, W900, 200 requests):

| `commitment` | 0 | 1 | 2 | 4 | 8 |
|---|---|---|---|---|---|
| Served fraction | **93.4 %** | 91.6 % | 91.3 % | 88.8 % | 63.4 % |
| Mean wait | **469.6 s** | 490.1 s | 504.7 s | 532.3 s | 535.5 s |
| Mean ride | **543.6 s** | 549.8 s | 564.5 s | 612.5 s | 610.6 s |

Re-measured at **800 paired trials**, the ordering holds and every difference
is significant at p < 0.001, but the size of the step from `2` to `0` is
smaller than the 20-trial sweep suggested — **+1.4 pp served and −27.8 s mean
wait** for `Window 900s`, against the +2.0 pp / −35 s first estimated. Treat
the sweep table above as the shape and the figures here as the magnitude.

Every policy tested improved or was unaffected by dropping to `0`
(800 trials, paired, `commitment=0` minus `commitment=2`):

| Policy | Served | Mean wait | Mean ride |
|---|---|---|---|
| Fixed route | +0.0 ± 0.0 | +0.0 ± 0.0 | +0.0 ± 0.0 |
| Fixed route w/ skip | **+3.0 pp** | **−13.6 s** | +0.5 s (ns) |
| Insertion (no window) | **+2.5 pp** | **−37.0 s** | **−9.1 s** |
| Window 600s | **+3.8 pp** | **−30.0 s** | **−82.8 s** |
| Window 750s | **+2.1 pp** | **−28.0 s** | **−46.0 s** |
| Window 900s | **+1.4 pp** | **−27.8 s** | **−26.3 s** |

`FixedRoutePolicy` is *exactly* indifferent — a locked prefix of a fixed cycle
is what the policy would have returned anyway, so the paired differences are
identically zero. `NearestUnservedPolicy` is flat (p = 0.96).
`SkipStopFixedRoutePolicy` gains, because a locked prefix forces it to visit
stops whose demand has since been served or abandoned.

!!! warning "One exception: a tight window plus full responsiveness costs ride time"
    `Window 450s` gains the most coverage of any column (+8.7 pp) but its mean
    ride gets **worse** by 49.9 s (p < 0.001) — the only regression in the
    table. A tight deadline combined with the freedom to re-plan every arrival
    means more chasing of expiring pickups, which defers dropoffs.

`run_trial` therefore defaults `commitment` to `0`. It should only be raised if
re-planning ever moves off *stationary* moments: today the triggers are
`on_arrival` and `replan_if_idle`, and the bus is parked for both, so it can
freely change its next destination. A vehicle re-planning **mid-leg** could
not — it is already driving somewhere — and would need at least `1`.

That distinction matters if mid-leg re-planning is ever revisited: the
measurements above say a locked prefix is harmful, but they were taken where
the bus only ever planned while stopped, so locking was pure loss. Mid-leg,
locking the current leg is not a tuning choice but a physical fact, and these
numbers would not transfer.

### The upper bound

Commitment does not merely cost a little more as it grows — past a threshold
the policy stops functioning. The projected clock advances by roughly one leg
per committed stop, so the policy plans from about `commitment × median_leg`
seconds into the future, while a request's deadline is measured from when it
was *made*. Once the projection runs further ahead than the service window is
wide, every newly created request is already expired before the insertion
search begins, and acceptance collapses.

Sweeping `commitment` at three service windows confirmed the breakdown point
moves with the window rather than sitting at a fixed number of stops:

| Service window | `window / median_pair_cost` | Rejection rate passes 90% at |
|---|---|---|
| 600 s | 4.5 | ~4.5 stops |
| 900 s | 6.8 | ~8 stops |
| 1350 s | 10.2 | ~13 stops |

!!! warning "Keep the prefix short relative to the window"
    `commitment × median_leg` must stay well below the policy's service window.
    The observed breakdown sits at about 1.0–1.25× the ratio tabulated above,
    because the median cost over *all* stop pairs overstates the leg the bus
    actually drives — a policy picks nearby stops, not random ones.

Measured on the campus map (21 stops, 2000 m square, median ordered-pair cost
132 s), swept and then confirmed on a second run.

## Projected state

Rather than asking the policy to plan from the bus's *current* real position,
`BusAgent` constructs a **projected `BusState`** that reflects where the bus
will be *after* executing the committed prefix:

- The projected position is the last committed stop.
- Passengers that would alight at committed stops are removed from `onboard`.
- Passengers that would board at committed stops are added to `onboard`
  (respecting capacity), and removed from `pending_requests`.
- The projected `now` is the time the bus **departs** the projected position.

That third bullet has a consequence worth stating plainly: a pending request
whose pickup falls inside the committed prefix is handed to the policy as
*already aboard*. It never appears in `pending_requests`, so a scheduling
policy never gets the chance to plan it and must adopt its dropoff after the
fact. This is the dominant source of unscheduled riders — measured at ~5
adoptions per 100 plan calls at `commitment=0` rising to ~63 at
`commitment=8`, roughly 5 per committed stop. It is one more reason the
default is `0`.

### The projected clock

Starting from the arrival time handed to `on_arrival`, the agent walks the
committed prefix and accumulates one dwell plus one drive per stop:

```
now = arrival_time + dwell(position)
for each committed stop:
    now += travel(previous, stop) + dwell(stop)
```

This mirrors `MockBackend`'s per-leg arithmetic exactly — that backend charges
`dwell + travel` for every leg — so a schedule projected here matches what the
simulation will actually execute, rather than approximating it. Under a
backend whose driving is not known in advance, it becomes an estimate again.

This means `DispatchPolicy` always plans from a clean starting point with no
knowledge of the agent's internal commitments. The projection logic lives
entirely inside `BusAgent`.

!!! note "Opportunistic boarding"
    `_board` boards **every** pending passenger waiting at the arrival stop, up
    to capacity — not only those the policy planned for. Boarding also happens
    *before* `_replan`, so a request created during the leg into a stop is
    already `ON_BUS` by the time the policy next sees the world. A policy that
    keeps a persistent schedule must therefore expect riders it never
    scheduled to turn up onboard. See [Dispatch Policy](dispatch_policy.md)
    for the reconciliation obligation this creates.

    Riders the policy *deliberately rejected* can also turn up this way, but
    they are the minority path: at `commitment=0` none of the measured
    adoptions were requests the policy had previously been offered, and at
    `commitment=2` only about 12% were. The rest are requests it never got to
    see at all.

## Relationship to other components

```
RequestPool ◄──────────────────────────────────────────┐
     │                                                  │
     │  get_pending() / update_state()                  │ update_state()
     ▼                                                  │
 BusAgent ──► _build_projected_state() ──► DispatchPolicy.plan()
     │
     ├──► position     ──►  simulation runner
     └──► next_stop()  ──►  simulation runner
```

`BusAgent` holds a reference to the shared `RequestPool`. The simulation runner
and demand generator also write to the pool (adding new requests); `BusAgent`
only reads pending requests and transitions their states to `ON_BUS` or
`FULFILLED`. At each transition it stamps the corresponding timestamp field on
the request (`boarded_at` when boarding, `fulfilled_at` when alighting), making
per-journey timing data available to `MetricsTracker`.
