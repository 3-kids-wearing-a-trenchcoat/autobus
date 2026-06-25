"""MockBackend: SimulationBackend for non-CARLA map sources."""

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import RequestPool
from src.map.MapProvider import MapProvider
from src.map.TransitGraph import TransitGraph
from src.metrics.MetricsTracker import MetricsTracker
from src.simulation.AnimationRecorder import AnimationRecorder
from src.simulation.DemandGenerator import DemandGenerator
from src.simulation.SimulationBackend import SimulationBackend


class MockBackend(SimulationBackend):
    """SimulationBackend backed by a MapProvider (Planar or Static map).

    Travel times are computed as the shortest-path cost through the
    TransitGraph built from the given provider.

    :param agent: The bus agent that manages routing and passenger state.
    :param demand_generator: Generates new passenger requests each tick.
    :param metrics_tracker: Passive observer that records per-tick snapshots.
    :param pool: Shared request pool.
    :param provider: Map source used to build the internal TransitGraph.
    :param recorder: Optional animation recorder. Defaults to None.
    """

    def __init__(
        self,
        agent: BusAgent,
        demand_generator: DemandGenerator,
        metrics_tracker: MetricsTracker,
        pool: RequestPool,
        provider: MapProvider,
        recorder: AnimationRecorder | None = None,
    ) -> None:
        # Must be set before super().__init__(), which calls _travel_time.
        self._transit_graph = TransitGraph(provider)
        super().__init__(
            agent, demand_generator, metrics_tracker, pool, recorder
        )

    def _travel_time(self, from_id: str, to_id: str) -> float:
        """Return shortest-path travel time in seconds between two node IDs."""
        return self._transit_graph.best_path_cost(from_id, to_id)
