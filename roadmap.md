# Autobus roadmap

Living document. Phases 1 and 2 are complete. **Phase 3 steps 3.1–3.6 are
done**, including the campaign harness and the results store, all exercised
end to end against a live server. **3.7 is substantively satisfied**: §3.2.2
passes on CARLA over 8 seeds on Town01 with 20 NPCs (fulfilment 100%, sum of
percentage differences +145% Window / +138% Reordering), and the statistical
evidence lives in the synthetic campaign as the 2026-08-10 split decided.
What remains of 3.7 is writing it up.

The evidence base is now four campaigns, all in `results/runs` and all
reproducible: the synthetic campaign (2,280 runs), the map-seed sweep
(7,000), the load sweep (4,950) and the safety-factor sweep (3,840).

**§1.1.2 plan-call latency was the last unverified stated requirement, and
it is now measured and satisfied** (2026-08-11, on two machines). The one
requirement still not met is mid-route replanning (functional requirement
3), which was deliberately dropped — see Open questions.
Last updated 2026-08-11.

---

## Phase 1 — Proof of concept, no CARLA

1. [X] Proof of Concept phase
   1. [X] Implement `PlanarStreetGraph` which generates a random planar street graph to be used in place of Carla
   2. [X] Implement `MapProvider` and its subclass (only one for now)
      1. [X] Implement `MapProvider` as an abstract class that provides an API that separates between the source of the 
      map (`PlanarStreetGraph` or Carla) and whatever method or object wants to use it to generate the graph that will be used by the algorithm
      2. [X] Implement `PlanarMapProvider` as a subclass of `MapProvider` that draws from `PlanarStreetGraph`
   3. [X] Implement `TransitGraph`, the map graph that will be used by the bus algorithm as part of its run.
      1. [X] `TransitGraph`'s constructor should create an edge-node directed graph from `MapProvider` data.
      2. [X] `TransitGraph` methods (to be determined)
   4. [X] Implement `RequestPool` which stores active passenger requests waiting to be handled.
   5. [X] Implement `DemandGenerator` which generates passenger requests in real time.
   6. [X] Implement `DispatchPolicy` base class and placeholder subclass
   7. [X] Implement `BusAgent`
   8. [X] Implement `SimulationBackend` which handles the simulation, this is probably an abstract library
   9. [X] Implement `MockBackend` which inherits from `SimulationBackend`
   10. [X] Implement `MetricsTracker`, self-explanatory
   11. [X] Implement `SimulationRunner` which initializes everything, runs the tick loops, collects metrics and prints/saves results.
   12. [X] Implement fixed-route `DispatchPolicy`
   13. [X] Implement at least one heuristic-based `DispatchPolicy`

Delivered beyond the original list: ten dispatch policies including
`WindowInsertionPolicy` (service-window insertion) and
`ReorderingInsertionPolicy` (L1 relocate-repair), a paired-statistics
experiment harness, and the concept documentation under `autobus-docs/`.

---

## Phase 2 — Restore backend modularity (**complete**)

**Why.** The design review specifies `BusAgent → SimulationBackend`, with
`SimulationRunner` orchestrating and the backend as an "abstract execution
engine". The implementation inverted this: `SimulationBackend` owns the tick
order, demand generation, abandonment, metrics and animation, and *calls into*
`BusAgent`. Its only abstract method is `_travel_time(from, to) -> float`,
which encodes "the duration of a leg is known before departure" — a question
CARLA cannot answer. A CARLA backend is not implementable against the current
seam.

**Goal.** The project does not care whether the world is synthetic, CARLA, or
anything else.

1. [X] **Freeze current behaviour with a golden master**
   - `notebooks/golden-master.ipynb` + `notebooks/golden_master.py`
   - 11 scenarios × 3 seeds; full KPIs, `wait_times`, `ride_times`, tick
     counts and arrival sequences. Every step below was checked by re-running
     it and demanding **bit-identical** output. All of them passed.
2. [X] **Settle the adapter contract**
   - Command: `set_destination(stop_id)` — abstract, and **idempotent**
   - Observation: `advance(dt) -> AdvanceResult(arrived_at, consumed)` —
     abstract
   - Position: `current_location()` — abstract
   - Lifecycle: `start()` / `close()` — no-op defaults
   - Passengers: `sync_passengers(pool)` — no-op default
   - **`now()` was dropped from the sketched contract.** The runner owns
     `sim_time` and derives arrival stamps from `consumed`; a second clock in
     the backend would only be something to drift. It also made the arrival
     arithmetic bit-identical, since `sim_time - remaining` is the same
     subtraction the old code performed.
3. [X] **Move orchestration out of the backend into `SimulationRunner`**
   - The runner now owns the clock, the tick order, `_expire_pending`,
     `is_done`, the `MetricsTracker` and the recorder, and mediates between
     agent and backend.
   - `BusAgent` needed **no changes**, as expected.
   - **Preserved the tick order exactly, including its wart.** Demand is
     still generated *before* movement, so a request created this tick can be
     boarded at an `arrival_time` computed to fall slightly earlier — the
     source of the small negative wait times (0.05% of requests, worst
     −0.49 s, bounded by `dt`). Documented in `simulation_runner.md`. Fix it
     separately, if at all.
   - **Done as a direct switch, not strangler-style.** The old `step()` could
     not coexist with the new runner — its existence *was* the coupling under
     repair, and keeping it alive would have required the backend to retain
     its agent reference. The golden master provided the safety that
     strangler was meant to buy.
4. [X] **Reduce `MockBackend` to a pure adapter**
   - Dead reckoning only; it tracks its own position rather than reading the
     agent's.
   - Construction-order trap gone: leg durations are computed lazily on the
     first `advance` toward a destination, so constructing a backend does no
     movement arithmetic.
5. [X] **Add the hooks CARLA needs, as no-ops**
   - `start()` / `close()` / `sync_passengers(pool)`, with `close()` called
     from a `finally` block so a failed trial still releases its actors.
6. [X] **Un-hardwire the experiment harness**
   - `run_trial(backend_factory=...)`, mirroring `policy_factory`; takes the
     `TransitGraph` and the starting stop ID.
   - The default is a closure over the trial's own execution model, which is
     what preserves object identity at `travel_noise=0.0`.
   - `backend._transit_graph` reach-through removed.
7. [X] **Delete the old seam** — `_travel_time`, `step()`, and the
   orchestration remnants are gone.
8. [X] **Prove modularity with a scripted backend**
   - `src/simulation/ScriptedBackend.py`: no graph, no travel model, no
     geometry, just a cycling timetable of leg durations.
   - `tests/test_trial.py` runs a full `run_trial` against it and asserts
     passengers are served. The project demonstrably does not care where the
     world comes from.
   - The runner's own tests drive it too, so they exercise a real
     implementation of the contract rather than a mock.

**Outcome.** 292 → 356 tests; abandonment gained its first direct unit tests.
Golden master bit-identical throughout. Adding `CarlaBackend` is now a matter
of writing one class against three abstract methods.

### Deliberately out of scope for Phase 2

Returning to the design review where it buys no modularity:

- **Not restoring `RoutePlanner`** — no job for it while a stop sequence *is*
  the route. Revisit if CARLA path resolution needs a home.
- **Not moving `BusAgent` to the simulation package** — it imports nothing
  from `simulation`, so the layering is already clean. The problem is the
  arrow direction, not the directory, and a move costs import churn across
  tests and notebooks for no functional gain.
- **Not switching `RequestPool → DispatchPolicy` to push** — the pull/snapshot
  model works and is arguably better.
- **Not solving mid-leg position ownership** — only bites once we re-plan
  while driving. The contract admits it via `current_location()`; the mock
  answers trivially and CARLA can defer.

### Deliberate deviation from the design review

The design has `BusAgent → SimulationBackend` (the agent commands the
backend). Doing that literally would give `BusAgent` a backend reference,
making `src/algorithm` depend on `src/simulation` and breaking Expected
Outcome #3 ("the routing algorithm is fully independent of the CARLA API").

**Instead the runner mediates**: it reads `agent.next_stop()`, commands
`backend.set_destination(...)`, observes the arrival, and calls
`agent.on_arrival(...)`. Same decoupling, better layering, and `BusAgent`
needs no changes at all.

---

## Phase 3 — CARLA integration

Sized against the design review's §5.2 ("Phase 2 should be complete by early
August") and the 31 August deadline. Sequence so a crude end-to-end run exists
as early as possible; treat everything after Phase 3.4 as optional depth.

1. [X] **Spike: throughput and capability** — measured 2026-08-03

   **Throughput** at `fixed_delta_seconds=0.05`, Town10HD_Opt, 300 ticks:

   | | 0 NPCs | 20 NPCs | 50 NPCs |
   |---|---|---|---|
   | rendering | 2.30× real | 2.26× | 1.98× |
   | no-rendering | 50.1× | **13.4×** | 6.1× |

   Rendering costs ~6×, and is the single biggest lever. **Town choice is
   irrelevant to speed** (Town01 11.0×, Town02 11.8×, Town04 11.4×,
   Town10HD_Opt 13.0×) — choose a town on authoring effort, not performance.

   **AI degradation is caused by the delta, not by wall speed.** Net
   displacement of 30 autopilot vehicles over 60 simulated seconds:
   112 m at delta 0.025/0.05/0.1, but **51 m at 0.5** — and CARLA warns once
   `fixed_delta_seconds > max_substep_delta_time * max_substeps` (0.01 × 10 =
   0.1). At a *fixed* delta of 0.05, rendering on (2×) and off (13×) gave
   121 m and 113 m — indistinguishable. So `no_rendering_mode` is free:
   in synchronous mode the world advances by exactly `delta` per tick no
   matter how long the tick took. **Keep delta at 0.05; never above 0.1.**

   **Sizing.** A 40-request trial (~2000–4000 sim-s) is 2.5–5 wall-minutes at
   13×, so a 30-trial campaign is under 2.5 hours and 100 trials is an
   overnight run. The design review's §3.2 statistical bar is therefore
   *affordable* on CARLA — the conflict with §5.1 need not be resolved by
   giving up. With rendering on for a demo the same trial is 17–33 minutes.

   **Bus blueprint: `vehicle.mitsubishi.fusorosa`** (`base_type=Bus`).
   *Superseded — the default is now `vehicle.volkswagen.t2_2021`, and the
   full-size bus is unsupported on Town01. See 3.4.*

   **Steering: `traffic_manager.set_path(actor, [locations])` works**, and
   keeps CARLA's built-in driving in charge as design review §4 intended. One
   bus completed a six-leg tour of Town01 with the path re-issued per leg;
   3/6 legs landed within 6 m, two of the misses stopping 32–54 m short.
   Expected to improve now that targets are authored stops rather than
   arbitrary spawn points. **Decision: use `set_path`; revisit only if it
   stays unreliable.**

   *Superseded: it did stay unreliable, and for a structural reason. A bare
   `set_path` cannot express a route — see the trap on it below. Steering is
   now `set_route`, given turns from `src/simulation/carla_routing.py`.*

   **The `agents` package** is confirmed absent from the wheel and present in
   the image at `/workspace/PythonAPI/carla/agents` (2262 LOC).

   *Two corrections, on measurement rather than reading: `shapely` is imported
   by `basic_agent.py` alone and by nothing in the route-planning chain, and
   although `agents/requirements.txt` does pin `numpy<2.0.0`, that pin is
   over-broad — `GlobalRoutePlanner` was run successfully on our numpy 2.5.1.
   Neither was the obstacle it appeared to be. We wrote our own planner
   anyway; see the decisions table.*
