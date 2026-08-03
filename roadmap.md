# Autobus roadmap

Living document. Phase 1 is complete; Phase 2 is the current work.
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

## Phase 2 — Restore backend modularity (**current**)

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

1. [ ] **Freeze current behaviour with a golden master**
   - `notebooks/golden-master.ipynb` + `notebooks/golden_master.py`
   - Captures full KPIs, `wait_times`, `ride_times`, tick counts and arrival
     sequences across scenarios chosen to cover the code paths the refactor
     touches.
   - Every later step is checked by re-running it and demanding
     **bit-identical** output.
2. [ ] **Settle the adapter contract on paper** (no code)
   - Lifecycle: `start()` / `close()`
   - Clock: `now()`
   - Command: `set_destination(stop_id)`
   - Observation: `advance(dt) -> (arrival | None, time_consumed)`
   - Position: `current_location()`
   - Passengers: `sync_passengers(pool)`
   - Sign-off needed here before any code: the `advance` semantics determine
     everything downstream.
3. [ ] **Move orchestration out of the backend into `SimulationRunner`**
   - Tick order, demand generation, `_expire_pending`, `record_tick`,
     animation recording — none of it is world-specific.
   - Strangler-style: build the new sequence alongside the old `step()`, then
     switch over once the golden master agrees.
   - `tests/test_simulation_backend.py` holds **22 tests** exercising `step()`
     orchestration directly; they move to the runner without changing
     meaning. `tests/test_mock_backend.py` has 1 and should stay put.
   - **Concrete surface to rehome.** `SimulationRunner.run()` is only a while
     loop today, and it reaches through the backend for three things:
     `backend.is_done()`, `backend.sim_time`, and `backend.metrics` — the
     runner gets the `MetricsTracker` *via the backend*. All three move: the
     runner should hold the pool and the tracker directly, and own the clock.
   - **`BusAgent` needs no changes.** Its entire public surface is
     `on_arrival(stop_id, sim_time)`, the `position` property, and
     `next_stop()` — exactly the three things the runner needs to mediate
     between agent and backend.
   - **Preserve the tick order exactly, including its wart.** Demand is
     generated *before* movement is processed, so a request created this tick
     can be boarded at an `arrival_time` computed to fall slightly earlier —
     which is where the small negative wait times come from (0.05% of
     requests, worst −0.49 s, bounded by `dt`). Do not "fix" this during the
     refactor; it would show up as a golden-master failure and confuse the
     signal. Fix it separately, afterwards, if at all.
4. [ ] **Reduce `MockBackend` to a pure adapter**
   - Keeps only dead reckoning (`_time_to_next_stop`, `_elapsed_toward_next`,
     travel model) as its own private implementation.
   - Remove the construction-order trap: `SimulationBackend.__init__` calls
     `_compute_next_travel_time()` → the abstract `_travel_time`, forcing
     `MockBackend` to set `_travel_model` before `super().__init__()`. CARLA
     cannot compute an initial leg at construction time (no vehicle yet);
     that work belongs in `start()`.
5. [ ] **Add the hooks CARLA needs, as no-ops in the mock**
   - `start()` / `close()` and `sync_passengers(pool)`.
   - `sync_passengers` taking the pool and diffing it keeps the algorithm
     layer untouched — no observer hooks on `RequestPool`.
6. [ ] **Un-hardwire the experiment harness**
   - `run_trial` gains a **backend factory** parameter defaulting to the mock,
     mirroring the existing `policy_factory`.
   - Stop reaching into `backend._transit_graph` for animation
     (`src/experiment/trial.py`); the graph is built in `run_trial` anyway.
   - This is the step that actually delivers the goal.
7. [ ] **Delete the old seam**
   - Remove `_travel_time` as the abstract method and the orchestration
     remnants. Golden master must still match.
8. [ ] **Prove modularity with a scripted backend**
   - A test-only backend with no graph, no travel model, no geometry, that
     replays a hand-written list of "at t=10 the bus arrives at stop A".
   - If a full simulation runs against it and produces the expected KPIs,
     the project demonstrably does not care where the world comes from.
   - Doubles as the cheapest stand-in for CARLA while the container is down.

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

- **`advance()` cannot return a batch of arrivals.** Today's
  `_process_movement` loops within one tick, and each arrival calls
  `agent.on_arrival(...)` *before* the next leg is computed — arrivals inside
  a tick are sequentially dependent on the agent re-planning. The contract
  must be "advance until `dt` is exhausted **or** one arrival occurs".
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
  computed by subtraction in `_process_movement`; CARLA observes arrivals at
  tick boundaries. (Also the source of the small negative wait times.)
- **The service window is not a hard guarantee.** The progress fallback drives
  to the nearest pending pickup when nothing can be scheduled, ignoring the
  window — roughly 1–2% of fulfilled requests at W750 with exact driving.
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
