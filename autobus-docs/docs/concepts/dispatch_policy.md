# Dispatch Policy

## Overview

The `algorithm` package defines how the bus decides which stops to visit and
in what order. The core abstraction for this is `DispatchPolicy`.

There are two main concepts:

1. **`BusState`** — a snapshot of the bus at a point in time, passed as input
   to the policy on each planning call.
2. **`DispatchPolicy`** — an abstract base class whose concrete implementations
   contain the actual routing logic.

## BusState

`BusState` is a plain dataclass that carries everything the policy needs to
make a routing decision:

| Field | Type | Meaning |
|-------|------|---------|
| `position` | `str` | TransitGraph node ID (road edge ID) of the bus's current location. |
| `onboard` | `list[Request]` | Passengers currently on the bus (`ON_BUS`). Their dropoff stops are obligations the route must eventually satisfy. |
| `pending_requests` | `list[Request]` | Requests still waiting at stops (`PENDING`). The bus may choose to pick these up. |
| `remaining_capacity` | `int` | How many more passengers the bus can accept before it is full. |

`BusState` is constructed by the bus agent, not by `DispatchPolicy`. When the
agent has already committed to a sequence of upcoming stops, it passes the
*projected* state after those stops have been completed — so the policy always
plans forward from a clean starting point with no knowledge of prior
commitments.

## DispatchPolicy

`DispatchPolicy` is an abstract base class with one abstract method:

```python
plan(state: BusState, n_stops: int) -> list[str]
```

Given a `BusState` snapshot and a desired route length, `plan` returns an
ordered list of stop IDs representing the next `n_stops` stops the bus should
visit.

The `TransitGraph` is injected at construction time (`__init__`) because it
is a static world model that does not change between planning calls:

```python
policy = SomeConcretePolicy(graph)
route = policy.plan(state, n_stops=5)  # ['stop-A', 'stop-C', 'stop-B', ...]
```

### Relationship to other components

```
DemandGenerator ──► RequestPool ──► (bus agent) ──► DispatchPolicy
                                         │                │
                                         │     BusState ──┘
                                         │
                                         └── commits to first m stops,
                                             re-queries with projected state
```

The bus agent is responsible for:

- Deciding when to re-invoke `plan` (e.g. at each stop, or when new requests
  arrive).
- Choosing how many stops to commit to (`m`) versus treat as tentative.
- Projecting the post-commitment state before passing it to `plan`.

`DispatchPolicy` has no knowledge of commitments; from its perspective it
always receives a current state and returns a fresh route.

## Implementing a concrete policy

A concrete policy subclasses `DispatchPolicy` and implements `plan`. The
method receives the full `BusState` and can use `self._graph` (the injected
`TransitGraph`) to compute travel costs via `TransitGraph.best_path_cost`.

Internally, most policies will follow three steps:

1. **Candidate generation** — identify stops worth considering (dropoffs for
   onboard passengers, pickups for pending requests, filtered by reachability
   or capacity).
2. **Scoring** — evaluate candidates or sequences of candidates. This is the
   primary axis on which policies differ: one might weight passenger wait time,
   another might minimise total travel distance.
3. **Selection** — build the best `n_stops` sequence from scored candidates
   (greedy, exhaustive over small `n`, beam search, etc.).

There is no contract requiring that every onboard passenger's dropoff stop
appears in the returned list. Whether to guarantee this is a design choice
left to each concrete implementation — it is an intentional axis of comparison
between policies.

## FixedRoutePolicy

`FixedRoutePolicy` is the baseline concrete policy. It follows a fixed,
repeating cycle of stops regardless of passenger demand — the bus behaves
like a real-life scheduled bus line.

### Construction

```python
# Explicit stop order (TransitGraph node IDs):
policy = FixedRoutePolicy(graph, stop_order=["3_5", "7_2", "1_4"])

# Auto-generated from all stops in the graph:
policy = FixedRoutePolicy(graph)
```

When `stop_order` is omitted, the policy collects every stop node from the
graph and orders them using a **nearest-neighbor heuristic**: starting from
the first stop, it repeatedly visits the closest unvisited stop (by
shortest-path travel time). This runs in O(n²) time and produces a
reasonable loop with minimal backtracking.

### Routing behaviour

`plan()` ignores `state.onboard` and `state.pending_requests` entirely. It
finds where in the cycle the bus currently is, then returns the next `n_stops`
entries, wrapping around at the end:

