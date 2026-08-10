# Window Insertion

## Overview

`WindowInsertionPolicy` implements the **window insertion heuristic** from the
dial-a-ride literature (Jaw, Odoni, Psaraftis & Wilson, 1986 — "ADARTW"). It
keeps one long-lived schedule and grows it one request at a time, accepting a
request only if the bus can reach its pickup inside a hard deadline.

Two pieces make it up: `Schedule`, the ordered plan and its timing arithmetic,
and the policy itself, which decides what goes into that plan.

## Why windows

Fixed routes are hard to beat, and the reason is structural rather than
clever: a bus that cycles visits every stop within a bounded period, so the
worst-case wait is bounded for free. Demand-responsive policies have no such
guarantee — they chase whatever is convenient and strand the awkward requests,
which is why they lose on the metrics that punish tails.

A service window buys that guarantee back without cycling. Every request gets a
hard deadline of `max_wait_s` after it was made, and an insertion is feasible
only if the projected arrival at that pickup respects it. Requests that cannot
be served in time are **rejected** rather than served late.

Rejection is a *soft skip*: the request stays `PENDING` and remains eligible for
a later plan, when the bus is somewhere more convenient.

!!! note "The deadline binds insertions, not boardings"
    The guarantee is narrower than "nobody is picked up late". It is: *no
    request is accepted into a schedule that predicts a late pickup*.

    Two things can still produce a late boarding. The **progress fallback**
    drives to the nearest pending pickup whenever nothing at all can be
    scheduled, deliberately ignoring the window — a late pickup beats a
    stalled bus. And under a backend whose driving is slower than the estimate
    (which is what `NoisyTravelModel` simulates, and what CARLA will do for
    real), an insertion certified feasible can still be executed late.

    Measured on the campus map at W750 over 250 trials, the fallback alone
    breaches the window for **2.3%** of fulfilled requests *with exact
    driving*. Execution noise adds to that: +1.3 pp at σ=0.1, +2.5 at σ=0.2,
    +3.3 at σ=0.3 — decelerating rather than compounding, because re-planning
    on each arrival re-anchors on the real clock.

    `safety_factor` is the lever if that matters. At σ=0.2, raising it to 1.25
    cuts the breach rate from 4.9% to 2.0% at **no cost in coverage**
    (+0.1 pp, not significant) — but pays about +107 s of mean ride for L0,
    and +67 s for L1. Since ride time is a graded KPI and breach rate is not,
    the default stays 1.0. See `notebooks/execution-noise.ipynb`.

## `Schedule`

An immutable, ordered list of `Event`s — a node, a load delta (`+1` boarding,
`-1` alighting), and the request the event serves.

Immutability is deliberate. The insertion search tries every possible slot pair
for a request, and returning a fresh schedule per candidate is far easier to
reason about than mutating a shared one and undoing it.

### Visits, not events

Several events at the same stop collapse into a single **visit**: a stop where
three passengers board is one pull-over, not three. This matters for timing —
dwell is charged **once per visit** — and it is why `Schedule` computes its
timeline over collapsed visits rather than raw events.

`stop_sequence` additionally drops a leading stop equal to the bus's current
position, since a zero-length self-transition would stall the bus.

### Forward slack

`forward_slack` answers, for each event, *how much later could the bus get here
without pushing anything downstream past its deadline*. One backward pass
computes it for the whole schedule:

```
slack[k] = min(deadline[k] - arrival[k], slack[k+1])
```

Because `slack[0]` is the running minimum over everything, a single
non-negative check tells you the entire schedule is feasible.

## The policy

### Cost function

A candidate is scored by the total time its passengers spend on it — every
rider's wait plus their ride:

```
wait = arrival at pickup  - request created
ride = arrival at dropoff - arrival at pickup
```

These are exactly the quantities `MetricsTracker` averages, because
`boarded_at` and `fulfilled_at` are both stamped from stop-arrival times. The
policy therefore optimises the reported KPI directly rather than a proxy such
as vehicle distance.

Riders **already aboard** are scored from their real `boarded_at`, so an
insertion that detours the bus is charged for the extra time it costs them.
That charge is what stops the search from serving each new request at everyone
else's expense — and it is the main thing separating insertion from a greedy
nearest-request policy.

### Reconciliation

The policy is stateful, so its schedule drifts from reality between calls. Four
things are repaired at the top of every `plan`:

1. Requests that left the system — fulfilled, or abandoned by an impatient
   passenger — are dropped.
2. Riders who have boarded lose their scheduled pickup but keep their dropoff.
3. Riders who boarded **opportunistically** — `BusAgent` boards anyone waiting
   at a stop the bus reaches, including riders this policy rejected — have
   their dropoff adopted as a commitment.
4. Promises that have become unkeepable are released.

See [Dispatch Policy](dispatch_policy.md) for why this is done by inspecting
the `BusState` rather than through callbacks.

### Recovery