2. [X] **`CarlaMapProvider`** — built 2026-08-04

   Reads two files from `src/map/carla_maps/` and **imports no carla**, so a
   CARLA town is routable, testable and experimentable with no server:
   `<Town>.topology.json` (machine-dumped by `src.map.carla_dump`) and
   `<Town>.stops.json` (hand-authored). Separate on purpose — re-dumping
   roads must never destroy stop authoring.

   `run_trial` gained a `provider` parameter, so a trial runs on Town01 with
   the mock backend and no CARLA at runtime. Measured: 40/40 served, L1 at
   232 s mean wait / 172 s mean ride against fixed-route's 301 s / 325 s.

   **Three silent-failure traps, each now covered by a mutation-tested
   regression test.** They are properties of `TransitGraph._build_map` and
   will bite anyone writing another provider:

   1. **A TransitGraph node *is* a road edge and holds one stop.**
      `stops_by_edge = {stop.edge: stop for stop in stops}` overwrites
      silently. Four of Town01's fifteen stops sit in pairs on a single
      topology segment, so the provider **splits segments at stops**, giving
      each its own edge.
   2. **A stop belongs to the edge that *begins* at its split node.** Edge
      `A → B` is weighted `length(A)`, so arriving at `B` means arriving at
      the *start* of `B`. Attaching a stop to the edge that ends at its node
      is the intuitive reading and is wrong by one edge length on every
      estimate.
   3. **`RoadEdgeRecord` equality is exact and `metadata` must be `None`.**
      A dict makes the record unhashable and `_build_map` raises; a float
      differing in its last bit detaches the stop. The provider shares the
      *same object* between `get_stops` and `get_road_edges`.

   **Two CARLA facts worth keeping.** `get_topology()` returns **adjacency
   pairs**, not segments: the exit waypoint is the start of the *following*
   road, so its `s` belongs to that road (the dumper interpolates the
   terminus instead), and one physical stretch appears once per downstream
   branch (the dumper deduplicates — Town01 drops 160 → 124).

   Town01: 124 segments, 6.4 km, 103 nodes, 139 edges, all 15 stops
   surviving, one strongly connected component, median leg 72 s.
   `provider.validate()` returns empty.
