# Autobus roadmap

Living document. Phases 1 and 2 are complete; Phase 3 (CARLA) is next.
Last updated 2026-08-05.

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

   **Steering: `traffic_manager.set_path(actor, [locations])` works**, and
   keeps CARLA's built-in driving in charge as design review §4 intended. One
   bus completed a six-leg tour of Town01 with the path re-issued per leg;
   3/6 legs landed within 6 m, two of the misses stopping 32–54 m short.
   Expected to improve now that targets are authored stops rather than
   arbitrary spawn points. **Decision: use `set_path`; revisit only if it
   stays unreliable.**

   **The `agents` package** is confirmed absent from the wheel and present in
   the image at `/workspace/PythonAPI/carla/agents` (2262 LOC). Vendoring it
   would need **shapely** (not in `environment.yml`) and its `requirements.txt`
   pins `numpy<2.0.0` against our 2.5.1. Not needed unless `set_path` fails.
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
4. [ ] **The bus, no passengers** — the walking skeleton
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

   | blueprint | legs reached | unwedges | repaths | per tour |
   |---|---|---|---|---|
   | `vehicle.mercedes.sprinter` | 20/25 (80%) | 23 | 132 | 5,2,5,5,3 |
   | `vehicle.volkswagen.t2_2021` | 19/25 (76%) | 21 | 127 | 5,2,5,5,2 |
   | `vehicle.mitsubishi.fusorosa` | 17/25 (68%) | 33 | 182 | 5,2,4,4,2 |

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

   **A wedged vehicle never recovers on its own.** Measured: the bus drove
   four legs, jammed against kerb geometry, and reported *zero movement for
   every one of the ten remaining legs*. So the backend watches for stalls
   and re-issues the path, then as a last resort lifts the vehicle onto the
   nearest lane waypoint. Both are counted in `stats()`, because unwedging
   is not physics and a run that needs it often is telling you the vehicle
   is too big for the town. With recovery: **3/15 legs to 11/15, and zero
   stuck**.

   Those two figures were measured before the traffic-manager fix and
   overstate how badly a run goes without recovery.

   **Corrected again 2026-08-05: with the default vehicle the recovery is
   now never needed at all.** Almost every firing was a false positive on a
   red light — see the trap below. Once that was fixed, five six-stop tours
   fired **one repath and zero unwedges**, against 124 and 20 before. The
   `t2_2021` bus does not wedge in Town01.

   The full-size `fusorosa` still does: the same five tours need **12
   unwedges**, and this time they are earned — 10 stopped in a junction, one
   on a driving lane, one genuinely off-road. So the original width concern
   survives after all, in a sharper form than it was first stated: the wide
   bus wedges in Town01's junctions, and the minibuses do not.

   **Unwedging is therefore off by default** (`unwedge_after_s=0.0`), which
   is what the submitted version should run. It teleports, and a leg reached
   by teleporting is a KPI that did not happen — better to let a genuine
   wedge show up as a failed leg than to hide it. Repath stays on: it only
   re-issues a destination and moves nothing. The parameter remains for
   anyone driving the full-size bus.

   Trap worth keeping: the two stall clocks must be independent. Decrementing
   the stall total on each repath starved the unwedge threshold outright —
   repath fired every 10 s forever, unwedge never once, and the results were
   byte-identical to having no recovery at all.

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
6. [ ] **Campaign harness**
   - Trial lifecycle with a world reload between trials — actor leakage is the
     classic cross-trial contaminant.
   - Per-trial result persistence so a stopped or crashed campaign resumes.
     The *seam* belongs here even if the storage is crude; retrofitting it
     means re-running everything already collected.
7. [ ] **Validation**
   - Reproduce a mock finding qualitatively (design review §5.1 already
     frames CARLA as demonstration, with the synthetic map as the fast test
     bed).

