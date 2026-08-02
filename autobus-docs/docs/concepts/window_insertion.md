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

The window is the headline knob and the trade-off is sharp. Measured on a
2000m planar map (median inter-stop hop ~132s, 50 requests, capacity 40,
abandonment at 900s, 8 seeds, at the then-default `commitment=2` — see below,
the numbers shift with that setting):

| `wait_factor` | window | fulfilled | cancelled | mean wait | p90 wait |
|---------------|--------|-----------|-----------|-----------|----------|
| 3             | 396s   | 33.6      | 16.4      | 339s      | 550s     |
| 5 (default)   | 660s   | 41.0      | 9.0       | 426s      | 642s     |
| 8             | 1057s  | 39.9      | 10.1      | 422s      | 742s     |
| 20            | 2642s  | 39.9      | 10.1      | 402s      | 727s     |

Too tight and most requests are rejected; too loose and it degenerates into
unbounded waiting, converging on the no-window behaviour. The default is a
reasonable starting point, not a universal answer — it is exactly the kind of
value worth sweeping per map.

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
