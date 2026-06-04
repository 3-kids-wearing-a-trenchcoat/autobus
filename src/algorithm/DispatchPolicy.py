"""Dispatch policy interface and bus state data model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from algorithm.RequestPool import Request
from map.TransitGraph import TransitGraph


@dataclass
class BusState:
    """
    Snapshot of the bus state at a point in time.

    :param position: TransitGraph node ID (road edge ID) of the bus position.
    :param onboard: Passengers currently on the bus (state = ON_BUS).
    :param pending_requests: Requests waiting at stops (state = PENDING).
    :param remaining_capacity: Number of additional passengers the bus can
        accept.
    """

    position: str
    onboard: list[Request]
    pending_requests: list[Request]
    remaining_capacity: int


class DispatchPolicy(ABC):
    """Abstract base class for bus dispatch routing policies."""

    def __init__(self, graph: TransitGraph) -> None:
        """
        :param graph: Transit graph representing the road network.
        """
        self._graph = graph

    @abstractmethod
    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Compute a route from the given bus state.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Number of stops to include in the route.
        :return: Ordered list of stop IDs to visit.
        """

class PlaceholderPolicy(DispatchPolicy):
    """placeholder policy, just so I can continue development without implementing a proper policy first"""

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        if state.onboard:
            return self._graph.best_path(state.position, state.onboard[0].dropoff_stop_id)
        return self._graph.best_path(state.position, state.pending_requests[0].pickup_stop_id)