---

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
| `safety_factor` | Default 1.0, but it is the CARLA lever | At σ=0.2, 1.25 cuts breaches 4.9%→2.0% free of coverage cost, for +107 s ride (L0) / +67 s (L1) |
| Which CARLA town? | **Town01** for now, but nothing may hard-code it — several maps are wanted eventually | Town choice does not affect throughput; Town01 drives a bus well, Town10HD_Opt barely moves one even on plain autopilot |
| How is the bus steered? | `traffic_manager.set_route`, given turns from our own route planner in `src/simulation/carla_routing.py` | `set_path` with a single destination is not navigation — it is a junction-decision API, and given one distant point the bus cycles. Planning the route and handing over the turns took a five-tour campaign from 17/25 legs to 21/25. See the trap below |
| Why not CARLA's `GlobalRoutePlanner`? | Measured identical, and ours is 60 lines against 1,240 | The pip wheel is client bindings only and does not ship `agents`, so either way the code would be copied into this repository. Ours reproduces theirs exactly — same 21/25, same per-tour split, same four failures to the metre — with no vendoring and no `shapely` |
| `fixed_delta_seconds` | **0.05**, never above 0.1 | Above `max_substep_delta_time × max_substeps` the physics silently stops keeping up; at 0.5 vehicles cover half the ground per simulated second |
| Where do bus stops come from? | Hand-authored per town, an input to the provider | A real deployment names its maps and stops rather than generating them |
| Does running faster cause vehicles to get stuck? | **No.** Wall-clock rate has no effect at all, and a finer physics step does not help either | Measured; see below |
| Who owns the traffic manager? | `CarlaBackend` creates its own on its own port, after the town is loaded, and shuts down only what it created | A traffic manager caches its map at creation and never re-caches it, so an inherited one steers the bus into the scenery — but it is also hosted by whichever *process* created it, so sharing or tearing down someone else's is worse. See the two traps below |
| Unwedging (lifting a jammed bus onto the lane) | **Off by default** — `unwedge_after_s=0.0`. Repath stays on | It teleports, and a leg saved that way is a KPI that did not happen. With the default bus it never fires anyway (0 in five tours). Enable it only for a vehicle that genuinely wedges, such as the full-size bus |
| Which vehicle drives? | `vehicle.volkswagen.t2_2021`, a shuttle | Re-measured over 5 tours: indistinguishable from the Sprinter. The full-size bus is usable too — the earlier "it cannot drive Town01" verdict was a stale-traffic-manager artefact |

## Open questions

- **NPC traffic?** Empty is fastest and most reproducible; light traffic is
  more realistic and is what the execution-noise study models synthetically.
  20 vehicles costs 4× throughput (50×→13×) and still leaves headroom.
- **Which reading of CARLA's role governs?** §3.2 says runs "will be done on
  the CARLA server as well" at the same statistical bar (CI half-width < 5% of
  mean); §5.1 says CARLA is for demonstrations. These conflict, and the
  difference is a demo versus a campaign harness.
- **Mid-route replanning.** Functional requirement 3 is *"the bus receives
  requests in real time (while potentially already in motion)"*. Today
  `on_arrival` is the only replan trigger. In the mock legs are short and this
  is nearly invisible; under CARLA a leg can run minutes.

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

  The stalls are geometric — the vehicle physically wedges against kerbs.

  **Caveat added 2026-08-05:** these runs predate the traffic-manager fix,
  so the absolute rates (3/6 legs, 44% stalled) are not trustworthy. The
  *comparison* still stands, because all three arms shared whatever state
  the traffic manager was in, and a 10x spread in wall rate moved nothing.
  Do not quote the absolute numbers; re-measure if they matter.
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
  window — **2.3%** of fulfilled requests at W750 with exact driving, rising
  to 4.9% at σ=0.2. `safety_factor` is the lever; see `window_insertion.md`.
- **Real-time requirement is unverified.** Design review §1.1.2 requires
  routing to be fast enough that the bus never waits. We have only measured
  wall-clock per *trial*, never latency per *plan call*. Rough arithmetic puts
  L1 at ~30 ms/call at W750 against a 50 ms tick — fits, but the margin is
  thin at wider windows.

---

## Environment notes

- `environment.yml` declares `name: carla-env`; `run.sh` and `jupyter-run.sh`
  use `ENV_NAME="autobus"`. Recreating the env from the file yields one the
  scripts cannot find.
- CARLA runs from `compose.yml` in the project root (image
  `carlasim/carla:0.9.16`, host networking, port 2000). Raise it manually.
- `tests/sanity_cehck_carla.py` connects, spawns a vehicle, enables autopilot
  for three seconds and destroys it. Use it to confirm the server is live.
