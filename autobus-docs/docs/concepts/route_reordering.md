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
    is passenger patience rather than the algorithm.

## What it actually buys

Measured at 200 paired trials, `commitment=0`, W900, 200 requests
(`notebooks/reordering-l1-ablation.ipynb`). Every column is a paired
per-trial difference against L0, and **every one is significant with no
regression anywhere**:

!!! tip "Superseded on magnitude — the effect is larger than this table"

    The table below is a 200-trial ablation at a **single** window. Two
    larger campaigns have since measured the same effect with far more
    power, and both put it higher:

    - map 42, 60 paired seeds, W900: mean ride **−70 s**, p < 0.0001.
    - 25 maps, 1,000 paired (map, seed) pairs: mean ride
      **−111.5 ± 7.4 s**, p ≈ 2e-138; wait −7.3 ± 1.5 s; served
      +0.53 ± 0.17 pp. Map 42 is an easier map than typical, which is why
      the single-map figure understates the effect.

    And the advantage **grows as the window tightens** — a dependence a
    single-window ablation could not have seen. A tighter deadline
    constrains the schedule harder, which is exactly when repairing its
    order pays:

    | window | 450 | 600 | 750 | 900 | 1200 | 1800 |
    |---|---|---|---|---|---|---|
    | ride, L1 − L0 | **−229 s** | −155 s | −91 s | −70 s | −31 s | −19 s |

    Quote **−111.5 s** for a general claim about the policy and **−70 s**
    for one about the campus map. What is superseded here is the
    *magnitude*; the direction, the significance and the mechanism below
    all stand.

!!! danger "`stop_density=0.5` is required to reproduce any of this"

    The "campus map" is
    `PlanarStreetGraph.generate(n_seeds=17, bounds=(0, 0, 2000, 2000),
    seed=42, stop_density=0.5)` — **21 stops**. The generator's *default*
    density is 0.15, which on the same seed gives **6 stops**, and on a
    6-stop map everything below collapses: the bus keeps up with any demand,
    fixed route serves 100%, and there is almost nothing left to reorder.

    This cost real time on 2026-08-10. A campaign was run at the default
    density and concluded that L1 was not worth its cost — the conclusion was
    an artefact of an over-easy map, and it was wrongly attributed to
    `commitment` before the density was checked.

    Re-verified on the correct map at `commitment=0`, 30 paired seeds:
    fixed route 68.9% served / 448 s wait / 656 s ride against the 68.6% /
    451 / 654 recorded here, and **L1 − L0 mean ride −75.6 s at p < 0.0001**
    against the −63.7 s below. **The figures in this section stand.**

| | L0 | L1 | delta |
|---|---|---|---|
| Served fraction | 93.3 % | 94.0 % | **+0.8 ± 0.3 pp** |
| Mean wait | 465.7 s | 456.4 s | **−9.3 ± 3.2 s** |
| P90 wait | 849.6 s | 840.2 s | **−9.3 ± 2.8 s** |
| Mean ride | 523.3 s | 459.6 s | **−63.7 ± 6.9 s** (−12 %) |
| Max ride | 2617 s | 2326 s | **−291 ± 108 s** (−11 %) |

Max ride was the risk worth watching, since the cost function is a *total*
and indifferent to how it is distributed — a large search space could have
shaved the sum by lengthening one rider's journey. It moved the other way.

### It works through ride quality, not through slack

The obvious theory before running this was that reordering frees capacity,
which lets more requests in, which shortens waits. **That is not what
happens.** The refusal rate barely moves:

| | L0 | L1 |
|---|---|---|
| Share of waiting requests left unscheduled | 11.2 % | 10.6 % |
| First-pass insertion rejections | 47.6 % | 46.5 % |

A 2.3% relative drop in refusals against a 12% improvement in ride time. The
benefit is overwhelmingly a better visit order for the riders **already
aboard** — which is exactly the pathology the direction was proposed to
attack, before the argument drifted toward the slack story.

### The cost function is measuring the right thing

The policy reported 78 s of internal cost improvement per fulfilled
passenger. The measured improvement in wait plus ride is 73 s per passenger —
agreement within 6%. Whatever `_cost` saves shows up in the KPIs rather than
leaking into quantities nobody reports.

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

### The retry step (off by default)

`retry_rejected` re-offers the requests refused earlier in the same call
against the repaired schedule, on the theory that reordering frees slack worth
collecting immediately rather than one arrival later.

**It was measured and it does not pay.** Retry admitted 263 of 31,074 attempts
(0.9%), moved no KPI by a measurable amount — mean ride −63.7 s with it and
−63.7 s without — and cost about 13% more planning time. That follows from the
finding above: reordering improves the order for riders already aboard rather
than freeing room for new ones, so there is very little for a retry to pick
up.

The flag is kept because it isolates re-admission from pure reordering, which
is worth being able to measure again on a different map or at a different
service window.

## Tuning

| Parameter | Default | Effect |
|---|---|---|
| `max_relocations` | `None` (all) | Requests to try relocating per pass. `0` reproduces `WindowInsertionPolicy` exactly. |
| `n_passes` | `1` | Repair sweeps per call. `None` sweeps to a fixed point; see below. |
| `retry_rejected` | `False` | Whether freed slack is used within the same call. Measured not to pay; see above. |