Step 4 never fires while travel estimates are exact: an insertion proven
feasible stays feasible. It exists for CARLA, where traffic means a commitment
made in good faith can quietly become impossible, and the bus should stop
driving a route it can no longer honour.

Releasing the offenders in one pass is enough — removing a stop can never delay
the stops after it, since shortest-path costs obey the triangle inequality and
one fewer visit means one fewer dwell.

### Safety factor

`safety_factor` inflates travel estimates when testing feasibility, so a
schedule survives a drive that runs slower than predicted. Dwell is not
inflated: it is a property of the stop, not of the traffic. The default of
`1.0` plans against the raw estimate, which is exact in the mock simulation.

## Construction

```python
# Window derived from the graph (5x the median inter-stop cost)
policy = WindowInsertionPolicy(graph)

# Explicit window, matching the simulation's abandonment threshold
policy = WindowInsertionPolicy(graph, max_wait_s=900.0)

# Plan pessimistically, for noisy travel estimates
policy = WindowInsertionPolicy(graph, max_wait_s=900.0, safety_factor=1.2)
```

The window is the policy's **own service promise**, deliberately independent of
the backend's `max_wait_s` abandonment threshold. Setting it at or below that
threshold gives a testable claim: no accepted request is ever abandoned.
Setting it above buys higher acceptance at the cost of that guarantee.

!!! warning "The two `max_wait_s` values are not linked"
    Nothing in the code ties the policy's window to the backend's abandonment
    threshold. If a sweep varies one, vary the other deliberately — a window
    looser than the threshold silently voids the guarantee above.

## Tuning

The window is the headline knob and the trade-off is sharp. Measured on the
campus map (21 stops, 2000 m square, 200 requests, capacity 40, abandonment at
900 s, **800 trials**, `commitment=0`), against the two fixed-route baselines:

!!! danger "The 21 stops come from `stop_density=0.5`, not from the default"

    Reproducing this needs
    `PlanarStreetGraph.generate(n_seeds=17, bounds=(0, 0, 2000, 2000),
    seed=42, stop_density=0.5)`. The generator defaults to **0.15**, which on
    the same seed yields **6 stops** — a map so easy that plain fixed route
    serves 100% and the whole table below flattens into noise. See
    [Route Reordering](route_reordering.md#what-it-actually-buys) for the
    session where that caught someone out.

| | Fixed | Skip | No window | W450 | W600 | W750 | W900 |
|---|---|---|---|---|---|---|---|
| Served | 68.6 % | 74.8 % | 82.6 % | 82.8 % | 87.5 % | 91.1 % | **93.1 %** |
| Mean wait | 451 s | 447 s | 379 s | **316 s** | 375 s | 424 s | 466 s |
| P90 wait | 804 s | 792 s | 735 s | **463 s** | 587 s | 723 s | 849 s |
| Mean ride | 654 s | 587 s | **356 s** | 874 s | 710 s | 596 s | 521 s |
| Max ride | 1302 s | **1261 s** | 1536 s | 4756 s | 3802 s | 3154 s | 2591 s |

Every interval is under ±2% of its mean, so the ordering is not in doubt.

Three things to read off it:

- **Coverage rises with the window**, peaking where the window equals the
  abandonment threshold. Beyond that it falls away sharply — see
  `notebooks/window-width-sweep-2.ipynb` for why.
- **The ride tail is worst at the tight end, not the loose end.** `W450` posts
  the best wait figures on the page and by far the worst max ride (4756 s). A
  tight deadline makes the bus chase expiring pickups, which defers dropoffs
  and drags whoever is already aboard.
- **The unbounded ablation is the ride-quality winner** — 356 s mean ride,
  better than every windowed column and than both fixed-route baselines, while
  still serving 82.6%. It is the only demand-responsive column whose max ride
  lands anywhere near the fixed-route cycle cap.

Against the project's goal of beating fixed-route, **`W750` is the strongest
single answer**: better on served (91.1% vs 68.6%), mean wait, P90 wait *and*
mean ride, losing only on max ride. No windowed column beats fixed-route's
structural 1302 s ride cap — that comes free from cycling, and is hard to buy
any other way. [Route reordering](route_reordering.md) closes part of the gap.

The `wait_factor` default of 5.0 derives a window from the graph alone
(≈660 s here, between the `W600` and `W750` columns). When the abandonment
threshold is known, set `max_wait_s` to it explicitly rather than relying on
the derived value.

### Interaction with the commitment window

The service window also bounds how long `BusAgent`'s committed prefix may be.
The agent hands this policy a state projected `commitment` stops into the
future, but a request's deadline is measured from when it was *made* — so once
the projection runs further ahead than the window is wide, every new request
arrives already expired and acceptance collapses. At `commitment=8` on the
campus map the served fraction falls from 93.4% to 63.4% and 89% of insertion
attempts are rejected.

`commitment` now defaults to `0`, so this does not bite unless it is raised
deliberately. If it is, check it against the window first — see
[Bus Agent](bus_agent.md#the-upper-bound) for the measured rule.
