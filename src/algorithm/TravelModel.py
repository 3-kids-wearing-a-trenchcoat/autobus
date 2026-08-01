"""Travel-time and dwell-time estimates used when projecting a route."""

from abc import ABC, abstractmethod

from src.map.TransitGraph import TransitGraph


class TravelModel(ABC):
    """
    Abstract estimate of how long driving and stopping take.

    This is the *planning* view of the world — what a policy believes will
    happen — and is deliberately distinct from the simulation's ground truth
    (``SimulationBackend._travel_time``, which subclasses supply). In the mock
    simulation the two coincide exactly, so a projected schedule is not an
    approximation: it is precisely what the simulation will execute. Under
    CARLA they will diverge, because traffic makes real driving times
    unpredictable.

    Keeping the two apart means a policy can never silently mistake its own
    projections for facts. A policy must re-anchor on the real position, clock
    and passenger set every time it is asked to plan.
    """

    @abstractmethod
    def travel_time(self, from_id: str, to_id: str) -> float:
        """
        Estimate the driving time between two TransitGraph nodes.
        :param from_id: TransitGraph node ID of the origin.
        :param to_id: TransitGraph node ID of the destination.
        :return: Estimated travel time in seconds.
        """

    @abstractmethod
    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """
        Estimate how long the bus is held at a stop.

        The passenger counts are part of the signature so that a future model
        can charge boarding and alighting time per passenger; the current
        implementation ignores them.
        :param stop_id: TransitGraph node ID of the stop being visited.
        :param n_boarding: Passengers expected to board at this stop.
        :param n_alighting: Passengers expected to alight at this stop.
        :return: Estimated dwell time in seconds.
        """


class GraphTravelModel(TravelModel):
    """
    Travel estimates read straight off the TransitGraph, with constant dwell.

    Travel time is the shortest-path cost through the graph, whose edge
    weights are already expressed in seconds. Dwell is a single constant
    charged once per stop *visit*, independent of how many passengers board or
    alight.
    """

    def __init__(self, graph: TransitGraph, dwell_s: float = 0.0) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param dwell_s: Seconds the bus is held at each stop it visits.
            Defaults to 0.0, which reproduces the original dwell-free
            behaviour exactly.
        """
        self._graph = graph
        self._dwell_s = dwell_s

    @property
    def graph(self) -> TransitGraph:
        """The transit graph these estimates are read from."""
        return self._graph

    @property
    def dwell_s(self) -> float:
        """Constant dwell time in seconds charged per stop visit."""
        return self._dwell_s

    def travel_time(self, from_id: str, to_id: str) -> float:
        """
        Return the shortest-path travel time between two nodes.
        :param from_id: TransitGraph node ID of the origin.
        :param to_id: TransitGraph node ID of the destination.
        :return: Travel time in seconds.
        """
        return self._graph.best_path_cost(from_id, to_id)

    def dwell_time(
        self, stop_id: str, n_boarding: int, n_alighting: int
    ) -> float:
        """
        Return the constant dwell time, ignoring the passenger counts.
        :param stop_id: TransitGraph node ID of the stop being visited.
        :param n_boarding: Ignored.
        :param n_alighting: Ignored.
        :return: Dwell time in seconds.
        """
        return self._dwell_s