### Sweeping to convergence

Each accepted relocation changes the schedule the next candidate is judged
against, so one sweep already compounds — but it need not exhaust the
neighbourhood. `n_passes=None` repeats until a sweep accepts nothing, leaving
the schedule at a **local optimum for the relocate operators**: no single
request can be moved anywhere cheaper, and improving further would require a
move that displaces several at once, which is what L2 is for.

Termination is guaranteed rather than hoped for — every acceptance strictly
lowers a cost bounded below — so the `_MAX_CONVERGE_PASSES` ceiling only bounds
the damage if a future operator ever breaks that invariant.

The fixed-point test is an identity check: `_repair` hands back the very object
it was given unless it accepted something, so "nothing moved" needs no
schedule comparison.

`repair_passes / repair_calls` is the readout that answers whether one sweep
was enough. Note the floor is **1**, not 2: a call whose first sweep accepts
nothing has converged already and stops there, so this figure really reports
what fraction of calls found anything to do.

!!! success "One pass is all there is — measured, 300 paired trials"
    Neither `n_passes=2` nor sweeping to convergence improves any KPI over a
    single pass at W750: served p=0.91, mean wait p=0.57, mean ride p=0.69,
    max ride p=0.44. The intervals are tight enough for the null to carry
    weight — any gain from converging is under about 3 s of mean ride, against
    the **92 s** L1 itself buys over L0.

    The counters say why. The second sweep roughly doubles relocation attempts
    (788,611 → 1,572,866) and buys **673 extra acceptances** — an acceptance
    rate of **0.086%**, against 3.2% for the first sweep. Convergence averages
    **1.26 passes per call**, so about three quarters of repair calls relocate
    nothing at all.

    So the relocate neighbourhood is genuinely exhausted after one sweep, and
    the ceiling is a property of the operators rather than of the budget.
    Improving further needs a move that displaces several requests at once —
    L2. `n_passes=1` stays the default; `None` remains available for
    re-testing on another map or window.

    One curiosity, not a defect: total `cost_improvement` *falls* as passes
    increase (6.62M → 6.57M → 6.56M s). A better-maintained schedule leaves
    each later plan call less to improve, so the accumulated total shrinks
    while the schedules themselves are equal or better.
    See `notebooks/reordering-n-passes.ipynb`.

## Reordering under unreliable driving

Everything above is measured in a simulation where the bus drives exactly as
predicted. `notebooks/execution-noise.ipynb` breaks that, handing the backend
a [`NoisyTravelModel`](travel_model.md) while the policy keeps planning
against the clean graph.

The prediction going in was that **L1 would be the more fragile of the two**:
repair works by tightening the schedule, and the slack it removes is also the
buffer that absorbs execution error. That was refuted. L1's advantage *grows*
with noise, monotonically:

| σ | 0.0 | 0.1 | 0.2 | 0.3 |
|---|---|---|---|---|
| Mean ride, L1 − L0 | −92 s | −108 s | −124 s | **−136 s** |
| Max ride, L1 − L0 | −250 s | −358 s | −492 s | −416 s |

All at p<0.0001. Seen as two columns rather than a difference it is starker:
L0's mean ride degrades 596 → 607 → 629 → **653 s**, while L1's moves only
505 → 499 → 505 → **517 s**. **L0 degrades roughly five times faster.**

L1 does not buy this by breaking more promises — the breach-rate difference is
non-significant at every noise level (+0.05, +0.12, +0.03, +0.20 pp).

The likely mechanism, which has **not** been measured directly: L1 re-sorts
the remaining schedule against the *current* clock on every planning call,
while L0 keeps the order that arrival sequence happened to produce. That makes
reordering an adaptive advantage precisely when the world departs from the
plan — which would mean L1 is worth *more* under CARLA than the noiseless
figures suggest, not less.

L1 costs **3.4×** a plain insertion trial (3.9× with retry enabled), matching
the ~3.7× predicted from the headroom measurements.

!!! note "Most of that cost is wasted, and that is the obvious thing to fix"
    550,202 relocation attempts produced 13,905 accepted moves — a **2.5%
    acceptance rate** (3.8% for pairs, 1.3% for dropoffs). Nearly all of the
    3.4× goes into evaluating moves that turn out not to be improvements.
    Predicting which requests are worth relocating would recover most of the
    benefit for a fraction of the work, and is a better next step than a more
    expensive neighbourhood search.

## Why a subclass

`WindowInsertionPolicy` is left completely untouched, so it remains a clean L0
control: any difference between the two columns in a comparison comes from
reordering and nothing else. This also matches how the other policy variants
here are built — `SkipStopFixedRoutePolicy` extends `FixedRoutePolicy`,
`DeviatedFixedRoutePolicy` extends that, and `RideBoundedGreedyPolicy` extends
`NearestUnservedPolicy`.

## Instrumentation

`stats()` returns counters — repair calls and the sweeps they ran, relocations
attempted and accepted per operator, cumulative cost improvement, and retry
attempts and admissions.

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
improve. `repair_passes` counts the sweeps those calls ran, so the ratio of
the two is the mean number of passes a repair took.
