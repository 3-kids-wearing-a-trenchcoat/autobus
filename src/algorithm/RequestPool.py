"""Passenger request data model and pool."""

from dataclasses import dataclass
from enum import Enum


class RequestState(Enum):
    """Lifecycle state of a passenger request."""

    PENDING = "pending"       # waiting at pickup stop
    ON_BUS = "on_bus"         # boarded, riding to destination
    FULFILLED = "fulfilled"   # dropped off at destination
    CANCELLED = "cancelled"   # passenger gave up waiting


_ACTIVE_STATES = {RequestState.PENDING, RequestState.ON_BUS}


@dataclass
class Request:
    """
    A single passenger request.
    :param id: Unique request identifier.
    :param pickup_stop_id: ID of the stop where the passenger waits.
    :param dropoff_stop_id: ID of the stop where the passenger wants to go.
    :param created_at: Simulation time in seconds when the request was
        submitted. Supplied by the caller; no default.
    :param state: Current lifecycle state (defaults to PENDING).
    :param boarded_at: Simulation time when the passenger boarded.
        None until the request transitions to ON_BUS.
    :param fulfilled_at: Simulation time when the passenger alighted.
        None until the request transitions to FULFILLED.
    """

    id: str
    pickup_stop_id: str
    dropoff_stop_id: str
    created_at: float
    state: RequestState = RequestState.PENDING
    boarded_at: float | None = None
    fulfilled_at: float | None = None


class RequestPool:
    """
    Stores all passenger requests, active and historical.

    Active requests (PENDING or ON_BUS) are what the routing algorithm
    works with. Fulfilled and cancelled requests are retained so that
    MetricsTracker can access history later.
    """

    def __init__(self) -> None:
        self._requests: dict[str, Request] = {}

    def add(self, request: Request) -> None:
        """
        Add a new request to the pool.
        :param request: The request to add.
        :raises ValueError: If a request with the same ID already exists.
        """
        if request.id in self._requests:
            raise ValueError(
                f"Request with id '{request.id}' already exists."
            )
        self._requests[request.id] = request

    def get(self, request_id: str) -> Request:
        """
        Retrieve a request by ID.
        :param request_id: The ID of the request to retrieve.
        :return: The matching Request.
        :raises KeyError: If no request with that ID exists.
        """
        return self._requests[request_id]

    def get_all(self) -> list[Request]:
        """Return all active requests (PENDING and ON_BUS)."""
        return [
            r for r in self._requests.values()
            if r.state in _ACTIVE_STATES
        ]

    def get_pending(self) -> list[Request]:
        """Return requests where the passenger is still waiting at a stop."""
        return [
            r for r in self._requests.values()
            if r.state is RequestState.PENDING
        ]

    def get_on_bus(self) -> list[Request]:
        """Return requests where the passenger has already boarded."""
        return [
            r for r in self._requests.values()
            if r.state is RequestState.ON_BUS
        ]

    def get_fulfilled(self) -> list[Request]:
        """Return all requests that have been fulfilled."""
        return [
            r for r in self._requests.values()
            if r.state is RequestState.FULFILLED
        ]

    def get_cancelled(self) -> list[Request]:
        """Return all requests that have been cancelled."""
        return [
            r for r in self._requests.values()
            if r.state is RequestState.CANCELLED
        ]

    def update_state(
        self, request_id: str, new_state: RequestState
    ) -> None:
        """
        Transition a request to a new state.
        :param request_id: ID of the request to update.
        :param new_state: The state to transition to.
        :raises KeyError: If no request with that ID exists.
        """
        self._requests[request_id].state = new_state

    def cancel(self, request_id: str) -> None:
        """
        Mark a request as cancelled.
        :param request_id: ID of the request to cancel.
        :raises KeyError: If no request with that ID exists.
        """
        self.update_state(request_id, RequestState.CANCELLED)

    def __len__(self) -> int:
        """Return the number of active requests (PENDING and ON_BUS)."""
        return sum(
            1 for r in self._requests.values()
            if r.state in _ACTIVE_STATES
        )
