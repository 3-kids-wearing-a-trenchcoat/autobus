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

1. [ ] **Spike: throughput and capability** (no production code)
   - Synchronous mode, fixed delta, no-rendering; measure sim-seconds per
     wall-second on the target town, with and without NPC traffic.
   - Confirm a bus blueprint exists in 0.9.16.
   - Resolve the `agents` package gap (see Risks).
   - Output: the one number every later step is sized against, and a
     CARLA-specific scaled-down `LOAD`.
2. [ ] **`CarlaMapProvider`**
   - `carla.Map.get_topology()` → `RoadNodeRecord` / `RoadEdgeRecord`; edge
     cost from segment length and speed limit.
   - Dump the map to a fixture once, then build and test the provider
     **offline** — otherwise every map test needs a live server.
3. [ ] **Bus stops** (its own discussion)
   - Working assumption: hand-authored and version-controlled, in the shape
     `StaticMapProvider.StopInput` already establishes.
   - Needs a small authoring aid (dump coordinates from a spectator camera).
   - Stops must be reachable and have a legal pull-over point.
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

## Open questions

- **Which town?** Affects stop-authoring effort most. Small (Town01/02) is the
  cheap default.
- **NPC traffic?** Empty is fastest and most reproducible; light traffic is
  more realistic and is what the execution-noise study models synthetically.
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
