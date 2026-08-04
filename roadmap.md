# Autobus roadmap

Living document. Phases 1 and 2 are complete; Phase 3 (CARLA) is next.
Last updated 2026-08-03.

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

   **The bus blueprint does not fit Town01.** `vehicle.mitsubishi.fusorosa`
   is the only `base_type=Bus` in 0.9.16 and is **3.9 m wide against 3.5 m
   lanes**. On a fixed three-stop route it stalled on 85% of ticks and
   completed **0/3** legs, while a Sprinter, an ambulance, a truck and a car
   all completed 2/3 and drove ~1535 m. Default is now
   `vehicle.mercedes.sprinter` (5.9 x 2.0 m, a minibus); `FULL_SIZE_BUS_ID`
   is kept for a town with wider lanes. **Open decision** — see below.

   **A wedged vehicle never recovers on its own.** Measured: the bus drove
   four legs, jammed against kerb geometry, and reported *zero movement for
   every one of the ten remaining legs*. So the backend watches for stalls
   and re-issues the path, then as a last resort lifts the vehicle onto the
   nearest lane waypoint. Both are counted in `stats()`, because unwedging
   is not physics and a run that needs it often is telling you the vehicle
   is too big for the town. With recovery: **3/15 legs to 11/15, and zero
   stuck**.

   Trap worth keeping: the two stall clocks must be independent. Decrementing
   the stall total on each repath starved the unwedge threshold outright —
   repath fired every 10 s forever, unwedge never once, and the results were
   byte-identical to having no recovery at all.

   Remaining gap: 4 of 15 legs still time out, but with the bus **driving
   the whole time** and approaching to 58–97 m rather than the 6 m arrival
   radius. That is the traffic manager routing near a stop it will not turn
   around for, not a stall — a different and milder problem than the one
   fixed here.
5. [ ] **Passengers as walkers**
   - `RequestPool` stays authoritative; `sync_passengers` mirrors pool state
     onto walker actors. Spawn at pickup stop on creation, destroy on
     boarding.
   - Pitfalls: navigable spawn points, batch destroy in synchronous mode,
     walkers stand still without an AI controller (which is what we want).
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
| How is the bus steered? | `traffic_manager.set_path` | Works, no new dependencies, keeps CARLA's own driving in charge per design review §4. Vendoring `agents` is the fallback only |
| `fixed_delta_seconds` | **0.05**, never above 0.1 | Above `max_substep_delta_time × max_substeps` the physics silently stops keeping up; at 0.5 vehicles cover half the ground per simulated second |
| Where do bus stops come from? | Hand-authored per town, an input to the provider | A real deployment names its maps and stops rather than generating them |

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
- **The `agents` package is not installed.** The pip `carla==0.9.16` wheel is
  client-only (`__init__.py`, `libcarla.so`, stubs). `BasicAgent`,
  `BehaviorAgent` and `GlobalRoutePlanner` live in `PythonAPI/carla/agents/`
  inside the container image. Either vendor it or write our own controller.
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