- If the bus is at a stop that belongs to the cycle, planning continues from
  the *following* stop in the cycle.
- If the bus is not at a cycle stop (e.g. at startup before reaching the
  first stop), it heads to the nearest cycle stop first.

Because the route is fixed, the commitment window in `BusAgent` has no
effect on route diversity — it only determines how far ahead the bus has
confirmed its path.

## NearestUnservedPolicy

`NearestUnservedPolicy` is the first demand-responsive concrete policy. On
each planning call it builds a route greedily, one stop at a time, by always
choosing the nearest stop that serves an active need.

### Candidate stops

At each step, two sets of candidates are considered:

- **Dropoff stops** — the `dropoff_stop_id` of every onboard passenger.
  Always eligible.
- **Pickup stops** — the `pickup_stop_id` of every pending passenger.
  Eligible only when `remaining_capacity > 0` (the *capacity gate*).

The capacity gate prevents the bus from routing to a pickup stop it cannot
serve: when the bus is full, only dropoff stops are candidates until a
passenger alights and frees a seat.

Pickup candidates additionally pass through an overridable
`_pickup_eligible(pickup_stop, position)` hook (default: always eligible).
Subclasses use it to filter pickups without duplicating the greedy loop —
see `MarginalCostFilterPolicy` below.

### Routing behaviour

`plan()` simulates the bus's future state locally — it does not mutate real
state. Starting from `state.position`, the simulation at each step:

1. Builds the candidate set as described above.
2. Picks the candidate with the lowest `best_path_cost` from the simulated
   current position.
3. Appends it to the route.
4. Advances the simulated state: passengers alight at their dropoff stop, then
   pending passengers board at their pickup stop (up to available capacity).

This repeats for at most `n_stops` steps. Planning stops early when the
candidate set is empty — i.e. all active demand has been served. The returned
list may therefore be shorter than `n_stops`; `BusAgent` will re-plan on the
next stop arrival regardless.

### Construction

```python
policy = NearestUnservedPolicy(graph)
```

No additional arguments are required beyond the `TransitGraph`.

## SkipStopFixedRoutePolicy

`SkipStopFixedRoutePolicy` is a demand-aware variant of `FixedRoutePolicy`.
It subclasses `FixedRoutePolicy`, inheriting the exact same cycle
construction and ordering, but overrides `plan()` to **skip stops that have
no boarding or alighting demand**.

Preserving the fixed cycle *order* is the key property: because stops are
never reordered, the worst-case wait is still bounded by one full cycle,
exactly as in a plain fixed route. Skipping empty stops only ever shortens a
cycle, so it can improve average wait and throughput without making the
worst case worse — the reason it is a natural first comparison point against
`FixedRoutePolicy`.

### Construction

Identical to `FixedRoutePolicy` — an explicit `stop_order`, or auto-generated
from the graph:

```python
policy = SkipStopFixedRoutePolicy(graph, stop_order=["3_5", "7_2", "1_4"])
policy = SkipStopFixedRoutePolicy(graph)
```

### Routing behaviour

`plan()` walks the fixed cycle from the current position for **at most one
full pass**, keeping only stops where a passenger would board or alight given
the projected bus state:

1. A stop is kept if any onboard passenger alights there, **or** a pending
   passenger picks up there and there is room after alighting (the *capacity
   gate* — a pickup-only stop is skipped while the bus is projected to be
   full).
2. When a stop is kept, the simulated state is advanced (alight, then board)
   before evaluating the next stop; skipped stops leave the state unchanged.
3. The walk stops once `n_stops` stops have been collected.

The returned list may be shorter than `n_stops` when demand is sparse, and
empty when there is no demand at all — matching the conventions of the other
demand-responsive policies. Because it is a single-cycle scan, a pickup
skipped while the bus is full is simply left for the next re-plan.

The alight/board simulation used here (`_simulate_stop`) lives on the
`DispatchPolicy` base class and is shared with `NearestUnservedPolicy`.

## MarginalCostFilterPolicy

`MarginalCostFilterPolicy` subclasses `NearestUnservedPolicy` and adds a single
rule: it **skips a pending pickup whose travel cost from the current position
exceeds a threshold**. Skipped requests stay `PENDING` — they are not
cancelled, so a later planning call (or, in a multi-bus fleet, another bus) may
still serve them. This is the *soft-skip* model: the policy spends a small
amount of unfulfilled demand to avoid expensive diversions, aiming to improve
average wait and ride time and the wait tail relative to unfiltered greedy.

