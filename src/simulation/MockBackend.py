"""MockBackend: SimulationBackend for non-CARLA map sources."""

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import RequestPool
from src.algorithm.TravelModel import GraphTravelModel
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
    :param max_wait_s: Maximum PENDING wait before abandonment; forwarded
        to SimulationBackend. None disables abandonment. Defaults to None.
    :param dwell_s: Seconds spent stopped at each visited stop; forwarded to
        SimulationBackend. Ignored when ``travel_model`` is supplied, which
        carries its own dwell. Defaults to 0.0.
    :param travel_model: Travel/dwell estimates to use. When None, one is
        built from ``provider``. Passing the *same* model the policy plans
        against makes the mock simulation's exactness structural rather than
        coincidental, and avoids building a second TransitGraph.
    """

    def __init__(
        self,
        agent: BusAgent,
        demand_generator: DemandGenerator,
        metrics_tracker: MetricsTracker,
        pool: RequestPool,
        provider: MapProvider,
        recorder: AnimationRecorder | None = None,
        max_wait_s: float | None = None,
        dwell_s: float = 0.0,
        travel_model: GraphTravelModel | None = None,
    ) -> None:
        # Must be set before super().__init__(), which calls _travel_time.
        if travel_model is None:
            travel_model = GraphTravelModel(
                TransitGraph(provider), dwell_s=dwell_s
            )
        else:
            dwell_s = travel_model.dwell_s
        self._travel_model = travel_model
        # Retained under its original name: callers reach for it to render
        # animations (see src/experiment/trial.py).
        self._transit_graph = travel_model.graph
        super().__init__(
            agent, demand_generator, metrics_tracker, pool, recorder,
            max_wait_s=max_wait_s, dwell_s=dwell_s,
        )

    def _travel_time(self, from_id: str, to_id: str) -> float:
        """Return shortest-path travel time in seconds between two node IDs."""
        return self._travel_model.travel_time(from_id, to_id)
