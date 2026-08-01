"""Ordered pickup/dropoff schedule with dwell-aware timing."""

from typing import NamedTuple, Sequence

from src.algorithm.TravelModel import TravelModel


class Event(NamedTuple):
    """
    One thing the bus must do for one passenger at one stop.

    :param node_id: TransitGraph node ID where the event happens.
    :param load_delta: +1 when a passenger boards, -1 when one alights.
    :param owner_id: ID of the request this event serves, so a rider's
        pickup and dropoff can be paired up again later.
    """

    node_id: str
    load_delta: int
    owner_id: str


class Visit(NamedTuple):
    """
    One stop the bus actually pulls over at, serving one or more events.

    Several events at the same stop collapse into a single visit, because the
    bus stops once and everyone boards or alights together.

    :param node_id: TransitGraph node ID of the stop.
    :param event_indices: Indices into the schedule's events served here.
    :param n_boarding: Passengers boarding at this visit.
    :param n_alighting: Passengers alighting at this visit.
    """

    node_id: str
    event_indices: tuple[int, ...]
    n_boarding: int
    n_alighting: int


class Schedule:
    """
    An immutable, ordered list of pickup and dropoff events.

    The schedule is the policy's plan of record. It is deliberately immutable:
    an insertion search tries many candidate orderings, and returning a new
    schedule per candidate is much easier to reason about than undoing
    mutations on a shared one.

    Timing is computed against a ``TravelModel``, never against the graph
    directly, so the same schedule can be projected under optimistic or
    pessimistic estimates. Dwell is charged **once per visit**, not once per
    event — a stop where three passengers board is one pull-over.
    """

    def __init__(self, events: Sequence[Event] = ()) -> None:
        """
        :param events: Ordered events. Defaults to an empty schedule.
        """
        self._events: tuple[Event, ...] = tuple(events)

    # ------------------------------------------------------------------
    # Basic accessors
    # ------------------------------------------------------------------

    @property
    def events(self) -> tuple[Event, ...]:
        """The ordered events in this schedule."""
        return self._events

    def __len__(self) -> int:
        """Number of events (not visits)."""
        return len(self._events)

    def __bool__(self) -> bool:
        """True when the schedule holds at least one event."""
        return bool(self._events)

    def __eq__(self, other: object) -> bool:
        """Two schedules are equal when their event sequences match."""
        if not isinstance(other, Schedule):
            return NotImplemented
        return self._events == other._events

    def __repr__(self) -> str:
        """Developer-facing representation listing the events."""
        return f"Schedule({list(self._events)!r})"

    def pickup_owners(self) -> set[str]:
        """Request IDs whose pickup is still scheduled."""
        return {e.owner_id for e in self._events if e.load_delta > 0}

    def dropoff_owners(self) -> set[str]:
        """Request IDs whose dropoff is still scheduled."""
        return {e.owner_id for e in self._events if e.load_delta < 0}

    def owners(self) -> set[str]:
        """Every request ID this schedule serves in any way."""
        return {e.owner_id for e in self._events}

    # ------------------------------------------------------------------
    # Structural edits (each returns a new Schedule)
    # ------------------------------------------------------------------

    def insert(
        self, pickup: Event, dropoff: Event, pickup_at: int, dropoff_at: int
    ) -> "Schedule":
        """
        Return a copy with a pickup and its dropoff spliced in.

        Indices are positions in the *current* event list; the dropoff lands
        after the pickup, preserving passenger precedence.
        :param pickup: The boarding event to insert.
        :param dropoff: The alighting event to insert.
        :param pickup_at: Index the pickup is inserted before.
        :param dropoff_at: Index the dropoff is inserted before, counted in
            the original list and never earlier than ``pickup_at``.
        :return: A new Schedule containing both events.
        :raises ValueError: If dropoff_at precedes pickup_at.
        """
        if dropoff_at < pickup_at:
            raise ValueError(
                "dropoff_at must not precede pickup_at "
                f"(got {dropoff_at} < {pickup_at})"
            )
        events = self._events
        return Schedule(
            events[:pickup_at]
            + (pickup,)
            + events[pickup_at:dropoff_at]
            + (dropoff,)
            + events[dropoff_at:]
        )

    def without_owner(self, owner_id: str) -> "Schedule":
        """
        Return a copy with every event for one request removed.
        :param owner_id: Request ID to drop entirely.
        :return: A new Schedule without that request.
        """
        return Schedule(
            [e for e in self._events if e.owner_id != owner_id]
        )

    def without_pickup(self, owner_id: str) -> "Schedule":
        """
        Return a copy with one request's pickup removed, keeping its dropoff.

        Used when a passenger has boarded: the pickup is history, but the
        obligation to deliver them remains.
        :param owner_id: Request ID whose pickup is done.
        :return: A new Schedule without that pickup event.
        """
        return Schedule([
            e for e in self._events
            if not (e.owner_id == owner_id and e.load_delta > 0)
        ])

    # ------------------------------------------------------------------
    # Visits and timing
    # ------------------------------------------------------------------

    def grouped_visits(self) -> list[Visit]:
        """
        Collapse the events into the stops the bus actually pulls over at.

        Consecutive events sharing a stop become one visit. A leading visit at
        the bus's current position is still reported here, so callers can see
        those events are served without moving; ``stop_sequence`` is what
        drops them from the driving route.
        :return: Ordered visits, each naming the events it serves.
        """
        visits: list[Visit] = []
        for index, event in enumerate(self._events):
            boarding = 1 if event.load_delta > 0 else 0
            if visits and visits[-1].node_id == event.node_id:
                previous = visits[-1]
                visits[-1] = Visit(
                    previous.node_id,
                    previous.event_indices + (index,),
                    previous.n_boarding + boarding,
                    previous.n_alighting + (1 - boarding),
                )
            else:
                visits.append(
                    Visit(event.node_id, (index,), boarding, 1 - boarding)
                )
        return visits

    def stop_sequence(self, position: str) -> list[str]:
        """
        Return the stop IDs to drive to, in order.

        Consecutive duplicates are collapsed and a leading stop equal to
        ``position`` is dropped: the bus is already there, and a zero-length
        self-transition would stall it (see DispatchPolicy.PlaceholderPolicy).
        :param position: TransitGraph node ID the bus is currently at.
        :return: Ordered list of stop node IDs to visit.
        """
        visits = self.grouped_visits()
        if visits and visits[0].node_id == position:
            visits = visits[1:]
        return [visit.node_id for visit in visits]

    def arrival_times(
        self, position: str, start_time: float, model: TravelModel
    ) -> list[float]:
        """
        Project the arrival time at each event's stop.

        Walks the visits, charging one travel leg and one dwell per visit.
        Events sharing a visit share an arrival time, since the bus reaches
        them all at once. Events at ``position`` are served at
        ``start_time``: the bus is already parked there, so no leg is driven
        and no extra dwell is charged.
        :param position: TransitGraph node ID the bus is currently at.
        :param start_time: Simulation time at which the bus *departs*
            ``position``.
        :param model: Travel and dwell estimates to project with.
        :return: One arrival time per event, in event order.
        """
        arrivals: list[float] = [0.0] * len(self._events)
        clock = start_time
        previous_node = position

        for visit in self.grouped_visits():
            if visit.node_id == previous_node:
                # Already parked here — served now, without driving anywhere.
                arrival = clock
            else:
                arrival = clock + model.travel_time(
                    previous_node, visit.node_id
                )
                clock = arrival + model.dwell_time(
                    visit.node_id, visit.n_boarding, visit.n_alighting
                )
            for index in visit.event_indices:
                arrivals[index] = arrival
            previous_node = visit.node_id

        return arrivals

    def forward_slack(
        self, arrival_times: Sequence[float], deadlines: Sequence[float]
    ) -> list[float]:
        """
        Compute how much each event could slip without breaking a deadline.

        The slack at an event is the smallest spare time across that event and
        everything after it, so an insertion that delays the route by ``D``
        from some point on is feasible exactly when ``D`` does not exceed the
        slack there. One backward pass replaces re-simulating the whole tail
        for every candidate insertion, which is what keeps insertion search
        affordable as the schedule grows.
        :param arrival_times: Projected arrival time per event.
        :param deadlines: Latest acceptable arrival per event; use ``inf``
            for events that have no deadline.
        :return: One slack value per event, in event order.
        :raises ValueError: If the two sequences differ in length.
        """
        if len(arrival_times) != len(deadlines):
            raise ValueError(
                "arrival_times and deadlines must be the same length "
                f"(got {len(arrival_times)} and {len(deadlines)})"
            )
        slack: list[float] = [0.0] * len(arrival_times)
        running = float("inf")
        for index in reversed(range(len(arrival_times))):
            spare = deadlines[index] - arrival_times[index]
            running = min(running, spare)
            slack[index] = running
        return slack

    # ------------------------------------------------------------------
    # Feasibility
    # ------------------------------------------------------------------

    def capacity_ok(self, capacity_total: int, initial_load: int) -> bool:
        """
        Check that the bus never holds more passengers than it has seats.
        :param capacity_total: Total seat capacity of the bus.
        :param initial_load: Passengers already aboard before the first event.
        :return: True if the running load stays within capacity throughout.
        """
        load = initial_load
        for event in self._events:
            load += event.load_delta
            if load > capacity_total:
                return False
        return True

    def precedence_ok(self) -> bool:
        """
        Check that nobody is dropped off before being picked up.

        A rider whose pickup is absent is treated as already aboard, which is
        the normal state for onboard passengers.
        :return: True if every scheduled pickup precedes its own dropoff.
        """
        scheduled_pickups = self.pickup_owners()
        seen_pickup: set[str] = set()
        for event in self._events:
            if event.load_delta > 0:
                seen_pickup.add(event.owner_id)
            elif event.owner_id in scheduled_pickups:
                if event.owner_id not in seen_pickup:
                    return False
        return True

