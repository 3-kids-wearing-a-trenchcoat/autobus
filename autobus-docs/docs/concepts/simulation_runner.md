# Simulation Runner

## Overview

`SimulationRunner` is the top-level entry point for a simulation run. It owns
the tick loop, enforces a maximum simulation time, and reports results when the
run ends.

It is deliberately thin: it knows nothing about passengers, routing, or maps.
Its only dependency is `SimulationBackend`, against which it calls `step(dt)`
in a loop and checks `is_done()` to decide when to stop.

## Initialisation

The caller (e.g. `main.py`) is responsible for constructing all components and
wiring them into a `SimulationBackend` before passing it to `SimulationRunner`:

```python
pool = RequestPool()
agent = BusAgent(pool, policy, capacity=30, position=start_id, ...)
demand_gen = DemandGenerator(pool, policy=PeriodicPolicy(...), ...)
metrics = MetricsTracker(pool)
backend = MockBackend(agent, demand_gen, metrics, pool, provider)

runner = SimulationRunner(backend=backend, dt=1.0, max_time=3600.0)
kpi = runner.run()
```

## Run loop

```
run()
  │
  └─ while not is_done() and sim_time < max_time:
          backend.step(dt)
  │
  └─ snapshot KPIs → print summary → return KPISnapshot
```

`max_time` acts as a safety ceiling: if `is_done()` never becomes `True` (e.g.
the demand generator keeps adding requests indefinitely), the loop still
terminates.

## Output

After the loop, `run()` prints a summary to stdout:

```
=== Simulation Complete ===
Sim time:         3600.00 s
Fulfilled:        142
Cancelled:        3
Mean wait time:   45.30 s
Mean ride time:   120.80 s
Mean journey:     166.10 s
Fulfillment rate: 97.9 %
Throughput:       0.039 req/s
```

It also returns the `KPISnapshot` for programmatic use.

## Relationship to other components

```
main.py
  │  constructs components
  │
  ▼
SimulationRunner
  │
  │ step(dt) × N
  ▼
SimulationBackend  ──►  BusAgent, DemandGenerator, MetricsTracker
```