Onboard passengers' dropoffs are committed obligations and are **never**
filtered — only pickups pass through the cost gate. The filter reuses the
`_pickup_eligible` hook on `NearestUnservedPolicy`, so the greedy loop itself is
inherited unchanged.

### Threshold

The pickup cost threshold is either supplied explicitly or auto-derived, in the
same spirit as `FixedRoutePolicy`'s explicit-or-auto `stop_order`:

```python
# Explicit absolute threshold (in travel-cost units):
policy = MarginalCostFilterPolicy(graph, max_pickup_cost=0.4)

# Auto-derived from the graph (default):
policy = MarginalCostFilterPolicy(graph)

# Auto-derived, but tuning how far a diversion may go:
policy = MarginalCostFilterPolicy(graph, cost_factor=3.0)
```

When `max_pickup_cost` is omitted, the threshold is `cost_factor` (default
`2.0`) times a **graph-derived baseline**: the median over stops of each stop's
nearest-neighbor travel cost — a scale-free proxy for typical inter-stop
spacing. Auto-derivation raises `ValueError` if the graph has fewer than two
stops (no baseline can be computed); pass `max_pickup_cost` explicitly in that
case.

### Routing behaviour

Identical to `NearestUnservedPolicy` except that, at each greedy step, pickup
candidates whose `best_path_cost` from the simulated position exceeds the
threshold are excluded. Unreachable pickups (infinite cost) are therefore
filtered automatically. When every remaining candidate is a filtered pickup and
there are no dropoffs, planning stops and the returned route may be shorter than
`n_stops` (or empty) — leaving that demand pending.

## RideBoundedGreedyPolicy

`RideBoundedGreedyPolicy` subclasses `NearestUnservedPolicy` and adds a single
rule aimed at a specific weakness of plain greedy routing: its **onboard ride
time**. Because plain greedy always heads for the nearest candidate, under dense
demand there is almost always a nearer *pickup* than any onboard passenger's
dropoff, so the bus keeps deferring dropoffs and drags its riders around — a ride
tail that grows without bound as demand rises. This policy caps that tail by
forcing the bus to deliver riders before they are over-detoured.

Unlike `MarginalCostFilterPolicy`, it **never rejects** demand: the rule only
*reorders* stops. A pickup passed over because riders need delivering stays
`PENDING` and is served on a later step (or a later plan) once the bus has
capacity to detour again. Coverage therefore matches the base greedy policy
while the ride tail is bounded — the appropriate trade-off for a single bus,
where a rejected request has no other bus to fall back on.

### The ride-slack gate

Each onboard rider has a **ride budget** — the most along-route travel their
trip should incur:

- either an explicit absolute `max_ride_cost`, applied to every rider, or
- `ride_factor` times that rider's **direct** cost
  (`best_path_cost(pickup_stop_id, dropoff_stop_id)`), so the tolerated detour
  scales with trip length.

At each greedy step, for every onboard rider the policy computes the *slack*
that remains if it delivered them next:

```
slack = budget − (ride_so_far + best_path_cost(position, dropoff_stop_id))
```

where `ride_so_far` is the along-route distance the rider has accrued **within
the current plan**. If any onboard rider has non-positive slack, the *gate
fires*: pickup candidates are suspended for that step and only onboard dropoffs
are considered, so the bus makes delivery progress. When every rider still has
slack, the inherited candidate set is used unchanged and the policy behaves
exactly like `NearestUnservedPolicy` (including its capacity gate and progress
guarantee). Selection is always the nearest candidate by `best_path_cost`.

Because ride is measured within a single plan, a rider already onboard when the
plan begins starts from `ride_so_far = 0` — the portion of their ride before
this plan is not visible, since `plan()` has no access to simulation time. This
is the same accepted limitation as `ConstrainedInsertionPolicy`'s geometric
bound: frequent re-planning re-checks the bound each time the bus reaches a
committed stop, so the cap is enforced repeatedly over the trip rather than once.

### Construction

```python
# Per-rider budget = ride_factor × direct trip cost (default ride_factor=1.5):
policy = RideBoundedGreedyPolicy(graph)

# Tighter or looser ride cap via the trade-off knob:
policy = RideBoundedGreedyPolicy(graph, ride_factor=1.3)

# Explicit absolute cap applied to every rider (in travel-cost units):
policy = RideBoundedGreedyPolicy(graph, max_ride_cost=1200.0)
```

