"""End-to-end sanity check: PlanarMapProvider + PlaceholderPolicy."""

import sys
import os

# ── Path bootstrap ───────────────────────────────────────────────────────────
# Add src/ so that source files using bare 'algorithm.X' imports can resolve
# their internal dependencies (pytest does this automatically via pytest.ini).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Source files are inconsistent: some import 'algorithm.X', others import
# 'src.algorithm.X'.  Without intervention Python creates two separate module
# objects for the same file, breaking Enum identity checks
# (e.g. `r.state is RequestState.PENDING` returns False even though the state
# was just set to PENDING, because the two modules have distinct RequestState
# classes).
#
# Fix: import modules bottom-up and register each under its 'src.*' alias
# before any file that uses 'from src.X import ...' is loaded.  Python's
# import system short-circuits on the first sys.modules hit, so subsequent
# 'from src.algorithm.RequestPool import ...' calls reuse our pre-loaded module.

import algorithm.RequestPool    # stdlib only — safe first
import algorithm.DispatchPolicy
import algorithm.BusAgent
import map.map_data
import map.MapProvider
import map.graph
import map.PlanarMapProvider
import map.TransitGraph


def _src(name: str) -> None:
    """Register sys.modules['src.<name>'] pointing at sys.modules['<name>']."""
    sys.modules.setdefault(f"src.{name}", sys.modules[name])


for _n in list(sys.modules):
    if any(_n == p or _n.startswith(p + ".") for p in ("algorithm", "map")):
        _src(_n)

# MetricsTracker imports src.algorithm.RequestPool — alias already in place.
import metrics.MetricsTracker
_src("metrics")
_src("metrics.MetricsTracker")

# DemandGenerator imports src.algorithm.RequestPool — alias already in place.
import simulation.DemandGenerator
_src("simulation")
_src("simulation.DemandGenerator")

# SimulationBackend imports src.algorithm.*, src.metrics.*, src.simulation.DemandGenerator
# — all three aliases are set above.
import simulation.SimulationBackend
_src("simulation.SimulationBackend")

# MockBackend and SimulationRunner use only bare (non-src) imports.
import simulation.MockBackend
import simulation.SimulationRunner

del _src, _n   # clean up bootstrap helpers

# ── Real imports ─────────────────────────────────────────────────────────────
from algorithm.BusAgent import BusAgent
from algorithm.DispatchPolicy import PlaceholderPolicy
from algorithm.RequestPool import RequestPool
from map.PlanarMapProvider import PlanarMapProvider
from map.TransitGraph import TransitGraph
from map.graph import PlanarStreetGraph
from metrics.MetricsTracker import MetricsTracker
from simulation.DemandGenerator import DemandGenerator, PeriodicPolicy
from simulation.MockBackend import MockBackend
from simulation.SimulationRunner import SimulationRunner


def main() -> None:
    """Wire up all components and run one simulation."""

    # 1. Shared request store — everything that needs to read or write
    #    passenger requests gets a reference to this same object.
    pool = RequestPool()

    # 2. Build the world map.  seed=42 makes the generated graph
    #    reproducible; stop_density=0.3 ensures enough stops exist for
    #    the demand generator (which requires ≥ 2).
    street_graph = PlanarStreetGraph.generate(
        n_seeds=20,
        bounds=(0.0, 0.0, 5000.0, 5000.0),
        seed=42,
        stop_density=0.3,
    )
    provider = PlanarMapProvider(street_graph)

    # 3. TransitGraph is the routing graph used by both the policy (to
    #    find paths) and MockBackend (to compute travel times).
    transit_graph = TransitGraph(provider)

    # 4. Collect the stop list once so it can be reused below.
    stops = provider.get_stops()

    # 5. Demand generator: one new request every 30 simulation-seconds,
    #    capped at 15 total.
    gen_policy = PeriodicPolicy(interval_s=30.0, max_requests=15)
    demand_gen = DemandGenerator(pool, gen_policy, stops)

    # 6. Seed the pool with at least one request before BusAgent is
    #    created.  BusAgent.__init__ immediately calls _replan(), which
    #    relies on pending requests to build a non-empty initial route.
    #    Without this, is_done() is True at the start and the runner
    #    loop exits before its first tick.
    demand_gen.update(0.0)

    # 7. Routing policy and bus agent.
    #    stops[0].id is now a valid TransitGraph node ID (edge ID "{u}_{v}"),
    #    so it can serve as the bus's starting position.
    policy = PlaceholderPolicy(transit_graph)
    agent = BusAgent(
        pool=pool,
        policy=policy,
        capacity=4,
        position=stops[0].id,
        n_stops=5,
        commitment=2,
    )

    # 8. Passive observers and the backend that ties the tick loop
    #    together.  MockBackend computes travel times via Dijkstra on
    #    the TransitGraph (as opposed to a CARLA physics simulation).
    metrics = MetricsTracker(pool)
    backend = MockBackend(
        agent=agent,
        demand_generator=demand_gen,
        metrics_tracker=metrics,
        pool=pool,
        provider=provider,
    )

    # 9. Drive the loop: 1-second ticks, 1-hour safety ceiling.
    runner = SimulationRunner(backend=backend, dt=1.0, max_time=3600.0)
    runner.run()


if __name__ == "__main__":
    main()
