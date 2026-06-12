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