A larger `ride_factor` (or `max_ride_cost`) lets the bus detour more before
delivering, trading a longer ride tail for slightly higher coverage; a smaller
one caps rides more aggressively. Unlike `MarginalCostFilterPolicy`, no minimum
stop count is required — with an empty bus the gate can never fire, so the policy
degrades gracefully to plain greedy.

## ConstrainedInsertionPolicy

`ConstrainedInsertionPolicy` subclasses `DispatchPolicy` directly (not the
`NearestUnservedPolicy` greedy family) and takes a different approach: rather
than picking one nearest stop at a time, it builds the route by **cheapest
insertion**. Each pending request is evaluated against the *entire* planned
sequence — the policy tries inserting the request's pickup and dropoff at every
valid slot pair and keeps the cheapest placement that stays feasible. A request
with no feasible placement is **rejected**: it is left out of the route and
stays `PENDING` (the *soft-skip* model), free to be served by a later plan.

### Route representation

Internally the route is a list of **events**, each a
`(node_id, load_delta, owner_id)` triple: `load_delta` is `+1` for a pickup and
`-1` for a dropoff, and `owner_id` ties the event to the request it serves so
per-rider ride cost can be measured. Onboard passengers seed the route first —
their dropoffs are ordered nearest-neighbor from the current position — and are
**never rejected**, though their remaining leg is still subject to the ride
bound. Pending requests are then inserted in **FIFO order** (`created_at`) for
fairness; regret / cheapest-first insertion is a noted later refinement.

### Insertion and the two bounds

For each request, `_best_insertion` tries every `(pickup slot, dropoff slot)`
pair with the pickup no later than the dropoff, discards pairs that violate a
constraint, and keeps the one giving the lowest total sequence cost. Three
constraints gate a placement:

- **Capacity** — the running onboard load (starting from the passengers already
  aboard) must never exceed the bus's total seats.
- **Wait bound** — the along-route travel cost from the current position to the
  newly served pickup must not exceed `max_wait_cost`.
- **Ride bound** — after the insertion, *every* affected rider's along-route
  pickup→dropoff cost must stay within `ride_factor` times their direct
  shortest-path cost. Crucially this protects **all** riders, not just the new
  one: any already-planned passenger the insertion would detour is checked too,
  guarding mean ride time against excessive ride-sharing. An onboard rider (whose
  pickup is already in the past) is measured from the current position to their
  dropoff.

A subtle but important interaction: with pure cheapest insertion, placing a
pickup immediately followed by its dropoff is always ride-clean, so the ride
bound can only ever reject a request in combination with the wait bound — the
wait bound forbids the cheap adjacent placement, and the remaining wait-feasible
slots are what risk over-detouring someone. The ride bound is therefore inert
unless a finite wait bound is also in force.

Note that "wait" here is the **forward** along-route time from the current
position to the pickup, not the absolute wait since the request was created —
`plan()` has no access to simulation time. The bound thus governs the
routing-controlled portion of wait, not time already spent queueing.

### Rejection and the progress guarantee

A rejected request simply does not appear in the route and remains `PENDING`.
If *every* pending request is rejected and the bus would otherwise idle, a
progress fallback serves the nearest pending pickup anyway — without this, an
all-rejected plan would return an empty route and freeze the bus at its position
forever (its position never changes, so nothing new ever becomes feasible). This
mirrors the progress guarantee in `NearestUnservedPolicy`.

### Construction

```python
# Auto-derived wait bound (default): wait_factor × median inter-stop cost,
# with ride_factor = 1.5.
policy = ConstrainedInsertionPolicy(graph)

# Explicit absolute wait bound (in travel-cost units):
policy = ConstrainedInsertionPolicy(graph, max_wait_cost=0.4)

# Tune the auto-derived wait bound and the ride detour allowance:
policy = ConstrainedInsertionPolicy(graph, wait_factor=3.0, ride_factor=2.0)
```

When `max_wait_cost` is omitted, the wait bound is `wait_factor` (default `2.0`)
times a graph-derived baseline — the median travel cost over all ordered stop
pairs, the same baseline `MarginalCostFilterPolicy` uses. Auto-derivation raises
`ValueError` if the graph has fewer than two stops; pass `max_wait_cost`
explicitly in that case.

Because it re-evaluates every slot pair for every request, this policy is the
most computationally expensive of the family — a deliberate trade of planning
cost for route quality and the ability to reason about service bounds.
