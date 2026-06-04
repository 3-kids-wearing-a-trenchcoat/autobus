# Bus Agent

## Overview

`BusAgent` is the decision-making component that sits between the simulation
runner and the routing policy. It owns the bus's mutable state — current
position, onboard passengers, and the planned route — and is the only component
that writes passenger state transitions back to the `RequestPool`.

The runner interacts with `BusAgent` through two methods:

- **`next_stop()`** — read the next stop to drive to.
- **`on_arrival(stop_id)`** — notify the agent that the bus has arrived; the
  agent handles boarding/alighting and re-plans before returning.

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
(maximum responsiveness). A larger commitment trades responsiveness for
stability: the bus will not abandon stops it is already committed to reaching
even when new requests arrive.

## Projected state

Rather than asking the policy to plan from the bus's *current* real position,
`BusAgent` constructs a **projected `BusState`** that reflects where the bus
will be *after* executing the committed prefix:

- The projected position is the last committed stop.
- Passengers that would alight at committed stops are removed from `onboard`.
- Passengers that would board at committed stops are added to `onboard`
  (respecting capacity), and removed from `pending_requests`.

This means `DispatchPolicy` always plans from a clean starting point with no
knowledge of the agent's internal commitments. The projection logic lives
entirely inside `BusAgent`.

## Relationship to other components

```
RequestPool ◄──────────────────────────────────────────┐
     │                                                  │
     │  get_pending() / update_state()                  │ update_state()
     ▼                                                  │
 BusAgent ──► _build_projected_state() ──► DispatchPolicy.plan()
     │
     └──► next_stop()  ──►  simulation runner
```

`BusAgent` holds a reference to the shared `RequestPool`. The simulation runner
and demand generator also write to the pool (adding new requests); `BusAgent`
only reads pending requests and transitions their states to `ON_BUS` or
`FULFILLED`.