3. [X] **Bus stops** — authored 2026-08-03, pending review

   Stops are an **input** to the map layer, never derived from it: in a real
   deployment you name the map and its stops rather than let a program guess.
   Hand-authored, version-controlled, one file per town under
   `src/map/carla_maps/<Town>.stops.json`.

   **Addressed by OpenDRIVE identity** — `road_id`, `lane_id`, `s` — which
   `Map.get_waypoint_xodr` resolves back exactly (worst drift measured on
   Town01: **0.0006 m**). World coordinates are cached alongside for offline
   plotting and as a load-time check that the file matches the town. The sign
   of `lane_id` carries direction, so a stop serves one direction of its
   road — consistent with `TransitGraph` nodes being directed road edges.

   `src/map/carla_stops.py` holds the schema and IO and **imports no carla**,
   so the provider and its tests can read stops with no server running.
   `notebooks/carla-stop-authoring.ipynb` is the aid: it proposes sites,
   plots them, draws them into the world for inspection, and writes the file.

   **A legal site** is on a Driving lane, off-junction, ≥10 m clear of both
   segment ends, with a Shoulder immediately to the right (CARLA's way of
   saying a bus can pull over), on a segment ≥24 m long. Town01 yields 3318
   such sites across 44 road-lanes.

   **Trap, already paid for once:** the junction test belongs on the *stop
   position*, not on the segment endpoints. `get_topology()` segments run
   junction to junction, so their endpoints are junction waypoints by
   construction — testing them discarded 144 of Town01's 160 segments and
   left stops only on the four perimeter roads.

   Town01 draft: 15 stops, 11 road-lanes, min spacing 92 m, median
   stop-to-stop road distance 555 m (~93 s at 6 m/s, against the campus
   map's ~130 s). All 15 mutually reachable.
4. [X] **The bus, no passengers** — the walking skeleton, **complete**
   - Spawn, drive to a stop, detect arrival (proximity + near-zero speed,
     never exact waypoint equality), stop, continue.
   - **`CarlaBackend` is now a single class against three abstract methods**
     (Phase 2 delivered the seam):
     - `start()` — connect, set synchronous mode and `fixed_delta_seconds`,
       spawn the bus at `position`.
     - `set_destination(stop_id)` — hand the target to the controller.
       Must be idempotent: the runner re-issues it every loop pass.
     - `advance(dt)` — tick `round(dt / fixed_delta)` times, checking
       proximity after each; return on the first arrival with
       `consumed = ticks_done * fixed_delta`, else consume all of `dt`.
     - `current_location()` — the last stop reached.
     - `close()` — destroy actors; the runner calls it from `finally`.
   - Develop it against `ScriptedBackend` semantics first: anything that
     works there is the runner's problem, not CARLA's.

   **Built 2026-08-04.** `src/simulation/CarlaBackend.py`, one class against
   the three abstract methods; nothing above it changed. A full `run_trial`
   on Town01 fulfilled **6/6 requests**, 11 arrivals, 5.33 km driven, at 36x
   real time.

   **The bus blueprint — re-measured 2026-08-05, and the earlier verdict was
   wrong.** This entry used to record that `vehicle.mitsubishi.fusorosa`
   "stalled on 85% of ticks and completed 0/3 legs" and therefore could not
   drive Town01. That was measured with a stale traffic manager — see the
   trap below — and does not survive re-measurement. Over 5 fixed six-stop
   tours (25 legs per blueprint, headless, seed 0, repaired traffic
   manager):

   | blueprint | legs reached | lifts | re-plans | per tour |
   |---|---|---|---|---|
   | `vehicle.mercedes.sprinter` | 20/25 (80%) | 23 | 132 | 5,2,5,5,3 |
   | `vehicle.volkswagen.t2_2021` | 19/25 (76%) | 21 | 127 | 5,2,5,5,2 |
   | `vehicle.mitsubishi.fusorosa` | 17/25 (68%) | 33 | 182 | 5,2,4,4,2 |

   **Read that table with its conditions attached**, because both have since
   changed: it was taken at a 200 s leg cap, which is too short for Town01
   (legs run to 399 s), and with the lift-onto-the-lane recovery still
   enabled. The "legs reached" column therefore measures how *quickly* a
   blueprint gets round, not whether it can. Re-measured properly, the
   default bus completes 15 of 15 legs and the full-size bus needs 20 lifts
   to do the same — and since those lifts have been removed, **the full-size
   bus is no longer supported on Town01**.

   The full-size bus **does** get round Town01. It is never better than the
   two minibuses and worse on 3 of the 5 tours, but 25 legs cannot separate
   68% from 80% — that is a direction, not a result. Its 3.9 m width against
   3.5 m lanes remains a real fact; "cannot drive the town at all" does not.

   Default is `vehicle.volkswagen.t2_2021`, a shuttle that looks the part
   and is indistinguishable from the Sprinter here. `FULL_SIZE_BUS_ID` stays
   available and is no longer disqualified.

   **One tour fails for every blueprint** (2/5 legs for all three), so the
   legs that still fail are a property of particular stops, not of the
   vehicle.

   That table was measured before the red-light fix below, while unwedges
   were still teleporting the bus 6 m forward and occasionally past a stop
   it could not otherwise reach. Re-run afterwards the ranking is unchanged:
   `fusorosa` 17/25 exactly as before, `t2_2021` 18/25 rather than 19 —
   one leg it had only reached by being teleported.

   **Stall recovery was removed on 2026-08-05, having turned out to be
   scaffolding.** It was introduced to lift a wedged bus back onto the lane,
   on the strength of a run where the bus jammed and then reported zero
   movement for its ten remaining legs. Every part of that justification has
   since dissolved:

   - Almost all of its firings were **false positives at red lights** — 20
     of 20 lifts, and 121 of 124 re-plans, on a bus standing at a red on a
     proper driving lane. See the trap below.
   - The stalls that remained were the **stale traffic manager** steering by
     another town's road graph, and the pathological **`set_path` loop**.
   - With those fixed, three tours of the default bus over 10 km complete
     15 of 15 legs with **zero lifts and zero re-plans**. Removing the code
     left the driven distances identical to the metre.

   What remains is `_track_stall` calling `_repath`, which re-plans the
   route from where the bus actually is and moves nothing. It never fires
   with the default vehicle, which is exactly why it is worth keeping: the
   counter in `stats()` is how a run reports that it needed help, and it
   cannot corrupt a KPI the way a teleport can.

   **The full-size `fusorosa` is not supported on Town01 as a result.** It
   does get round the town, but only on the strength of the recovery that is
   now gone: over the same three tours it needed 20 lifts and 61 re-plans,
   and `plan_route` could not find a way out of five of the positions it
   wedged itself into. `FULL_SIZE_BUS_ID` is kept named in the source
   because "why not the actual bus?" is the first question anyone asks.

   **There is no remaining reachability gap. Every stop is reachable, and
   the earlier "4 of 25 legs fail" was a measurement artefact** — the tour
   harness capped each leg at 200 s, and three of the four failures needed
   201, 210 and 267 s. Re-run at a 600 s cap, tours 2, 4 and 5 complete
   **15 of 15 legs**, including the one previously written off as a near
   miss at 14.9 m, which arrives at 5.5 m in 201 s.

   **Town01 legs are simply long.** Measured span **45–399 s**, median
   around 160 s, on a town whose 36 traffic lights cycle in groups of three
   and hold an approach for about 28 s. Anything that bounds a leg — a
   timeout, `max_time`, `max_wait_s` — has to be sized against 400 s, not
   against the mock's tens of seconds. A short leg timeout would not protect
   a trial; it would manufacture the failures it was meant to catch. This
   retires the earlier claim that a missing per-leg timeout is a critical
   bug: legs finish, they just take minutes.

   Three diagnoses died on the way to this, all of them mine, and the same
   lesson each time — suspect the harness before the world:

   1. "Specific stops are unreachable." No: every one of the 210 ordered
      stop pairs routes, and all 15 stops are reached in tours.
   2. "The failures are cascades." No: three of the four began from a
      properly reached stop.
   3. "Spawning at a stop wedges the bus." No: that was traffic-manager
      contention with a Jupyter kernel. Re-run clean, all 15 stops are
      driveable away from, 46–195 m in 25 s.
5. [X] **Passengers as walkers** — built 2026-08-04

   `sync_passengers` diffs the pool each tick: a walker appears when a
   request starts waiting and is destroyed when it stops — boarded *or*
   abandoned. The pool stays authoritative and nothing feeds back, so a
   failed spawn costs a visual and never a result. Walkers get no AI
   controller, so they stand still, which is what waiting for a bus looks
   like.

   Measured trial on Town01: **10/10 requests fulfilled**, 10 walkers
   spawned and 10 removed, **zero spawn failures**, 8.25 km driven, 36x
   real time.

   **Walkers must not stand at the stop coordinate.** Stops are lane-centre
   positions; a pedestrian there makes the traffic manager brake, and a
   walker with no controller will not move aside — which would hand back the
   stall problem step 4 had to solve. They are placed on the **shoulder**
   instead, which the stop authoring already guaranteed exists at every stop
   (it was the pull-over test). Confirmed at run time: 0 stops without a
   shoulder.

   The shoulder centre alone is not enough. Measured on Town01 it sits
   2.15 m from a 4.0 m lane's centre — clearing the lane edge by **0.15 m**,
   less than a walker's own body. `WALKER_LATERAL_OFFSET_M` pushes them a
   further metre right, giving 1.15 m of clearance.

   Several passengers at one stop are fanned out along the shoulder, since
   two walkers at the same point simply fail to spawn.
6. [X] **Campaign harness** — complete 2026-08-10

   Original two bullets, both still the point:

   - Trial lifecycle with a world reload between trials — actor leakage is the
     classic cross-trial contaminant.
   - Per-trial result persistence so a stopped or crashed campaign resumes.
     The *seam* belongs here even if the storage is crude; retrofitting it
     means re-running everything already collected.

   **Broken down 2026-08-10, after reading the harness rather than the
   roadmap.** *One* CARLA trial is a solved problem, because Phase 2 left
   `run_trial` fully backend-agnostic. Every CARLA figure recorded above was
   nevertheless produced by hand from `carla-demo.ipynb`, and nothing
   persists a result anywhere.

   *Corrected 2026-08-10, same day, on measurement:* this entry first said
   "*N* CARLA trials has no code path at all", and that was wrong.
   `compare_policies` forwards `**trial_kwargs` to `run_trial`, so
   `provider` and `backend_factory` already reached it — a multi-trial CARLA
   comparison ran on the first attempt. The real defect was narrower: the
   capability was untyped, undocumented, untested and needed `graph=None`
   passed positionally. Worth recording, because the lesson is the recurring
   one — read the harness, do not reason about it.

   1. [X] **Backend stats into `TrialResult`** — done 2026-08-10.
      `backend_stats` *and* `policy_stats`, both filled by `run_trial` from
      an optional duck-typed `stats()`: no `SimulationBackend` or
      `DispatchPolicy` change, so every other implementation is untouched.
      Both sides went in together because they are the same fix in the same
      place — `CarlaBackend.stats()` was reachable only through a
      `holder = {}` closure in `carla-demo.ipynb`, and
      `ReorderingInsertionPolicy.stats()` only through a module-level
      `INSTANCES` list in three reordering notebooks, which is the variant
      that silently reports zeros once trials run in separate processes.

      **The ordering is the trap, and it has a test.** The counters are read
      after the run, hence after the runner has closed the backend from its
      `finally`. A `close()` that reset them would give a campaign a file of
      zeros and raise nothing. `CarlaBackend.close()` clears its actors and
      walker maps but deliberately not `_stats`.

      467 tests green. The notebooks that use `INSTANCES` were left alone —
      they hold committed results, and rewriting them belongs with the
      parallelisation work that actually needs it.
   2. [X] **Generalise the multi-trial layer** — done 2026-08-10.
      `compare_policies` and `summarize_comparison` now name `provider` and
      `backend_factory`, and `graph` became optional rather than a required
      positional. Smaller than billed, for the reason in the correction
      above: both arguments already arrived via `**trial_kwargs`, so this
      turned an accident into a contract — typed, documented, and pinned by
      the new `tests/test_comparison.py`, which had no equivalent before.

      Those tests also pin two invariants that had been resting on nothing:
      every policy sees the same seed at a given trial index, and
      `results[label][i]` is the trial seeded `base_seed + i`. Both are what
      every paired statistic in the project assumes, and both would fail
      silently — misaligned pairs produce wrong p-values, not errors. They
      are exactly what a future parallel runner would break first.

      **The seed wrinkle was left where it belongs.** A single
      `backend_factory` serves every trial, which is right for a
      deterministic world and wrong for CARLA, where traffic and spawn
      placement should vary across the sample. `BackendFactory` was *not*
      widened to carry a seed: doing so would have touched `run_trial` for
      the benefit of one caller. Step 4 builds a factory per trial in its
      own loop instead.
   3. [X] **Per-trial world reload in `CarlaBackend`** — done 2026-08-10.
      `reload_world: bool = False` loads the town unconditionally on
      `start()`; the default keeps the demo's behaviour, since re-running a
      cell should not pay a map load for nothing.

      The world acquisition moved out of `start()` into `_acquire_world()`,
      which is what made it testable: it is the only part of `start()` that
      can be exercised without a server, and the existing `_FakeClient`
      pattern extends to a fake recording `load_world` calls. Both branches
      and the default are pinned.

      Worth being precise about what this fixes, since `close()` already
      destroys the actors the backend created and an ordinary sequence of
      trials does not leak. The case it does not cover is a trial that
      **failed part-way** and left something behind — a stray vehicle across
      a junction changes the next trial's driving and nothing reports it.
      The load still happens before the traffic manager is created, so the
      map-caching trap stays respected.
   4. [X] **A results store, not a campaign file** — **redesigned
      2026-08-10 in consultation, superseding what this step used to say.**

      It used to describe a per-campaign append-only JSON lines file whose
      only job was resume. What is wanted is a **store**: runs identified
      globally rather than per campaign, queried across campaigns, and
      turned into grouped graphs and statistics. Three decisions settled it:

      - **The seed is part of a run's identity.** A run is one
        `(parameters, seed)` pair. Repeated seeds against the same
        parameters are how statistical power is bought; re-running a seed
        already recorded is *skipped or overwritten, never appended*, so no
        single run can be double-counted and bias the sample.
      - **Policies declare their own parameters.** The alternative — the
        caller declaring them at the call site — was rejected as leaving
        room for errors that would be a nightmare to pinpoint later.
      - **`results/` is tracked in git**: it is the evidence behind the
        design review's success criteria, and hours of CARLA to regenerate.

      Split into four commits:

      1. [X] **`params()` on every policy** — done 2026-08-10. Abstract on
         `DispatchPolicy`,
         **deliberately unlike `stats()`**, which is optional because most
         components have nothing to report. Every policy has parameters, and
         one that silently reported none would collide in the store with a
         differently-configured sibling — so the failure belongs at
         construction, not three weeks into a sweep. Eleven classes in
         `src/algorithm` implement it, subclasses extending rather than
         replacing; the notebook subclasses of `WindowInsertionPolicy`
         inherit it and are untouched.

         Two safety nets, because "it will be forgotten" is the entire
         reason it is abstract: the identity also records
         `type(policy).__name__`, so a subclass that forgets to extend
         cannot collide with its parent; and a **meta-test** compares each
         policy's `inspect.signature(__init__)` against its `params()` keys
         and fails on any undeclared knob.

         `tests/test_policy_params.py` discovers policies dynamically —
         a hand-maintained list would not contain the new policy that is
         the actual risk — and was **checked by breaking it**: deleting
         `n_passes` from `ReorderingInsertionPolicy.params()` fails with
         the name of the knob and what its absence would cost. A guard that
         cannot fail is worth nothing. 515 tests green.
      2. [X] **`src/experiment/store.py`** — done 2026-08-10. One
         **write-once** JSON file per
         run, flat directory, filename the identity hash
         (`sha256` of the canonical identity, 16 hex chars). Numerics are
         normalised before hashing, or `dt=1` and `dt=1.0` become two
         half-populated copies of one sweep.

         Write-once files rather than one shared file or SQLite, for a
         reason specific to this machine: **Syncthing syncs this directory
         live and has overwritten files mid-edit before.** A conflict on a
         write-once file costs one trial and leaves a visible
         `.sync-conflict-*`; a corrupted database page costs the store. It
         is also the only shape that stays correct if trials are ever
         parallelised, and a SQLite index *derived* from these files remains
         a pure addition later.

         `save_run(..., overwrite=False) -> bool` returns whether it wrote,
         and that boolean is the whole skip-or-overwrite rule.
         `to_results()` rehydrates into `dict[str, list[TrialResult]]`, so
         every existing plot helper and the summary table work on stored
         runs unchanged.

         **`to_results(paired=True)` by default**, and that default is a
         correctness decision rather than a convenience. Every paired
         statistic here indexes two policies' lists together and assumes
         position *i* is the same seed in both; a campaign interrupted
         part-way leaves policies with different seed sets, and aligning
         those by position compares unrelated runs — wrong p-values, no
         error. Dropping the unshared seeds can lose runs, but a smaller
         sample is visible in the table and being wrong is not. Pass False
         for a sweep, which is not a paired comparison.

         Writes go through a temporary file and `os.replace`, so a run is
         either absent or complete and never half-parsed.
      3. [X] **`src/experiment/campaign.py`** — done 2026-08-10. Takes
         `seeds` rather than `n_trials` + `base_seed`, so extending a
         campaign to seeds 30–59 later skips 0–29 by identity and runs only
         what is missing. Owns its loop rather than calling
         `compare_policies`, so it can check-then-skip per trial and hand
         each trial its own backend via `backend_factory_for(seed)` — which
         is how a CARLA campaign varies traffic across the sample instead of
         holding it fixed.

         **Trial parameters are resolved against `run_trial`'s defaults
         before being recorded.** Naming `capacity=16` and leaving it to the
         default are the same simulation; recording only what was passed
         would file them as two runs that never dedupe against each other.
         Reporting-only arguments — `silent`, `record_animation`,
         `keep_tick_history` — are excluded, since they cannot move a KPI.

         `world` is **required and never guessed**: it is the part of an
         identity that cannot be read off the arguments, and a thin one
         would let two genuinely different worlds share a run.

         A failing trial stops the campaign rather than being swallowed.
         Everything already completed is in the store, so restarting is
         cheap — which is exactly what removes the argument for continuing
         quietly through errors.
      4. [X] **Reading the results** — done 2026-08-10.
         `summarize_results` (the existing `_format_summary_table` with a
         public face, so hours of CARLA need not be re-run to be tabulated)
         and `plot_parameter_sweep`, a parameter on x against a KPI on y,
         one line per policy, confidence intervals computed across the
         repeated seeds. **No new dependency** — `json` and `hashlib` are
         stdlib, and pandas and seaborn were already in `environment.yml`.

         `plot_parameter_sweep` raises on an unknown column rather than
         drawing an empty axes: a mistyped dotted path would otherwise read
         as a parameter that had no effect, which is the failure you would
         believe. The docs carry the matching warning about filtering to the
         parameters being held fixed — seaborn will average across one
         nobody meant to vary and the line looks perfectly reasonable.
   **Verified end to end against a live CARLA on 2026-08-10**, server raised
   from `compose.yml` and shut down afterwards. Two policies × two seeds on
   Town01, headless, `reload_world=True`, four requests each:

   - **4 ran in 110 s; the second pass ran 0 and skipped 4 in 0.00 s.** The
     skip rule works on real runs, which is also what makes a crashed
     campaign cheap to restart.
   - 4/4 fulfilled every trial, **0 repaths and 0 walker spawn failures**,
     ~4 km driven. The reload does not disturb the driving.
   - Records carry resolved trial parameters, the policy's declared
     parameters, backend and policy counters, and provenance including
     `git_commit` and `PYTHONHASHSEED`.

   **The reload was verified with a control**, since the campaign alone
   cannot show it. Three stray vehicles were leaked into the world, as a
   trial dying part-way would: with `reload_world=True` the backend then saw
   **1** vehicle — its own bus — and with `reload_world=False` it saw **3**.
   That is the cross-trial contaminant, demonstrated in both directions.

   **Window and Reordering returned identical KPIs, and that is correct.**
   L1 made 8 repair calls and 17 relocation attempts and accepted **none**
   (`cost_improvement=0.0`): at four requests there is barely a schedule to
   repair. Worth recording because two policies agreeing to the decimal looks
   exactly like a factory that was not varied — the counters are what
   distinguish the two, which is what step 1 was for. The runs still filed
   separately, on `policy_class`.

   5. [X] **Traffic tested at `npc_vehicles=20`** — measured 2026-08-10.
      Same trials run twice, 2 seeds, 6 requests, Town01, headless. Runs are
      in `results/runs`; `npc_vehicles` is part of the world identity, so
      the two arms file separately and stay comparable.

      | | fulfilled | arrivals | km | repaths | speed |
      |---|---|---|---|---|---|
      | 0 NPCs, seed 0 | 6/6 | 11 | 5.69 | 0 | 38× |
      | 0 NPCs, seed 1 | 6/6 | 11 | 5.10 | 0 | 38× |
      | 20 NPCs, seed 0 | 6/6 | 11 | 5.69 | 0 | 13× |
      | 20 NPCs, seed 1 | 6/6 | 11 | 5.10 | **3** | 13× |

      **The service is untouched.** Every trial served 6/6 with the same 11
      arrivals, and the distances match to the metre — the route is the
      route, and traffic costs waiting rather than driving. Simulated time
      moved by +3 s and +45 s. No unroutable legs, no spawn failures.
      Throughput 38× → 13×, which lands on the 13.4× the Phase 3.1 spike
      predicted for 20 NPCs.

      **"Recovery never fires" does not survive traffic, and the reason is
      structural.** `_is_held_at_red` exempts a bus only when
      `is_at_traffic_light()` is true, and CARLA sets that only inside the
      light's *trigger volume* — that is, at the head of the queue. A bus
      queued **behind** other vehicles at a red is outside it, so the
      exemption misses, and after 10 s of no movement the stall detector
      re-plans.

      Measured rather than reasoned, because three earlier stall diagnoses
      here were wrong: instrumenting `_repath` on one 20-NPC trial, **4 of 5
      firings had the bus not at a light with a stationary vehicle directly
      ahead within 20 m**. One is unexplained.

      This is the *same class* of false positive the red-light fix
      addressed, and the empty town hid it structurally: with no traffic the
      bus is always at the head of the queue, so the case could not arise.
      The lesson generalises past this bug — a fix validated only in
      conditions where the failure cannot occur has not been validated.

      **Nothing was corrupted by it.** `_repath` re-plans from where the bus
      is and moves nothing, so no KPI was affected; the teleport that could
      have corrupted one is long gone, and this vindicates rather than
      questions its removal. What degraded was the **diagnostic**: a
      non-zero repath count would no longer mean "this run needed help",
      which is exactly what `carla-demo.ipynb` tells the reader to
      investigate.

      **Fixed the same day.** `_is_queued_behind_traffic` exempts a bus
      stopped behind a stationary vehicle in its own lane, mirroring the
      red-light exemption, and counts `queued_ticks` so the wait is still
      reported. Thresholds (20 m ahead, 3 m across, 0.5 m/s) are the ones
      the instrumented run measured, not guesses. It reads a single world
      snapshot and short-circuits on `_npc_ids`, so it costs **nothing at
      all in an empty town** — the condition every earlier measurement used.

      Re-measured with the fix, both arms overwritten in the store:

      | | fulfilled | repaths | queued | red |
      |---|---|---|---|---|
      | 0 NPCs, seeds 0/1 | 6/6 | **0**, 0 | 0 s, 0 s | 668 s, 544 s |
      | 20 NPCs, seeds 0/1 | 6/6 | **0**, **0** | 8 s, 7 s | 655 s, 537 s |

      The 3 false repaths are gone and the real queueing is small — 7–8 s
      over a 1,500 s trial. The counter means what it claims again.

      **One thing to carry into 3.7: identical parameters do not give
      identical runs.** At `npc_vehicles=0`, seed 1 took 1305 s of simulated
      time in the first pass and 1350 s in this one, with the same route and
      the same 5.10 km driven. That is the documented "approximately
      repeatable, not bit-identical" behaviour, and it means a seed is a
      dedup key rather than a reproducibility guarantee. It also means the
      campaign's variance already contains CARLA's own execution noise,
      which is realistic but has to be sized for.

   Steps 1–4 are ordinary Python needing no CARLA server, and are covered by
   the existing test suite. Only step 5 needs the server up.
### Sizing pilot — measured 2026-08-10

Eight seeds of `ReorderingInsertionPolicy` at the parameters 3.7 will use
(12 requests every 150 s, capacity 20, 900 s window, **20 NPCs**, Town01,
headless). Run twice: the first pass found the two termination bugs above and
was discarded; these are the numbers from fixed code, with all eight records
overwritten so nothing pre-fix survives in the store.

All eight served **12/12 with zero repaths**. 181 s of wall clock per trial.

| KPI | mean | sd | CI half-width at n=8 | seeds for 5% |
|---|---|---|---|---|
| Mean wait time | 350 s | 99.5 | 23.8% | **182** |
| Mean ride time | 215 s | 42.9 | 16.7% | 90 |
| Throughput | 0.303 req/min | 0.035 | 9.7% | 30 |
| Served fraction | 1.000 | 0 | 0% | already there |
| Fulfilled count | 12.0 | 0 | 0% | already there |

**The design review's bar is not affordable as stated.** Applied to every KPI,
mean wait time binds at 182 seeds — **27.5 hours** of CARLA for three
policies, against 13.6 h if ride time bound it and 4.5 h for throughput.
Wait time is the noisy one because a 12-request trial's mean is dominated by
which stop pairs its demand seed happened to draw.

**More requests per trial is not the lever it looks like.** Doubling requests
roughly halves the between-trial variance and roughly doubles trial cost, so
total wall clock is unchanged; the only gain is amortising the ~15 s world
reload, worth about 8%.

**The distinction that probably matters:** §3.2's 5% bar reads as applying to
each KPI's *absolute* mean, but every claim the project actually makes is a
*difference* between policies at the same seeds — §3.2.2's success criterion
is literally a sum of percentage differences against fixed-route, and the L1
question is a difference too. Paired differences cancel most of the
between-trial variance, since the demand seed is shared, so the seeds needed
for a *difference* should be far below 182. Unmeasured: the pilot ran one
policy, so there is nothing to pair yet.

**Resolved 2026-08-10, after the numbers above:** the 5% bar applies to the
**synthetic map**, not to CARLA. That is where many runs are produced and
where tuning happens, and a trial there costs seconds. CARLA's job is to
demonstrate transfer. So 3.7 does not need 182 seeds — it needs enough to
show the mock's findings hold in a real town — and the statistical claim
moves to a synthetic campaign that can afford whatever n the bar demands.
See the decisions table and Open questions.

**This adds a deliverable that was previously assumed rather than done:** a
synthetic-map campaign, through the store, large enough to meet the bar on
every KPI, as the formal §3.2 evidence. The existing mock figures come from
notebooks predating the store and were never filed as reproducible runs.

7. [~] **Validation** — **half answered 2026-08-10**, and the other half
   turned out to be the wrong experiment.
   - Reproduce a mock finding qualitatively (design review §5.1 already
     frames CARLA as demonstration, with the synthetic map as the fast test
     bed).

   Three policies × 8 shared seeds, Town01, 20 NPCs, 12 requests, 58 min.
   All 24 runs are in `results/runs`; every trial served **12/12 with zero
   cancellations and zero repaths**.

   | | served | wait | ride | throughput |
   |---|---|---|---|---|
   | Fixed route | 12/12 | 591 s | 580 s | 0.225 |
   | Reordering (L1) | 12/12 | 350 s | 215 s | 0.303 |
   | Window (L0) | 12/12 | 333 s | 200 s | 0.306 |

   **§3.2.2 passes on CARLA, decisively, at n=8.** Fulfilment 100% against a
   ≥80% criterion, and the sum of percentage differences against fixed-route
   is **+145.0%** for Window and **+138.2%** for Reordering against a
   criterion of merely "positive". Paired against the same seeds: wait
   −257 s (p=0.005), ride −381 s (p<0.0001), throughput +36% (p=0.0006).
   **The transfer this phase existed to demonstrate is demonstrated.**

   **L1 versus L0 did not reproduce — and the experiment had no power to
   see it.** Measured L1 *worse* by 16.3 s of wait (p=0.45) and 15.3 s of
   ride (p=0.20); nothing significant. Before reading that as a refutation
   of the execution-noise prediction, the policy counters say why:

   | | across 8 trials |
   |---|---|
   | repair calls | 161 |
   | relocations attempted | 409 |
   | relocations **accepted** | **10** (2.4%) |
   | modelled cost improvement | 571 s total = **6 s/passenger** |

   The mock measured **78 s/passenger** of modelled saving against 73 s
   observed. Here L1 finds one-twelfth of that, because **12 requests over
   1650 s with capacity 20 barely make a schedule to reorder** — the order
   is close to forced. A true effect of ~6 s/passenger sits well inside the
   ±25 s interval this experiment can resolve.

   So this is not evidence against L1; it is an experiment with nothing to
   detect. *(Corrected below — the first version of this entry gave the
   wrong reason.)*

   **There are two separate barriers here, and conflating them produced a
   wrong conclusion the first time this was written up.**

   **Barrier 1 — the effect was not present to be measured.** That is a
   property of the parameters chosen, not of CARLA. It is fixable, and the
   fix is nearly free — but *only one* of the two ways to add demand works:

   - **More requests at the same interval** merely lengthens the trial.
     Concurrency is unchanged, so the schedule does not get richer. This is
     the case the sizing analysis covered, and it buys nothing.
   - **A shorter interval at the same count** makes arrivals outpace
     service, a queue builds, the schedule gets rich, and L1 has work. It
     costs little or no extra wall clock — the bus still has 12 requests to
     serve, and wastes less time idle.

   The original entry claimed denser demand "costs trial length", which
   conflated the two and dismissed the lever that would actually work.

   **Barrier 2 — resolving the effect needs seeds CARLA cannot afford, and
   this is the real obstacle.** Even with a rich schedule L1's edge is
   modest: −92 s of ride on a ~500–600 s baseline on the mock, established
   at p<0.0001 only over hundreds of paired trials. Scaling that
   t-statistic down puts the requirement near **n≈200**, which at 3.6 min
   per trial across two policies is about **24 hours**.

   **Conclusion: the L1-versus-L0 question belongs on the synthetic map** —
   because resolving it needs ~200 seeds, *not* because CARLA cannot produce
   the effect. With a shorter demand interval CARLA would produce it; it
   simply could not measure it in affordable time. That is where it was
   already answered at n=300–750, and where fine-grained routing comparison
   is what the fast test bed is *for*. CARLA's job was transfer of the
   headline result, and that is done.

   **A finding worth carrying into the synthetic campaign: this scenario is
   under-saturated.** Fixed-route also served 12/12 with zero
   cancellations. A scenario in which even the naive baseline achieves 100%
   coverage cannot discriminate policies *on coverage at all* — it separates
   them only on wait and ride, which is exactly what happened. If fulfilment
   rate is to be a meaningful KPI rather than a constant 100%, the demand
   must stress the bus, and that demand level should be chosen deliberately
   rather than inherited from these runs.

   Not a reason to change the default policy: the mock evidence for L1 is
   far stronger than eight CARLA trials, and L1 is not *harmed* here — it
   simply has no work to do.

   **Decided 2026-08-10: reproduce both findings from one campaign.** Three
   policies — `FixedRoutePolicy`, `WindowInsertionPolicy`,
   `ReorderingInsertionPolicy` — on Town01, headless, sharing one seed
   sequence, which costs roughly 1.5× a two-policy run and answers two
   questions:

   - **The design review's §3.2.2 success criteria**, whose only comparator
     is a fixed-route bus: fulfilment ≥ 80%, and the *sum* of percentage
     differences across the other three KPIs positive. Large effect, so it
     needs the fewest trials, and it is what the project is assessed on.
   - **Whether L1 survives real driving.** The execution-noise study makes a
     falsifiable prediction: L1's mean-ride lead over L0 should *widen*
     under CARLA, not narrow (−92 s clean → −136 s at σ=0.3, with L0
     degrading ~5× faster). Smaller effect, so it is the one that sets the
     trial count.

   **Size the campaign from measurement, not from a guess.** CARLA's
   trial-to-trial variance has never been measured, so whether 30 trials
   clears the §3.2 bar (CI half-width < 5% of mean) is unknown and cannot be
   known in advance. Run ~8 trials of one policy first, read the observed
   spread, and size the real campaign from it. The wall clock is not the
   risk: headless at 0 NPCs Town01 measured ~36× real time, putting a
   3-policy × 30-trial campaign within an overnight run.

---

### Parallel trials — done 2026-08-10

Deferred since 2026-08-02, and much smaller than the original plan because
the results store had since solved its three hardest parts: ordering is
rebuilt from the store by seed rather than from completion, instrumentation
counters moved onto `TrialResult`, and nothing large crosses a process
boundary because each worker writes its own file.

`run_campaign(n_jobs=...)`. Measured on 48 synthetic runs, 16 cores:

| `n_jobs` | wall | speedup | identical to sequential |
|---|---|---|---|
| 1 | 41.9 s | — | — |
| 4 | 11.3 s | 3.7× | yes |
| 8 | 6.2 s | 6.8× | yes |
| 16 | 5.6 s | 7.4× | yes |

Returns flatten past 8 — CPU-bound pure Python gains little from
hyperthreads.

**Processes, not threads, and not only for the GIL:** `run_trial` seeds the
*global* `random` module and `DemandGenerator` draws from the same global,
so threads would interleave RNG draws and destroy every seeded result.

**Fork pinned deliberately.** Children inherit the module-level context, so
policy factories stay lambdas and the graph is never pickled; and they
inherit `PYTHONHASHSEED`, where spawn would re-randomise it per child and
make policies that break ties by set-iteration order disagree *within* one
campaign. The cost is fork's own hazard — forking a threaded process can
deadlock a child. A script through `run.sh` is single-threaded (measured);
a Jupyter kernel is not. Prefer a script for a long sweep.

**The guard is on the backend, not the map.** `SimulationBackend.parallel_safe`
defaults True; `CarlaBackend` sets False, and `n_jobs > 1` is refused for it
via a free probe construction. Keying on the provider would have forbidden a
CARLA *town* driven by the mock backend — no server, and one of the more
useful configurations available.

### The synthetic campaign — run 2026-08-10

2,280 runs: 19 policy/window configurations × 60 seeds × 2 regimes, on the
campus map (`n_seeds=17`, 2000 m, seed 42), 200 requests, capacity 40.
**Just over two minutes** at `n_jobs=10`.

**The 5% bar is met**, at n=60, for every serious contender: worst CI
half-width 1.46% (Window W900), 1.26% (Reordering W900), 1.21% (fixed route).
Only the policies being discarded miss it — `Nearest unserved` at 8.77% on
ride time.

**§3.2.2 passes on the synthetic map**, against fixed route: Window W900
+34.8%, Reordering W900 +37.3%, both at ~100% fulfilment.

**W900 is the operating point.** Coverage peaks there (100%) and falls off in
both directions — below it the window refuses too much (W450 serves 92.5%),
above it the policy accepts promises it cannot keep (W1200/1800 serve 99.8%).

**A KPI trap worth naming: mean wait rewards abandoning difficult
passengers.** `Deviated` posts the *best* wait in the field, 255 s against
Window's 332 — while serving 75.4%. The passengers it fails are exactly the
ones who would have waited longest, and a cancelled request contributes no
wait sample. Wait and ride are only meaningful read alongside coverage.

### The synthetic campaign, re-run correctly — 2026-08-11

2,280 runs on the **21-stop** campus map, 19 configurations × 60 seeds × 2
regimes, 770 s. **The 5% bar is met** for every contender (worst 2.70%).
Fixed route reproduces the documented 68.6% served exactly.

**L1 beats L0 everywhere, and by more than was recorded.** Paired over 60
seeds, mean ride, all p<0.0001:

| window | 450 | 600 | 750 | 900 | 1200 | 1800 |
|---|---|---|---|---|---|---|
| ride, L1−L0 | **−229 s** | −155 s | −91 s | −70 s | −31 s | −19 s |

**The advantage grows as the window tightens** — a finding the earlier
single-window ablation could not have seen. A tighter deadline constrains the
schedule harder, which is exactly when repairing its order pays. Served
fraction is also better at five of six windows.

**W900 is the operating point, and the reason is principled**: coverage peaks
where the service window equals passenger patience (93.8% at W900, against
85.8% at W1200 and 92.0% at W750). Promise more than the passenger will
tolerate and you accept requests that are abandoned anyway; promise less and
you refuse ones you could have kept.

**§3.2.2 has a survivorship problem worth naming before it is quoted.** The
criterion is fulfilment ≥80% *and* a positive sum of percentage differences —
but wait and ride are computed over *fulfilled* requests only, so a policy
that abandons its hard riders scores well on them. Ranked by the sum,
`Reordering W1800` leads at **+93.8%** on 86.3% fulfilment, ahead of
`Reordering W900`'s +67.8% on 93.8%. `Constrained` posts the best ride on the
page (223 s, +66.1%) while serving **56.0%**, and fails only because of the
fulfilment gate. **The gate is doing the real work; the sum must not be
maximised on its own.**

Failing §3.2.2 outright: `Nearest unserved` and `Marginal cost` (sum −26%,
ride roughly double fixed route's), plus `Skip-stop`, `Deviated` and
`Ride bounded` on the fulfilment gate.

**Recommendation: `ReorderingInsertionPolicy` with `max_wait_s` set equal to
passenger patience.** Best coverage, significantly better ride than L0 at
every window, and it clears both criteria.

### The map-seed sweep — 2026-08-11

7,000 runs, 25 maps × 7 policies × 40 seeds, 1,659 s. Seeds capped at 40 so
map 42 (which has 60 from the campaign) does not weight the comparison.

**The ranking is map-independent — this was the question the sweep existed to
answer, and the answer is clean.**

| policy | median rank | range | maps won |
|---|---|---|---|
| Reordering W900 | 1 | 1–2 | **22 / 25** |
| Window W900 | 2 | 1–2 | 3 / 25 |
| Window W750 | 3 | **same on every map** | 0 |
| Window W1200 | 4 | **same on every map** | 0 |
| Skip-stop | 5 | **same on every map** | 0 |
| Fixed route / Constrained | 6–7 | swap | 0 |

Only the top two ever swap. Every single-map policy conclusion in this
project is safe to quote.

**W900 maximises coverage on 25 of 25 maps.** The "set the window to
passenger patience" rule is not a map-42 artefact.

**L1's advantage is larger than the campus map suggested.** Pooled over 1,000
(map, seed) pairs: ride **−111.5 ± 7.4 s** (p ≈ 2e-138), wait −7.3 ± 1.5 s,
served +0.53 ± 0.17 pp. Map 42 showed −70 s, because map 42 is easy.

**The map dominates the seed as a source of variance — the finding with the
widest consequences:**

| KPI | between-map sd | within-map sd | ratio | range across maps |
|---|---|---|---|---|
| served | 8.3 pp | 2.4 pp | **3.4×** | 65.5 – 98.8 % |
| wait | 42.6 s | 20.7 s | 2.1× | 367 – 543 s |
| ride | 245.6 s | 64.4 s | **3.8×** | 278 – 1336 s |

So the 5% CI bar met at n=60 on map 42 measures precision **within that map**,
not the uncertainty of the figure as a general claim. Any single-map number
carries an error bar two to four times wider than its own interval suggests.
**Map 42 is also easier than typical** — 93.5% served against an all-map mean
of 87.9% — so every documented figure is drawn from a favourable map.

**§3.2.2 does not hold universally.** Per map, against fixed route:

| policy | maps passed | worst fulfilment |
|---|---|---|
| Reordering W900 | **21 / 25** | 65.5 % |
| Window W900 | 19 / 25 | 65.7 % |
| Window W750 | 17 / 25 | 63.0 % |
| Constrained | 0 / 25 | 33.1 % |

Every failure is the **fulfilment gate**, not the sum — the sums stay positive
almost everywhere. So the honest claim is "met on the campus map, and on 21 of
25 random maps", not "met". On the hardest maps no policy tested reaches 80%,
which is a statement about the load those maps present rather than about the
routing.

### The load sweep — 2026-08-11

4,950 runs, 5 maps × 3 policies × 30 seeds × (7 demand rates + 4 capacities),
40 min. Maps chosen by measured difficulty from the sweep above: 17
(hardest), 24 (25th pct), 10 (median), 42 (campus, 75th), 15 (easiest).
`notebooks/load-sweep.ipynb`.

**The claim it existed to settle is supported: the four §3.2.2 failures above
are a load limit, not a routing limit.**

| map | 17 | 24 | 10 | 42 | 15 |
|---|---|---|---|---|---|
| clears 80% (Reordering W900) | from 180 s | from 60 s | always | always | always |

Map 17 — the one that bottomed out at 65.5% — climbs monotonically to
**94.6% at one request every 300 s**. There is only one bus in this
simulation, so the honest statement is about **fleet size**, not about the
algorithm. Quote §3.2.2 as "met on the campus map and on 21 of 25 random
maps, with the four failures load-limited and recovering above roughly one
request per 180 s on the hardest".

**Fixed route does not benefit from thinner demand at all** — the finding
nobody was looking for, and the more useful one. It sits at 33–37% across
the entire 10× range of demand rates on map 17, and is pinned at ~81% on the
easiest map while both dynamic policies reach 100%. The comparator's coverage
is bounded by its cycle geometry rather than by load, so **the dynamic
advantage is structural and widens as demand thins.**

**Capacity refines the headroom finding rather than confirming it.** Pooled
over the five maps at the standard 60 s interval:

| capacity | 10 | 20 | 40 | 80 |
|---|---|---|---|---|
| Reordering W900 | 73.3 | 85.1 | **86.3** | 85.8 |
| Window W900 | 67.6 | 82.7 | 85.8 | 85.3 |
| Fixed route | 53.9 | 57.2 | 57.8 | 57.2 |

"Rejections are caused by the deadline rather than by seats" was measured at
capacity 40 and holds **from 20 seats up** — a 4× increase past that moves
coverage 1.2 pp. Below 20 it is simply false: 10 seats costs L1 13 pp. State
it as *"at capacity ≥ 20, seats are not the binding constraint"*. L1 also
degrades more gracefully into the seat-limited regime than L0 (73.3 vs 67.6).

Two limits worth naming before quoting this. The load explanation is
demonstrated directly on **map 17 only** — map 24 already clears at the
standard rate — so "the four failures are load" is a well-supported inference
from one instance, not four measurements. And capacity was swept only at the
60 s interval, so the ≥20 claim is unverified in the saturated regime where
seats would bind hardest.

### The safety-factor sweep — 2026-08-11

3,840 runs, 3 maps × 4 noise levels × 4 factors × 2 policies × 40 seeds,
30 min. **W750 against 900 s patience**, deliberately: at window == patience
a breach is structurally impossible, because anyone who waits that long has
already been cancelled. `notebooks/safety-factor-sweep.ipynb`.

**The mechanism is real.** Breach rate rises 3.41% → 7.29% as σ goes 0 → 0.3
at `safety_factor=1.0`, and a higher factor pulls it back monotonically with
the gap widening as noise grows. Reordering, pooled over three maps:

| σ | 1.0 | 1.1 | 1.25 | 1.5 |
|---|---|---|---|---|
| breach | 3.41 → 7.29% | −1.4 to −1.8 pp | −0.4 to −3.0 pp | −0.3 to −3.5 pp |
| Δ ride at σ=0.2 | — | +28 s | +53 s | +111 s |

**Two findings underneath the headline matter more than it does.**

1. **There is a ~3% breach floor `safety_factor` cannot touch.** At σ=0 the
   mock backend drives exactly what the graph promises — there is no
   execution error to protect against — and 3.41% of fulfilled riders still
   breach, with 1.5 barely denting it (3.11%, inside the error bars) for
   +65 s of ride. So **most breaches at the recommended operating point are
   structural, not execution noise.** The likely cause is the progress
   fallback, which drives to the nearest pending pickup ignoring the window
   entirely. Worth an hour to attribute, because "3% of promises are broken
   by a fallback we wrote" is a different sentence from "by traffic", and
   only one of them is fixable.
2. **Under the design review's own scoring, raising the factor loses
   points.** Breach rate is not one of the four §3.1 KPIs. Ride time is, and
   it is inside the §3.2.2 sum. At σ=0.2, `1.25` costs +53 s on a ~759 s
   baseline — a 7% regression against a graded criterion, to fix something
   that is not scored at all.

**The knee is 1.25**; 1.25 → 1.5 buys ~0.5 pp more breach for another +25 s
ride and −0.8 pp coverage. Nothing above 1.25 is defensible.

**Statistical caveat.** The deltas are differences of *unpaired* means pooled
over three maps of very different difficulty, which is why the coverage and
ride error bars dwarf the effects. The breach trend is large and monotone and
is safe to quote; the **±0.3 pp coverage deltas are indistinguishable from
zero**, and the notebook's verdict column reads noise on that axis. A paired
test per (map, seed) is cheap and would settle it.

**What is still missing is CARLA's actual σ.** The whole sweep prices a knob
against a noise level never measured. It is recoverable from the Town01 runs
already in the store — driven leg durations against the graph's estimates —
and until it exists, 1.0 vs 1.25 is a choice made by feel.

### Plan-call latency, §1.1.2 — measured 2026-08-11

23,536 plan calls over 140 trials, run twice: a desktop **Ryzen 7 5800X3D**
and a laptop **Ryzen AI 9 HX 370**, identical configuration.
`notebooks/plan-latency-local.ipynb` and `-remote.ipynb`. Sequential and
unstored by design — workers contending for cores would inflate every
measurement, and a `TimedPolicy` wrapper would corrupt run identity.

**Satisfied. Zero calls exceeded the budget on either machine.**

| arm | 5800X3D max | margin | HX 370 max | margin |
|---|---|---|---|---|
| **Reordering W900** — the operating point | 2,011 ms | **2.5x** | 1,272 ms | 3.9x |
| Reordering W1800 — stress bound, not an operating point | 3,042 ms | 1.6x | 1,944 ms | 2.6x |
| Window W900 (L0) | 952 ms | 5.3x | 613 ms | 8.2x |

**The claim to quote**: at the recommended operating point the worst plan
call in 23,536 was 2.0 s on the slower machine, against the ~5 s a bus is
stopped for boarding anyway. Quote **max and p99, never the median**, and
name the load — 200 requests on the campus map, up to 29 pending at a call.

**The budget was the wrong one in this document until today.** It is
passenger dwell, not the 50 ms CARLA tick: the bus plans only from
`on_arrival` and is stationary throughout, so the tick binds only under
mid-leg re-planning, which was deliberately not built. In synchronous mode a
slow call pauses the whole world rather than stranding the bus.

**5 s of dwell was chosen, not measured, and the verdict is sensitive to
it**: at a 2 s dwell the desktop running L1 at W900 would breach and L0
would not. Quote the assumption wherever the claim is quoted.

**The two machines differ by a constant 1.57x** — wall clock 1.57, p50 1.57,
p99 1.58, max 1.58, with the scaling curves the same shape merely rescaled.
So the workload is purely single-thread-bound, the measurement is clean
(noise would not hold a constant ratio across median, tail and total), and
**any other machine can be predicted by benchmarking its single-thread speed
and scaling**. The *laptop* is the faster one — the X3D trades clock for
cache, and interpreter dispatch is clock- and IPC-bound. Quote the desktop:
it is conservative and it hosts CARLA.

**Scaling is smooth but still climbing.** No cliff, which was the risk; but
no plateau either — p99 rises monotonically and steeply in pending requests
and is still rising at the largest load seen. Eyeballed off the curve, the
5 s crossing sits near 30 pending at W1800 and roughly double at W900.
*Eyeballed, not fitted*: the notebook discards its per-call frame, so a fit
needs a re-run. Structurally mitigating — the scenarios that stress latency
are the hard maps already failing the coverage gate, where the answer is
already a second bus, and a second bus cuts pending per bus.

**L1's latency cost is worse than its throughput cost implied**: ~1.4x per
trial, but 2.1x on max and **3.5x on p99**. Its tail is much fatter than its
mean. Does not change the recommendation; does add a term nobody had priced.

**One anomaly, unexplained.** For L0 the widest window is the *cheapest*
(p99 277 ms at W1800 against 356/379 at W750/W900) while for L1 it is the
most expensive. Hypothesis, unchecked: at W1800 against 900 s patience,
accepted requests are abandoned before pickup, so L0's schedule churns
rather than grows while L1's repair loop still pays for it.

**This does not reopen the `forward_slack` optimisation.** It was refused on
throughput grounds and would come back only if latency bound. It does not
bind. Trigger to revisit, now measured: a dwell under ~2.5 s, or loads past
~30 pending.

### ⚠ An earlier campaign ran on the wrong map — corrected 2026-08-11

**The campaign above used `stop_density` at its default 0.15, giving a 6-stop
map. Every historical study used `stop_density=0.5` — 21 stops.** The campus
map is the 21-stop one; the default was never the map this project measures
on, and nothing said so.

On a 6-stop map the bus keeps up with any demand. Plain fixed route serves
100%, schedules are tiny, and there is almost nothing to reorder — so the
campaign's headline conclusions are artefacts of an over-easy world:

- "L1 is not worth its cost" — **wrong**.
- "W900 is the operating point, coverage peaks there" — **unverified**; the
  window table it came from was measured on 21 stops.
- The §3.2.2 margins (+34.8%, +37.3%) — real but on the easy map, where fixed
  route already serves everyone.

**A wrong diagnosis was published before the density was checked.** The
discrepancy was attributed to `commitment`, on the strength of a commitment
sweep in which `commitment=4` reproduced the recorded 93.3% served *exactly*.
That was a coincidence. The lesson is the recurring one and it was ignored:
the configuration was assumed rather than read, and a coincidence that
confirmed the assumption was taken as evidence.

**Re-measured on the correct 21-stop map at `commitment=0`, 30 paired seeds:**

| | served | wait | ride |
|---|---|---|---|
| Fixed route | 68.9% (doc records 68.6%) | 448 s (451) | 656 s (654) |
| L0 W900 | 93.4% (93.1%) | 471 s (466) | 541 s (521) |
| L1 W900 | 93.6% | 463 s | **465 s** |

**L1 − L0 mean ride −75.6 s, p < 0.0001**, against the −63.7 s on record.
Every historical figure reproduces. **The documentation was right and the new
campaign was wrong.**

Both notebooks now set `stop_density=0.5` and carry it in the world identity,
so a 6-stop run can never share an identity with a 21-stop one. The 2,280
runs already recorded remain valid *as runs* — they are simply a 6-stop
experiment, and the store distinguishes them.

### Where a synthetic trial actually spends its time — profiled 2026-08-10

10 trials per policy, 100 requests, 40-seed map. **2.35 s/trial for L0,
3.33 s for L1.** cProfile, single process.

| | L0 cumulative | calls |
|---|---|---|
| `_best_insertion` | 103% (all of it) | 16,190 |
| `_feasible` | 94.3% | **1,301,408** |
| `arrival_times` | 75.0% | 1,324,203 |
| `grouped_visits` | 31.3% | 1,325,048 |
| `travel_time` (3 layers) | 22.5% | 16,699,809 |
| `forward_slack` | 10.8% | 1,301,408 |
| **`_cost`** | **1.5%** | **21,950** |

**The funnel is the story.** 2.9M candidates are generated, `capacity_ok`
rejects ~55% cheaply, feasibility then rejects **98.3%** of the survivors,
and only 22k reach the cost function.

**Two optimisations proposed earlier are refuted by this, and both were
mine.**

1. *"`_feasible` and `_cost` both compute `arrival_times`; dedupe it and
   halve the work."* Worth **~1.5%**, not 50%. The premise was that `_cost`
   runs per candidate — it does not. It runs only for candidates that pass
   feasibility, which is 1 in 59.
2. *"Memoise `grouped_visits`; it is 31% and recomputed constantly."* Worth
   **~0%**. Every candidate is a *new* `Schedule` — the class is immutable by
   design — so each instance calls `grouped_visits` about once. The call
   counts give it away: 1,325,048 calls against 1,324,203 `arrival_times`.

**Only the structural change is worth anything**, and the funnel says why:
`_feasible` re-simulates the whole candidate to reject 98.3% of them.
`forward_slack`'s own docstring describes the alternative — compute arrivals
and slack **once** for the base schedule, then test each splice point against
the delay it introduces, O(1) instead of O(n) — and `_best_insertion` bypasses
it by building each candidate first. That targets 94% of the runtime rather
than 1.5%.

**Recommendation: do not do it, for now.** It is a reimplementation of the
feasibility test, it touches the code behind every recorded baseline, and
parallelism already gives 6.8×. At 3 s/trial and `n_jobs=8`, a
7-policy × 800-seed sweep is about 40 minutes. Revisit only if a sweep you
actually want is too slow at that.

## Decisions already made

| Question | Answer | Source |
|---|---|---|
| Who drives the bus between stops? | CARLA's built-in autonomous driving | design review §4: *"should be sufficient for a PoC"* |
| Is CARLA the experiment engine? | No — demonstration; the synthetic map stays the fast test bed | design review §5.1 |
| Trials in CARLA | Sequential only; one server | hosting CARLA is heavy |
| Commitment window | Default 0 | measured monotonically harmful; see `bus_agent.md` |
| `retry_rejected` | Default off | 0.9% admissions, no KPI change, ~13% more compute |
| `n_passes` | Default 1 — L1 is finished | Converging adds nothing (all p>0.4); the second sweep accepts 0.086% of attempts |
| Does L1 survive CARLA-like driving? | **Yes, and its advantage grows** | Under execution noise L1's mean-ride lead widens −92 s → −136 s; L0 degrades ~5× faster. Do not re-litigate in Phase 3 |
| `safety_factor` | **Stays 1.0. If it is ever raised, 1.25 and no higher, and only on CARLA** | Swept properly 2026-08-11 (3,840 runs, 3 maps). It does cut breaches — 7.29% → 4.33% at σ=0.3 — but breach rate is not a scored KPI and ride time is, so at σ=0.2 the +53 s it costs is a 7% regression against §3.2.2 for a gain the criteria do not count. At σ=0 it buys nothing at all: a ~3% floor survives every factor, so most breaches are structural rather than noise. Above 1.25 the curve is flat and coverage starts to fall. **Blocked on measuring CARLA's actual σ**, which nobody has done |
| Which CARLA town? | **Town01** for now, but nothing may hard-code it — several maps are wanted eventually | Town choice does not affect throughput; Town01 drives a bus well, Town10HD_Opt barely moves one even on plain autopilot |
| How is the bus steered? | `traffic_manager.set_route`, given turns from our own route planner in `src/simulation/carla_routing.py` | `set_path` with a single destination is not navigation — it is a junction-decision API, and given one distant point the bus cycles. Planning the route and handing over the turns took a five-tour campaign from 17/25 legs to 21/25. See the trap below |
| Why not CARLA's `GlobalRoutePlanner`? | Measured identical, and ours is 60 lines against 1,240 | The pip wheel is client bindings only and does not ship `agents`, so either way the code would be copied into this repository. Ours reproduces theirs exactly — same 21/25, same per-tour split, same four failures to the metre — with no vendoring and no `shapely` |
| `fixed_delta_seconds` | **0.05**, never above 0.1 | Above `max_substep_delta_time × max_substeps` the physics silently stops keeping up; at 0.5 vehicles cover half the ground per simulated second |
| Where do bus stops come from? | Hand-authored per town, an input to the provider | A real deployment names its maps and stops rather than generating them |
| Does running faster cause vehicles to get stuck? | **No.** Wall-clock rate has no effect at all, and a finer physics step does not help either | Measured; see below |
| Who owns the traffic manager? | `CarlaBackend` creates its own on its own port, after the town is loaded, and shuts down only what it created | A traffic manager caches its map at creation and never re-caches it, so an inherited one steers the bus into the scenery — but it is also hosted by whichever *process* created it, so sharing or tearing down someone else's is worse. See the two traps below |
| Unwedging (lifting a jammed bus onto the lane) | **Removed.** Re-planning on no progress stays | It teleported, and a leg saved that way is a KPI that did not happen. Once red lights, the stale traffic manager and the `set_path` loop were fixed it never fired at all: 15/15 legs over 10 km with zero lifts, and deleting it left the driven distances identical |
| Which vehicle drives? | `vehicle.volkswagen.t2_2021`, a shuttle | Completes every leg on Town01 without needing recovery, and is indistinguishable from the Sprinter. The full-size `fusorosa` is **not supported**: it only got round on the lift-onto-the-lane recovery that has since been removed, needing 20 lifts over three tours where the shuttle needed none |
| Demo or campaign on CARLA? | **A persistent campaign, but not a statistical one** | Decided 2026-08-10, then **refined the same day once the pilot priced it**. The harness, store and world reload were worth building and are used. What changed is what CARLA is *for* — see the next row |
| Where does the **CI half-width < 5%** bar apply? | **To the synthetic map, not to CARLA** | Decided 2026-08-10. The synthetic map is where many runs are produced, parameters and strategies compared, and tuning done — so that is where a statistical claim belongs and where it is cheap. CARLA stands in for a real deployment: its job is to show the result **carries over**, not to re-establish it. This is §5.1 governing the *statistics* while §3.2 governs the *synthetic* campaigns, and it is what makes the numbers affordable — the pilot priced the bar at 182 seeds and 27.5 hours on CARLA against seconds per trial on the mock |
| What does 3.7 validate? | **Both findings, from one campaign** | Decided 2026-08-10. Three policies sharing a seed sequence costs ~1.5× a two-policy run and answers both the §3.2.2 success criteria (against fixed-route) and whether L1's ride-time advantage survives real driving |

## Open questions

- **NPC traffic?** Empty is fastest and most reproducible; light traffic is
  more realistic and is what the execution-noise study models synthetically.
  20 vehicles costs 4× throughput (50×→13×) and still leaves headroom.

  ~~This is the largest untested assumption in Phase 3.~~ **Tested
  2026-08-10; see step 3.6.5 for the numbers.** Traffic costs 3× wall clock
  (38× → 13×) and changes the service not at all: 6/6 served in every arm,
  same arrivals, same distances to the metre. The bus is never boxed in — no
  unroutable legs and no spawn failures.

  One claim did fail. "Recovery never fires" holds only in an empty town,
  because the red-light exemption misses a bus queued *behind* traffic. It
  costs a misleading diagnostic rather than a wrong KPI.

  **What remains open is the trade, not the risk.** Realism costs 3× on a
  campaign already sized in hours. Empty is faster and more reproducible;
  20 vehicles is closer to what the execution-noise study modelled
  synthetically, and that study's prediction about L1 is half of what
  Phase 3.7 sets out to test.
- ~~**Which reading of CARLA's role governs?**~~ **Resolved 2026-08-10, in
  two steps, and the second corrected the first.**

  §3.2 says runs "will be done on the CARLA server as well" at the same
  statistical bar (CI half-width < 5% of mean); §5.1 says CARLA is for
  demonstrations. The first reading took §3.2 literally and built toward a
  statistical CARLA campaign. **The sizing pilot then priced that at 182
  seeds — 27.5 hours for three policies**, which is not a thing this project
  can afford three weeks from the deadline.

  **The resolution is a split, not a compromise.** The bar belongs to the
  **synthetic map**: that is where many runs are produced, where parameters
  and routing strategies are compared, where tuning happens, and where a
  trial costs seconds rather than three minutes. CARLA stands in for a real
  deployment, and its job is to show that what the synthetic map established
  **carries over** — a demonstration of transfer, not an independent
  re-establishment of the result.

  So Phase 3.7 needs only enough seeds to show the mock's findings hold in a
  real town, and the statistical claim moves to a synthetic campaign that is
  cheap to run at whatever n the bar demands.

  **The harness was not wasted by this.** The store, the campaign runner and
  the per-trial world reload are what make either kind of run reproducible
  and resumable, and the synthetic campaign will use all three.
- **Mid-route replanning.** Functional requirement 3 is *"the bus receives
  requests in real time (while potentially already in motion)"*. Today
  `on_arrival` is the only replan trigger. In the mock, legs are short and
  this is nearly invisible. On Town01 it is not a hypothetical: legs are
  **measured at 45–399 s, median ~160 s**, so a request arriving just after
  a departure waits an entire leg before the bus can even consider it. This
  is the clearest remaining gap between the implementation and a stated
  functional requirement.

  **Decided 2026-08-10: not worth building. "Maybe if we have time."**
  Functional requirement 3 is not a rigid requirement, and the analysis
  below is why the cost/benefit does not justify it.

  **The cheap version buys nothing — this corrects an earlier claim here.**
  It looked as though the bus could re-plan mid-leg without owning a mid-leg
  position, by projecting to the end of the current leg:
  `_build_projected_state` already starts its clock at `_arrived_at` and adds
  whole legs, so it yields "at the current destination at time T" regardless
  of how much of the leg has elapsed. True, and useless. With the current leg
  locked, `_route[0]` is unchanged, so the commanded destination does not
  move; the recomputed tail cannot be executed before arrival; and
  `on_arrival` re-plans there anyway with strictly fresher information.
  Boarding and alighting read the pool, not the route. **No KPI can move.**

  **Only diverting the leg in progress could change an outcome**, and its
  upside is bounded by roughly one leg (median ~160 s on Town01) for the
  subset of requests that arrive mid-leg *and* justify a diversion. Against
  that: it needs the mid-leg position ownership Phase 2 deferred — and CARLA
  cannot report time-remaining on a leg; it means re-issuing `set_route`
  mid-leg, poking the exact machinery that took days to stabilise; and it
  invalidates every mock baseline, so the L1 ablation, window sweeps and
  noise study would all need re-running.

  **If it is ever reopened, measure the ceiling first**, cheaply: run
  `CarlaMapProvider("Town01")` against the **mock** backend — real geometry
  and long legs at seconds per trial — and compare current behaviour with a
  deliberately unphysical oracle that re-plans freely every tick with no
  committed leg. That overestimates the benefit, so a small ceiling closes
  the question with evidence rather than argument.

  ~~§1.1.2 plan-call latency remains genuinely unverified~~ **Measured
  2026-08-11 and satisfied** — see the section below.

---

## Known traps

- **The traffic manager caches the map it was created on, permanently.**
  `client.get_trafficmanager()` builds its road network from whatever map is
  loaded at that instant, and **never re-caches it on `load_world`**. One
  created before the town is loaded will steer vehicles around the new town
  using the *old* town's waypoints. Measured directly: create it on Town02,
  load Town01, then ask `get_next_action` where it intends to drive a car
  that is physically in Town01 — **60/60 of its chosen waypoints lie exactly
  on Town02, and 0/60 on Town01**.

  The damage is total and silent, and it looks exactly like bad driving —
  the bus saws into the kerb and the sidewalk. On an identical five-leg
  Town01 tour:

  | | legs | off any lane | unwedges | repaths |
  |---|---|---|---|---|
  | traffic manager made before the load | **0/5** | **92.8%** | 34 | 105 |
  | traffic manager made after the load | 4/5 | 9.8% | 6 | 28 |

  It bites only on a **fresh server**, because that is the only time the
  startup map (Town10HD_Opt) differs from `TOWN`. That is the whole
  explanation for the first run after `docker compose up` misbehaving while
  every later run in the same session looked fine. It is not the delta, not
  the wall-clock rate, and not the vehicle.

  `CarlaBackend.start()` now creates a traffic manager **of its own, on its
  own port**, after the map load, which makes the repaired run bit-identical
  to a clean one. `carla-demo.ipynb`'s preflight was the trigger — `revive()`
  touched the traffic manager two cells before the town was loaded — and now
  loads the town first. **There is no API to ask a traffic manager which map
  it holds**, so not inheriting one is the only cheap defence.

- **A traffic manager belongs to a process, not to the server**, and sharing
  one fails silently. It is an RPC service bound to a port, hosted by the
  client that created it; `get_trafficmanager()` returns a *remote proxy*
  when somebody else owns that port. A proxy to a traffic manager that is no
  longer being ticked — a Jupyter kernel that finished a run and was never
  restarted is the usual case — accepts the vehicle and then never commands
  it. The bus sits still reporting **zero throttle and zero brake** with the
  action `Void`, which reads exactly like being wedged.

  **Tearing down the holder is not the fix**, and was tried: it disconnects
  the other client mid-session, and re-binding the same port immediately
  afterwards either raises `bind error` or returns a traffic manager that
  drives nothing. `CarlaBackend` now searches upward from port 8000 for one
  nobody is using and shuts down only what it created. Verified by holding
  8000 externally: the backend takes 8001 and drives identically.

  This cost an hour of misdiagnosis. The tell was that *every* stop looked
  broken while the same code had just completed 21 of 25 legs — a 15-of-15
  failure rate is a broken harness, not a broken map.

- **`set_path` is not "drive to here".** It reads like a destination API and
  is a *junction-decision* one: the traffic manager can only choose, at each
  junction, the exit that best advances towards the next unconsumed point.
  Given a single point half a town away that choice is greedy, and on a grid
  it cycles. Measured on Town01, a bus told only where its stop was drove a
  ten-road loop — `25 → 75 → 61 → 1 → 27 → 16 → 253 → 10 → 341 → 334 → 25 →
  …` — indefinitely, coming within 78 m and **never once entering the road
  the stop was on**.

  It is not ignored, and re-issuing it does not help: with the single point
  the bus reached 78 m over 712 m driven, with no path at all 122 m over
  984 m, and re-issuing every 5 s gave a byte-identical trace. The
  instruction is followed; it just cannot express a route.

  Three diagnoses this rules out, each of which looked plausible first:
  the stops (every one snaps to its authored lane at **0.00 m**, on a
  driving lane, none in a junction), the vehicle (the same legs fail for
  every blueprint), and the destination (every "unreachable" stop is
  reachable from other approaches — the failures cluster by *origin*).

  `src/simulation/carla_routing.py` plans a lane-level route by
  breadth-first search over `waypoint.next()` and reads the turns off it,
  which `set_route` takes directly.

  **What routing actually buys, measured both ways.** The headline figure
  first reported here — 17/25 legs to 21/25 — was taken at a 200 s leg cap,
  and most of that gap is *speed*, not reachability: given 600 s, single
  points also complete tours 4 and 5, just slower (1823 s of simulated time
  against 1537 s, ~16% worse). The correctness claim is narrower and real:
  **some pairs never arrive at all.** `s04 → s07` single-point drove
  **3086 m over 600 s** and never came closer than 78.0 m — the same 78.0 m
  it reached at 150 s, because the loop is closed. With turns it arrives in
  142 s. Over tour 2 at a 600 s cap: single-point 4/5, routing 5/5, and
  678 s of simulated time against 985 s for one leg fewer.

  So: a correctness fix for the pairs that cycle, and a worthwhile speed-up
  everywhere else.

- **A bus at a red light is indistinguishable from a wedged bus, unless you
  look.** `_track_stall` counts any tick covering less than 0.05 m — 1 m/s at
  the default delta — as a stall. Town01 has **36 traffic lights cycling in
  groups of three**, so an approach waits about **28 s of red**, and the
  clock starts while the bus is still braking, several seconds before it
  actually stops. Against a 30 s unwedge threshold, every long red overshot.

  Measured over five six-stop tours *before* the fix: 124 repaths and 20
  unwedges, of which **all 20 unwedges, and 121 of the 124 repaths, fired on
  a bus standing at a red light on a proper driving lane**. Not one was a
  genuine wedge. Each unwedge teleported the bus 6 m forward — through the
  junction, against the light — which is the jitter visible in the CARLA
  window. The old comment claiming the thresholds were "well past any
  credible red light" was simply wrong, and nobody had checked.

  `_track_stall` now **freezes** both clocks while `_is_held_at_red(bus)`
  holds, rather than resetting them, so a bus genuinely jammed inside a
  junction still carries its accumulated time into the green. The same five
  tours then fire one repath and zero unwedges.

  Lesson worth keeping: a stall detector needs to know what a *legitimate*
  stop looks like, or it will find stalls everywhere. Dwell was already
  exempt; red lights were not.

- **Simulation speed does not cause the stalls.** Two different things get
  called "speed" and they have to be separated. Measured on an identical
  six-stop Town01 tour:

  | Same `delta` = 0.05, different wall rate | legs | unwedges/min | stalled | speed |
  |---|---|---|---|---|
  | no rendering | 3/6 | 0.31 | 44% | 37.4× |
  | rendering on | 3/6 | 0.31 | 45% | 3.9× |
  | throttled | 3/6 | 0.15 | 44% | 3.6× |

  A **10× spread in wall-clock rate changes nothing** — same legs, same
  1.59 km driven. That is what synchronous mode means: the world advances by
  exactly `fixed_delta_seconds` per tick however long the tick took in real
  time. Slowing the simulation down will not make the driving better.

  Varying the physics step does not help either: 0.025 s and 0.05 s are
  identical (3/6, 0.31, 44%), 0.1 s is marginally worse, and the *finest*
  step tried — 0.0125 s — is the **worst** of the four (2/6 legs, 0.86
  unwedges/min, 54% stalled). So there is nothing to buy by stepping finer,
  and 0.05 stays the right default.

  **What survives of this, and what does not.** The *comparison* stands, and
  it is the point of the entry: a 10x spread in wall-clock rate moved
  nothing, and a finer physics step moved nothing, because all arms shared
  whatever state the traffic manager was in.

  The absolute figures do not survive, and neither does the conclusion once
  drawn from them — that "the stalls are geometric". Those runs predate both
  the traffic-manager fix and the red-light fix, and the stalls they counted
  were overwhelmingly a stale road graph and cars waiting at lights. The
  `unwedges/min` column measures a mechanism that no longer exists. On the
  current code the same bus completes 15 of 15 legs and never stalls at all.
  Quote the invariance, not the numbers.
- **Two termination bugs that were hiding each other.** Found 2026-08-10 by
  the Phase 3.7 sizing pilot, which is the entire argument for running a
  rehearsal before a campaign.

  1. **`is_done()` ignored demand still to come.** It ended a run when the
     pool held no `PENDING` and no `ON_BUS` requests — but an empty pool is
     the ordinary state of a bus between requests. At a 150 s demand
     interval, **2 of 8 CARLA trials ended having served 1 of 12 requests**,
     because the bus finished that first trip in under 150 s and the second
     request did not exist yet.

     It reported **`served_fraction` of 1.0**: the metric is
     `fulfilled / (fulfilled + cancelled + pending + on_bus)`, and the eleven
     requests never created cannot count against it. A trial that did not
     happen scored perfect service, on one of the design review's own
     success criteria.
  2. **A parked bus never woke up.** `BusAgent` re-plans in its constructor
     and in `on_arrival`, and nowhere else — `next_stop()` is documented as
     *"without triggering a re-plan"*. So a bus whose route empties never
     arrives anywhere again, therefore never re-plans, therefore keeps an
     empty route. It sleeps through every later request. Measured after
     fixing (1): the bus served one request and sat still for the remaining
     20,000 s with four passengers waiting.

  **Each concealed the other.** The only moment the bus could park was the
  moment the pool emptied — which is exactly when the old `is_done` ended the
  trial. Fix one and the other appears immediately; leave both and the
  simulation looks fine.

  Fixed by requiring `demand_generator.is_exhausted()` in `is_done`, and by
  `BusAgent.replan_if_idle(sim_time)`, called from `_process_movement` when
  the route is empty and requests are pending. Idle-only is deliberate and
  safe: the bus is stationary, so there is no committed leg to contradict.
  **A bus in motion still re-plans only on arrival** — the separate
  functional-requirement gap under Open questions.

  **The golden master is bit-identical across both fixes**, so no recorded
  mock result changes. On the campus map demand outran service, so the route
  never emptied and neither bug could fire.

- **`advance()` must never return a batch of arrivals.** Arrivals inside one
  tick are sequentially dependent: the agent boards, alights and re-plans on
  each one, and the leg that follows is not known until it has. The contract
  is "advance until `dt` is exhausted **or** one arrival occurs", and any new
  backend has to honour it. The `coarse_tick` golden-master scenario (dt=120,
  more arrivals than ticks) is what catches a violation.
- **`advance()` must consume the whole budget when it does not arrive**, or
  the runner's clock and the world's drift apart. For CARLA that means `dt`
  has to be a whole multiple of `fixed_delta_seconds`.
- **`PYTHONHASHSEED` is not pinned.** `run_trial(seed=N)` is not reproducible
  across processes: several policies `min()` over a `set[str]`, and tie-breaks
  follow hash order. `jupyter-run.sh` does not set it. Any golden master or
  cross-session comparison must force `PYTHONHASHSEED=0`.
- **The `agents` package is not installed, and we resolved this.** The pip
  `carla==0.9.16` wheel is client bindings only — the whole manifest is
  `__init__.py`, `libcarla.so` and two `.pyi` stubs. `BasicAgent`,
  `BehaviorAgent` and `GlobalRoutePlanner` live in `PythonAPI/carla/agents/`
  inside the container image, as ordinary Python over the same public API, so
  there is nothing privileged in them. **Decision: we wrote our own** — see
  `src/simulation/carla_routing.py`, measured identical to theirs.

  Correcting this entry's earlier dependency claim: `shapely` is imported by
  **`basic_agent.py` alone** and nothing in the route-planning chain needs
  it, and while `agents/requirements.txt` does pin `numpy<2.0.0`, that pin is
  over-broad — the navigation code was run successfully on our numpy 2.5.1.
  Both halves of the original "vendoring is blocked" reasoning were wrong.
- **Reproducibility weakens under CARLA.** Synchronous mode with a fixed delta
  is approximately repeatable, not bit-identical. The bit-identical goal in
  the parallel-trials plan does not transfer.
- **Sub-tick arrival precision is a mock capability.** `arrival_time` is
  derived from the leftover budget in `SimulationRunner._process_movement`,
  so it is exact only because `MockBackend` can report a fractional
  `consumed`. CARLA observes arrivals at tick boundaries and will quantise
  them. (The pre-movement demand generation is the source of the small
  negative wait times, and it survived the refactor deliberately.)
- **The service window is not a hard guarantee.** The progress fallback drives
  to the nearest pending pickup when nothing can be scheduled, ignoring the
  window — **2.3%** of fulfilled requests at W750 with exact driving on map
  42, rising to 4.9% at σ=0.2. Pooled over three maps (2026-08-11) the same
  two figures are **3.41%** and **6.05%**. `safety_factor` is the only lever
  and it is a partial one: it removes the noise-driven part and leaves the
  ~3% floor untouched, which is why that floor is worth attributing. See the
  safety-factor sweep above and `window_insertion.md`.
- ~~**Real-time requirement is unverified.**~~ **Measured 2026-08-11 and
  satisfied**, but not in the way this entry predicted, and both halves of
  its arithmetic were wrong. The budget is **passenger dwell (~5 s)**, not
  the 50 ms tick — `BusAgent` plans only from `on_arrival`, so the bus is
  stationary throughout, and the tick would bind only under mid-leg
  re-planning, which was deliberately not built. And the cost is not
  ~30 ms/call: the *worst* call is **2.0 s** at the operating point. Both
  errors were large and they happened to cancel. See the section below.

---

## Environment notes

- `environment.yml` declares `name: carla-env`; `run.sh` and `jupyter-run.sh`
  use `ENV_NAME="autobus"`. Recreating the env from the file yields one the
  scripts cannot find.
- CARLA runs from `compose.yml` in the project root (image
  `carlasim/carla:0.9.16`, host networking, port 2000). Raise it manually.
- `tests/sanity_cehck_carla.py` connects, spawns a vehicle, enables autopilot
  for three seconds and destroys it. Use it to confirm the server is live.
