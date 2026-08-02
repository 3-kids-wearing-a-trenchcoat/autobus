# Route Reordering

## Overview

`ReorderingInsertionPolicy` extends [Window Insertion](window_insertion.md)
with the step that policy has always been missing: it re-optimises the order it
already has, instead of only ever growing it.

`WindowInsertionPolicy` *constructs* a schedule. Once a request's pickup and
dropoff are spliced in at positions *(i, j)*, their position relative to every
other event is frozen for the schedule's lifetime — `Schedule` offers `insert`,
`without_owner` and `without_pickup`, but nothing that **moves** an event.
Since sequential cheapest insertion is order-dependent, the surviving plan is
partly a fossil of the order requests happened to arrive in.

Two further mechanisms keep degrading it after construction:

- `_adopt_dropoff` splices in riders the policy never planned for, chosen for
  cheapness rather than fit.
- Reconciliation removes departed riders but leaves behind the detours that
  existed to serve them.

## The reordering ladder

Re-optimisation is a ladder rather than a single technique. `ReorderingInsertionPolicy`
implements **L1**, the cheapest rung that does real work.

| Level | Approach | Status |
|---|---|---|
| L0 | Insert only; order frozen once placed | `WindowInsertionPolicy` |
| **L1** | **Relocate one request at a time, keep improvements** | **implemented** |
| L2 | Ruin & recreate: eject a related subset, rebuild it | future |
| L3 | Discard the order entirely each epoch and rebuild | future |

## Is there anything to reorder?

Measured before building anything, because a repair pass over a schedule
holding two requests would have nothing to do
(`notebooks/reordering-headroom.ipynb`, campus map, 20 trials):

| | W900 / 200 req | No window | W900 / 500 req |
|---|---|---|---|
| Median requests in schedule | 13 | 11 | 14 |
| P90 requests | 18 | 14 | 18 |
| Plans with ≥ 4 requests | 95.5 % | 95.9 % | 98.2 % |
| Insertion attempts rejected | 54.1 % | 0.0 % | 55.5 % |

Three things follow. The search space is real and present on essentially every
planning call. Schedule length is **window-bound, not demand-bound** — 2.5×
the demand moves the median by one. And the rejections are caused by the
deadline alone: the no-window control rejects nothing, so neither capacity nor
travel geometry is what binds.

!!! note "Expect wait and ride gains, not coverage"
    A trial rejects ~189 insertion attempts but loses only ~17 requests, so at
    most 9% of rejections are terminal — the rest are requests that got in
    later, having waited longer than they needed to. Widening the abandonment
    threshold to 1350 s reaches 99.3% served, which shows the ceiling at W900
    is passenger patience rather than the algorithm. Reordering should be
    judged on **wait and ride time**, with rejection count as the direct
    mechanistic readout.

## How L1 works

Each planning call runs the parent's reconcile-and-insert unchanged, then:

```
repair pass  →  relocate each request once, keeping strict improvements
retry        →  offer the still-unscheduled requests another chance
```

### Two operators

A schedule holds two kinds of member, so relocation needs two moves:

- **relocate-pair** — a request still waiting has both events scheduled and
  moves as a unit. Implemented as `without_owner` followed by the parent's
  `_best_insertion`, so a relocation is judged by exactly the same feasibility
  and capacity rules as an original insertion.
- **relocate-dropoff** — a rider already aboard has only a dropoff left, and
  moves as a single event.

Neither adds anything to `Schedule`; both compose from methods that already
existed. On the campus map a median schedule of 13 requests splits roughly 6
pairs to 7 dropoffs, so neither operator is a rounding error.

!!! warning "Relocate-dropoff cannot reuse `_adopt_dropoff`"
    `_adopt_dropoff` tests nothing. That is correct for a *forced* adoption —
    the rider is already aboard and must be delivered whatever it costs — but
    wrong for an optional move. Shifting a dropoff later raises the load
    carried in between, and shifting it in either direction moves every
    arrival after it. Relocate-dropoff therefore runs its own `capacity_ok`
    and feasibility scan.

### Acceptance rules

- **Strictly cheaper, or no move.** Ejecting and re-placing a request usually
  reproduces the schedule it came from; accepting ties would churn for nothing.
- **Never lose a rider.** If an ejected request cannot be placed back, the
  original schedule is kept. This is the guarantee that outranks any cost
  saving: `WindowInsertionPolicy` promises that no *accepted* request is
  abandoned, and a repair pass must not quietly break that promise to save a
  few seconds.

### The retry step

Reordering frees slack, and freed slack is only worth having if something uses
it. With `retry_rejected` (the default), requests refused earlier in the same
call are offered again against the repaired schedule.

Without it the gain is deferred rather than lost — a rejected request stays
`PENDING` and is retried on the next arrival anyway — but taking it here
shortens the very wait it was refused over. The flag exists so that pure
reordering can be measured separately from re-admission.

## Tuning

| Parameter | Default | Effect |
|---|---|---|
| `max_relocations` | `None` (all) | Requests to try relocating per pass. `0` reproduces `WindowInsertionPolicy` exactly. |
| `n_passes` | `1` | Repair sweeps per call. Converges quickly, since each sweep only accepts strict improvements. |
| `retry_rejected` | `True` | Whether freed slack is used within the same call. |

Cost is not expected to be the binding concern. Planning runs well under 7 ms
per call, and L1 prices at roughly 3.7× the insertion work it adds to — only
about 6 of a median 13 requests carry a relocatable pair, the rest being
single-event dropoffs.

## Why a subclass

`WindowInsertionPolicy` is left completely untouched, so it remains a clean L0
control: any difference between the two columns in a comparison comes from
reordering and nothing else. This also matches how the other policy variants
here are built — `SkipStopFixedRoutePolicy` extends `FixedRoutePolicy`,
`DeviatedFixedRoutePolicy` extends that, and `RideBoundedGreedyPolicy` extends
`NearestUnservedPolicy`.

## Instrumentation

`stats()` returns counters — relocations attempted and accepted per operator,
cumulative cost improvement, and retry attempts and admissions.

They are reported on the policy rather than accumulated in a module-level
global, so they travel with the instance. That matters for more than tidiness:
a global would silently return zeros the moment trials are run in separate
processes.

```python
policy = ReorderingInsertionPolicy(graph, max_wait_s=900.0)
...
policy.stats()
# {'repair_calls': 193.0, 'pair_attempts': 1160.0, 'pair_accepted': 88.0, ...}
```

`repair_calls` counts planning calls that reached the repair stage, which is
fewer than the total: a call whose schedule came out empty has no order to
improve.
