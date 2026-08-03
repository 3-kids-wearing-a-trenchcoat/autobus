# Travel Model

## Overview

`TravelModel` is how a policy estimates two things: how long it takes to drive
between two nodes, and how long the bus is held at a stop.

```python
travel_time(from_id: str, to_id: str) -> float
dwell_time(stop_id: str, n_boarding: int, n_alighting: int) -> float
```

Both return seconds.

## Why this exists separately from the backend

The simulation already knows how long driving takes —
`SimulationBackend._travel_time` is the ground truth, supplied per backend. So
why add a second source for the same quantity?

Because they are not the same quantity. One is **what happens**; the other is
**what the policy believes will happen**.

Today they coincide: `MockBackend` computes travel via
`TransitGraph.best_path_cost`, and so does `GraphTravelModel`. The agreement is
exact, which is precisely why a projected schedule in the mock simulation is
not an approximation — it is what the bus will do, to the second.

Under CARLA that stops being true. Real driving times vary with traffic, so a
policy's projection becomes an estimate that can be wrong. Without the split
that divergence would be invisible: the policy would keep calling
`best_path_cost` and quietly plan against a world that no longer exists.

Keeping the two apart makes the divergence a first-class thing rather than a
silent bug, and yields a rule policies can be held to:

> A policy must never treat its own projections as fact. At every planning call
> it re-anchors on the real position, the real clock and the real passenger
> set, and recomputes forward.

## `GraphTravelModel`

The base implementation. Travel time is the shortest-path cost through
the `TransitGraph`, whose edge weights are already in seconds. Dwell is a
single constant charged once per stop **visit**, independent of how many
passengers board or alight.

```python
model = GraphTravelModel(graph)                 # dwell-free (the default)
model = GraphTravelModel(graph, dwell_s=30.0)   # 30s at every stop
```

The passenger counts are part of `dwell_time`'s signature even though the
constant model ignores them, so a passenger-proportional dwell model can be
dropped in without new plumbing. `Schedule` already derives and passes the real
counts.

## `NoisyTravelModel`

A `GraphTravelModel` whose `travel_time` is multiplied by a random factor on
every query. It exists to break the identity described above *on purpose*,
before CARLA breaks it for real.

```python
model = NoisyTravelModel(graph, sigma=0.2, seed=1)
```

Given only to the **backend**, it opens a gap between what a schedule promises
and what the bus delivers. That gap makes a failure mode reachable that cannot
occur today: a request is accepted, promised a pickup inside the service
window, and then delivered late. In the noiseless simulation the only way to
fail a passenger is to refuse them up front.

**The factor is lognormal with median 1.0** — `exp(N(0, sigma))`. Three
properties motivate that choice over a symmetric one:

- It cannot go negative, whatever the spread.
- Its median is the unperturbed time, so `sigma` reads as a spread rather than
  a slowdown: at `sigma=0.2` roughly two thirds of legs land within ±20% of
  prediction.
- It is right-skewed, so its **mean sits above 1.0**. A bus loses more time to
  congestion than it can win back by driving fast, so delay accumulates along
  a route instead of cancelling out. A mean-preserving noise model would
  flatter the policies by letting early legs pay for late ones.

The simulation turns one draw per *query* into one draw per *leg driven*:
`SimulationBackend` asks for a leg's duration when the leg begins and caches it
in `_time_to_next_stop` until the bus arrives, so a leg never re-rolls
underneath the bus.

!!! warning "Noise draws from a private generator, never the global one"
    `run_trial` seeds the module-level `random` for demand generation. If
    noise drew from it too, switching noise on would shift the demand stream
    and a noisy run could never be compared against the baseline it is meant
    to be measured against — the experiment would silently answer a different
    question. `NoisyTravelModel` therefore owns a `random.Random(seed)`.

## Wiring

`run_trial` builds one model for planning and hands it to `BusAgent`, then asks
`_execution_model` what the backend should drive against. With
`travel_noise=0.0` — the default — that returns **the very same object**, so
estimate and ground truth stay literally identical and every existing result is
reproduced bit for bit. With noise it returns a `NoisyTravelModel` instead,
while the policy and agent keep the clean one:

```python
run_trial(..., travel_noise=0.0)              # the ideal: today's behaviour
run_trial(..., travel_noise=0.2, noise_seed=1)  # degraded execution
```

`noise_seed` defaults to the trial `seed`, so a trial stays reproducible as a
whole. Passing it explicitly resamples execution luck against **fixed demand**,
which is what lets a paired comparison vary the driving without varying the
requests.

A CARLA backend is what would eventually break the identity for real; this is
the dress rehearsal.

!!! warning "Keep the policy's dwell in step with the simulation's"
    A policy constructs its own `GraphTravelModel` by default, with **zero**
    dwell. If a trial runs with non-zero `dwell_s`, pass a matching model to
    the policy explicitly, or its projections will run ahead of the bus.

## Testing with deliberately wrong estimates

Because `TravelModel` is an ABC, tests can inject a model that lies — reporting
travel times that do not match what the backend will do. That is what makes the
"estimate diverged from reality" branch of a policy reachable in phase 1, where
the real models are exact and it would otherwise be dead, untested code.
