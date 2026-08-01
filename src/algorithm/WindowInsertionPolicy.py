"""Insertion policy that keeps a persistent schedule across planning calls."""

from src.algorithm.DispatchPolicy import BusState, DispatchPolicy
from src.algorithm.RequestPool import Request
from src.algorithm.Schedule import Event, Schedule
from src.algorithm.TravelModel import GraphTravelModel, TravelModel
from src.map.TransitGraph import TransitGraph


class WindowInsertionPolicy(DispatchPolicy):
    """
    A demand-responsive policy that grows one long-lived schedule by
    cheapest insertion, scored purely on passenger time.

    Unlike every other policy here, this one is **stateful**: it keeps its
    schedule between ``plan`` calls instead of rebuilding a route from
    scratch. That is what the dial-a-ride insertion literature actually
    describes, and it means an accepted request keeps the slot it was given
    rather than being re-derived (and possibly re-ordered) every arrival.

    Statefulness has a price. The simulation moves on without asking the
    policy: passengers abandon their requests, and ``BusAgent`` boards anyone
    waiting at a stop the bus reaches — including riders this policy never
    scheduled. So the schedule is **reconciled against reality** at the top of
    every ``plan`` call rather than trusted. Nothing is added to
    ``DispatchPolicy`` for this, because the ``BusState`` snapshot already
    says everything needed: who is aboard, who is still waiting, where the bus
    is and what time it is.

    Cost is the sum of every affected rider's wait plus ride time. That is
    exactly what ``MetricsTracker`` reports — ``boarded_at`` and
    ``fulfilled_at`` are both stamped from stop-arrival times — so the policy
    optimises the reported KPI directly rather than a proxy for it.

    Requests whose best insertion is infeasible are **rejected**: they are
    left out of the schedule and stay PENDING (the soft-skip model), free to
    be picked up by a later plan.
    """

    def __init__(
        self,
        graph: TransitGraph,
        travel_model: TravelModel | None = None,
    ) -> None:
        """
        :param graph: Transit graph representing the road network.
        :param travel_model: How the policy estimates travel and dwell times.
            Defaults to reading the graph with zero dwell. Supply one
            explicitly whenever the simulation runs with non-zero dwell, so
            the policy's projections match what the bus will actually do.
        """
        super().__init__(graph)
        self._travel_model = (
            travel_model if travel_model is not None
            else GraphTravelModel(graph)
        )
        self._schedule = Schedule()

    @property
    def schedule(self) -> Schedule:
        """The current persistent schedule (exposed for inspection/tests)."""
        return self._schedule

    def plan(self, state: BusState, n_stops: int) -> list[str]:
        """
        Reconcile the schedule against reality, then insert new requests.
        :param state: Current (or projected) bus state snapshot.
        :param n_stops: Maximum number of stops to include in the route.
        :return: Ordered list of stop IDs to visit; may be shorter than
            n_stops when demand is exhausted or every request was rejected.
        """
        if n_stops == 0:
            return []

        self._schedule = self._reconcile(self._schedule, state)
        for request in self._new_requests(state):
            inserted = self._best_insertion(self._schedule, request, state)
            if inserted is not None:
                self._schedule = inserted  # otherwise the request is rejected

        route = self._schedule.stop_sequence(state.position)[:n_stops]
        if not route:
            route = self._progress_fallback(state)[:n_stops]
        return route

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _reconcile(self, schedule: Schedule, state: BusState) -> Schedule:
        """
        Bring the persistent schedule back in line with what really happened.

        Three things drift between planning calls, all of them driven by
        components that never consult this policy:

        1. Requests leave the system — fulfilled, or abandoned by an
           impatient passenger — while still holding slots in the schedule.
        2. Riders board, making their scheduled pickup history while leaving
           the obligation to deliver them outstanding.
        3. ``BusAgent`` boards passengers opportunistically, so someone this
           policy deliberately rejected can end up aboard anyway. Their
           dropoff has to be adopted as a commitment.
        :param schedule: The schedule as of the previous planning call.
        :param state: The state snapshot describing what is actually true.
        :return: A schedule consistent with the snapshot.
        """
        onboard_ids = {request.id for request in state.onboard}
        pending_ids = {request.id for request in state.pending_requests}

        # 1. Gone from the pool entirely: fulfilled or cancelled.
        for owner_id in schedule.owners() - onboard_ids - pending_ids:
            schedule = schedule.without_owner(owner_id)

        # 2. Boarded since the last plan: the pickup is done.
        for owner_id in schedule.pickup_owners() & onboard_ids:
            schedule = schedule.without_pickup(owner_id)

        # 3. Aboard without ever being scheduled.
        for request in state.onboard:
            if request.id not in schedule.dropoff_owners():
                schedule = self._adopt_dropoff(schedule, request, state)

        return schedule

    def _adopt_dropoff(
        self, schedule: Schedule, request: Request, state: BusState
    ) -> Schedule:
        """
        Splice in a dropoff for a rider who boarded without being scheduled.

        This is not optional and has no feasibility test: the passenger is
        already on the bus, and dropping them off only frees a seat. The only
        question is where it hurts least.
        :param schedule: Schedule to add the obligation to.
        :param request: The unscheduled onboard request.
        :param state: Current bus state snapshot.
        :return: A schedule that delivers this rider.
        """
        dropoff = Event(request.dropoff_stop_id, -1, request.id)
        best_schedule = Schedule(schedule.events + (dropoff,))
        best_cost = self._cost(best_schedule, state)

        for index in range(len(schedule)):
            candidate = Schedule(
                schedule.events[:index] + (dropoff,) + schedule.events[index:]
            )
            cost = self._cost(candidate, state)
            if cost < best_cost:
                best_cost = cost
                best_schedule = candidate
        return best_schedule

    # ------------------------------------------------------------------
    # Insertion
    # ------------------------------------------------------------------

    def _new_requests(self, state: BusState) -> list[Request]:
        """
        Return pending requests not yet in the schedule, oldest first.

        Insertion order is by arrival time for fairness; regret-based or
        cheapest-first ordering is a natural later refinement.
        :param state: Current bus state snapshot.
        :return: Requests to attempt to insert, in creation order.
        """
        known = self._schedule.owners()
        return sorted(
            (r for r in state.pending_requests if r.id not in known),
            key=lambda request: request.created_at,
        )

    def _best_insertion(
        self, schedule: Schedule, request: Request, state: BusState
    ) -> Schedule | None:
        """
        Find the cheapest feasible way to add one request to the schedule.

        Tries every (pickup slot, dropoff slot) pair with the pickup no later
        than the dropoff. Precedence needs no check: splicing preserves the
        order of existing events, and the pickup is always spliced ahead of
        its own dropoff.
        :param schedule: Schedule to insert into.
        :param request: The pending request to place.
        :param state: Current bus state snapshot.
        :return: The cheapest feasible schedule, or None if the request has
            no feasible slot and is therefore rejected.
        """
        pickup = Event(request.pickup_stop_id, 1, request.id)
        dropoff = Event(request.dropoff_stop_id, -1, request.id)
        capacity_total = state.remaining_capacity + len(state.onboard)
        initial_load = len(state.onboard)

        best_schedule: Schedule | None = None
        best_cost = float("inf")
        for pickup_at in range(len(schedule) + 1):
            for dropoff_at in range(pickup_at, len(schedule) + 1):
                candidate = schedule.insert(
                    pickup, dropoff, pickup_at, dropoff_at
                )
                if not candidate.capacity_ok(capacity_total, initial_load):
                    continue
                if not self._feasible(candidate, state):
                    continue
                cost = self._cost(candidate, state)
                if cost < best_cost:
                    best_cost = cost
                    best_schedule = candidate
        return best_schedule

    def _feasible(self, schedule: Schedule, state: BusState) -> bool:
        """
        Check any timing constraints beyond capacity.

        No timing constraint is imposed yet, so every capacity-respecting
        candidate is accepted. Time-window feasibility hooks in here.
        :param schedule: Candidate schedule.
        :param state: Current bus state snapshot.
        :return: True if the candidate is acceptable.
        """
        return True

    # ------------------------------------------------------------------
    # Cost
    # ------------------------------------------------------------------

    def _cost(self, schedule: Schedule, state: BusState) -> float:
        """
        Score a schedule by the total time its passengers spend on it.

        Every rider contributes their wait (request submitted until picked up)
        plus their ride (picked up until dropped off), which is precisely the
        pair of quantities ``MetricsTracker`` averages. Riders already aboard
        contribute from their real ``boarded_at``, so an insertion that
        detours the bus is charged for the extra time it costs them — that
        charge is what stops the search from serving each new request at
        everyone else's expense.
        :param schedule: Candidate schedule.
        :param state: Current bus state snapshot.
        :return: Total passenger time in seconds.
        """
        arrivals = schedule.arrival_times(
            state.position, state.now, self._travel_model
        )
        requests = {r.id: r for r in state.pending_requests}
        requests.update({r.id: r for r in state.onboard})

        pickup_arrival = {
            event.owner_id: arrivals[index]
            for index, event in enumerate(schedule.events)
            if event.load_delta > 0
        }

        total = 0.0
        for index, event in enumerate(schedule.events):
            if event.load_delta >= 0:
                continue
            request = requests.get(event.owner_id)
            if request is None:
                continue
            boarded_at = pickup_arrival.get(
                event.owner_id,
                # Already aboard: no pickup event left to time from.
                request.boarded_at
                if request.boarded_at is not None
                else state.now,
            )
            total += (boarded_at - request.created_at)  # wait
            total += (arrivals[index] - boarded_at)     # ride
        return total

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _progress_fallback(self, state: BusState) -> list[str]:
        """
        Serve the nearest pending pickup when the schedule yields no route.

        Without this an empty plan freezes the bus: its position never
        changes, so nothing new ever becomes reachable and the simulation
        deadlocks. Mirrors ConstrainedInsertionPolicy._progress_fallback.
        :param state: Current bus state snapshot.
        :return: A single-stop route to the nearest pending pickup, or empty.
        """
        if state.remaining_capacity <= 0 or not state.pending_requests:
            return []
        nearest = min(
            state.pending_requests,
            key=lambda request: self._graph.best_path_cost(
                state.position, request.pickup_stop_id
            ),
        )
        return [nearest.pickup_stop_id]
