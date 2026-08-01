"""Dispatch policy interface and bus state data model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.algorithm.RequestPool import Request
from src.map.TransitGraph import TransitGraph


@dataclass
class BusState:
    """
    Snapshot of the bus state at a point in time.

    :param position: TransitGraph node ID (road edge ID) of the bus position.
    :param onboard: Passengers currently on the bus (state = ON_BUS).
    :param pending_requests: Requests waiting at stops (state = PENDING).
    :param remaining_capacity: Number of additional passengers the bus can
        accept.
    :param now: Simulation time in seconds at which the bus *departs*
        ``position`` — not the time it arrived there. The distinction matters
        once dwell time is non-zero: a policy adds travel time to ``now`` to
        get the arrival time at its first planned stop, so anchoring on the
        arrival instead would under-count every leg by one dwell period.
        Defaults to 0.0 for policies that do not reason about time.
    """

    position: str
    onboard: list[Request]
    pending_requests: list[Request]
    remaining_capacity: int
    now: float = 0.0


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

    def _simulate_stop(
        self,
        stop_id: str,
        sim_onboard: list[Request],
        sim_pending: list[Request],
        sim_remaining_capacity: int,
    ) -> tuple[list[Request], list[Request], int]:
        """
        Advance the simulation state after visiting a stop.

        Passengers whose dropoff matches stop_id alight first (freeing
        capacity), then pending passengers whose pickup matches stop_id
        board up to the available capacity.
        :param stop_id: The stop being visited.
        :param sim_onboard: Simulated onboard passengers before the stop.
        :param sim_pending: Simulated pending passengers before the stop.
        :param sim_remaining_capacity: Remaining capacity before the stop.
        :return: Updated (sim_onboard, sim_pending, sim_remaining_capacity).
        """
        # Alight
        alighting = [r for r in sim_onboard if r.dropoff_stop_id == stop_id]
        sim_onboard = [
            r for r in sim_onboard if r.dropoff_stop_id != stop_id
        ]
        sim_remaining_capacity += len(alighting)

        # Board
        boarding: list[Request] = []
        remaining_pending: list[Request] = []
        for r in sim_pending:
            if (r.pickup_stop_id == stop_id
                    and sim_remaining_capacity > 0):
                boarding.append(r)
                sim_remaining_capacity -= 1
            else:
                remaining_pending.append(r)

        return (
            sim_onboard + boarding,
            remaining_pending,
            sim_remaining_capacity,
        )


class PlaceholderPolicy(DispatchPolicy):
    """placeholder policy, just so I can continue development without implementing a proper policy first"""

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        if not state.onboard and not state.pending_requests:
            return []
        if state.onboard:
            target = state.onboard[0].dropoff_stop_id
        else:
            target = state.pending_requests[0].pickup_stop_id
        path = self._graph.best_path(state.position, target)
        # Exclude the origin: BusAgent already knows where it is, and including
        # it creates a zero-length self-transition that stalls _process_movement.
        return path[1:] if len(path) > 1 else path