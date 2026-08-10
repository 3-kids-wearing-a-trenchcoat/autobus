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

    @abstractmethod
    def params(self) -> dict[str, object]:
        """
        Report the settings this policy was constructed with.

        **Abstract, deliberately unlike ``stats()``.** Diagnostics are
        optional because most components have nothing to report; parameters
        are not, because the results store hashes them into a run's identity.
        A policy that silently reported none would be indistinguishable from
        a differently configured sibling, and two runs sharing an identity is
        data corruption rather than a missing feature. Requiring it here means
        a new policy cannot be added without deciding what identifies it.

        **Report the arguments as given, not as resolved.** A service window
        named ``max_wait_s=750`` and one derived from ``wait_factor=3`` may
        come to the same number on one map and not on another. Recording what
        was asked for keeps identity predictable, and errs toward recording
        two equivalent runs rather than merging two different ones — the
        cheap mistake rather than the expensive one.

        Values must be JSON-serialisable. An argument that is an object
        rather than a scalar is reported by class name, which identifies the
        kind of thing but not its own settings.
        :return: Parameter name to value. Subclasses extend rather than
            replace: ``{**super().params(), "n_passes": self._n_passes}``.
        """
        return {}

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

    def params(self) -> dict[str, object]:
        """Report no settings; this policy has none to vary."""
        return super().params()

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