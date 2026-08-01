"""Tests for WindowInsertionPolicy's persistent schedule and reconciliation."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.RequestPool import Request, RequestState
from src.algorithm.WindowInsertionPolicy import WindowInsertionPolicy
from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

# TransitGraph node IDs (road edges) in _PROVIDER. Any node ID is usable as a
# pickup or dropoff; only _S1 and _S2 carry an actual StopRecord.
_START = "n4_n1"
_S1 = "n1_n2"
_S2 = "n3_n4"
_S3 = "n2_n3"


def _req(
    req_id: str,
    pickup: str,
    dropoff: str,
    created_at: float = 0.0,
    state: RequestState = RequestState.PENDING,
    boarded_at: float | None = None,
) -> Request:
    """Build a request in the given lifecycle state."""
    return Request(
        id=req_id, pickup_stop_id=pickup, dropoff_stop_id=dropoff,
        created_at=created_at, state=state, boarded_at=boarded_at,
    )


class TestWindowInsertionPolicy(unittest.TestCase):
    """Verify insertion and schedule persistence on the test map."""

    def setUp(self) -> None:
        self.graph = TransitGraph(_PROVIDER)
        self.policy = WindowInsertionPolicy(self.graph)

    def _state(
        self,
        pending: list[Request] | None = None,
        onboard: list[Request] | None = None,
        remaining_capacity: int = 4,
        now: float = 0.0,
        position: str = _START,
    ) -> BusState:
        """Build a BusState with sensible defaults."""
        return BusState(
            position=position,
            onboard=onboard or [],
            pending_requests=pending or [],
            remaining_capacity=remaining_capacity,
            now=now,
        )

    # ------------------------------------------------------------------
    # Basic insertion
    # ------------------------------------------------------------------

    def test_serves_a_single_request_pickup_first(self) -> None:
        state = self._state(pending=[_req("r1", _S1, _S2)])
        self.assertEqual(self.policy.plan(state, 5), [_S1, _S2])

    def test_returns_empty_route_without_demand(self) -> None:
        self.assertEqual(self.policy.plan(self._state(), 5), [])

    def test_zero_n_stops_returns_empty(self) -> None:
        state = self._state(pending=[_req("r1", _S1, _S2)])
        self.assertEqual(self.policy.plan(state, 0), [])

    def test_route_is_truncated_to_n_stops(self) -> None:
        state = self._state(pending=[_req("r1", _S1, _S2)])
        self.assertEqual(self.policy.plan(state, 1), [_S1])

    def test_shared_pickup_collapses_into_one_visit(self) -> None:
        """Two riders boarding at the same stop is one pull-over."""
        state = self._state(pending=[
            _req("r1", _S1, _S2), _req("r2", _S1, _S2, created_at=1.0),
        ])
        self.assertEqual(self.policy.plan(state, 5), [_S1, _S2])

    # ------------------------------------------------------------------
    # Persistence across calls
    # ------------------------------------------------------------------

    def test_schedule_persists_between_calls(self) -> None:
        request = _req("r1", _S1, _S2)
        first = self.policy.plan(self._state(pending=[request]), 5)
        second = self.policy.plan(self._state(pending=[request]), 5)
        self.assertEqual(first, second)

    def test_request_is_not_inserted_twice(self) -> None:
        request = _req("r1", _S1, _S2)
        self.policy.plan(self._state(pending=[request]), 5)
        self.policy.plan(self._state(pending=[request]), 5)
        self.assertEqual(len(self.policy.schedule), 2)

    # ------------------------------------------------------------------
    # Reconciliation: requests leaving the system
    # ------------------------------------------------------------------

    def test_abandoned_request_is_dropped_from_schedule(self) -> None:
        """A passenger who gave up must not keep a slot in the schedule."""
        request = _req("r1", _S1, _S2)
        self.policy.plan(self._state(pending=[request]), 5)
        # The backend cancelled it, so it appears in neither list any more.
        self.assertEqual(self.policy.plan(self._state(), 5), [])
        self.assertEqual(len(self.policy.schedule), 0)

    def test_abandonment_leaves_other_requests_scheduled(self) -> None:
        kept = _req("r1", _S1, _S2)
        doomed = _req("r2", _S3, _S2, created_at=1.0)
        self.policy.plan(self._state(pending=[kept, doomed]), 5)
        self.policy.plan(self._state(pending=[kept]), 5)
        self.assertEqual(self.policy.schedule.owners(), {"r1"})

    # ------------------------------------------------------------------
    # Reconciliation: boarding
    # ------------------------------------------------------------------

    def test_boarded_rider_keeps_only_their_dropoff(self) -> None:
        request = _req("r1", _S1, _S2)
        self.policy.plan(self._state(pending=[request]), 5)

        boarded = _req(
            "r1", _S1, _S2, state=RequestState.ON_BUS, boarded_at=10.0
        )
        route = self.policy.plan(
            self._state(onboard=[boarded], now=10.0, position=_S1), 5
        )

        self.assertEqual(route, [_S2])
        self.assertEqual(self.policy.schedule.pickup_owners(), set())
        self.assertEqual(self.policy.schedule.dropoff_owners(), {"r1"})

    def test_opportunistic_boarder_is_adopted(self) -> None:
        """
        BusAgent boards anyone waiting at a stop it reaches, including riders
        this policy never scheduled. Their dropoff becomes an obligation.
        """
        stowaway = _req(
            "r9", _S1, _S2, state=RequestState.ON_BUS, boarded_at=5.0
        )
        route = self.policy.plan(
            self._state(onboard=[stowaway], now=5.0, position=_S1), 5
        )
        self.assertIn(_S2, route)
        self.assertEqual(self.policy.schedule.dropoff_owners(), {"r9"})

    def test_adopted_dropoff_survives_alongside_planned_work(self) -> None:
        planned = _req("r1", _S3, _S2)
        self.policy.plan(self._state(pending=[planned]), 5)

        stowaway = _req(
            "r9", _S1, _S2, state=RequestState.ON_BUS, boarded_at=5.0
        )
        self.policy.plan(
            self._state(pending=[planned], onboard=[stowaway], now=5.0), 5
        )
        self.assertEqual(self.policy.schedule.owners(), {"r1", "r9"})

    # ------------------------------------------------------------------
    # Capacity
    # ------------------------------------------------------------------

    def test_pickup_deferred_until_a_seat_frees(self) -> None:
        """
        A full bus does not reject a request outright — capacity is checked
        against the running load, so the pickup is simply scheduled after the
        onboard rider alights.
        """
        aboard = _req(
            "r0", _S1, _S2, state=RequestState.ON_BUS, boarded_at=0.0
        )
        state = self._state(
            pending=[_req("r1", _S3, _S2)],
            onboard=[aboard],
            remaining_capacity=0,
        )
        self.policy.plan(state, 5)

        events = self.policy.schedule.events
        alights = next(
            i for i, e in enumerate(events)
            if e.owner_id == "r0" and e.load_delta < 0
        )
        boards = next(
            i for i, e in enumerate(events)
            if e.owner_id == "r1" and e.load_delta > 0
        )
        self.assertLess(alights, boards)

    def test_request_rejected_when_no_seat_can_ever_free(self) -> None:
        """With no capacity at all, every insertion is infeasible."""
        state = self._state(
            pending=[_req("r1", _S3, _S2)], remaining_capacity=0
        )
        self.policy.plan(state, 5)
        self.assertEqual(len(self.policy.schedule), 0)

    def test_rejected_request_can_be_served_later(self) -> None:
        """Rejection is a soft skip: the request stays eligible."""
        waiting = _req("r1", _S3, _S2)
        self.policy.plan(
            self._state(pending=[waiting], remaining_capacity=0), 5
        )
        self.assertEqual(len(self.policy.schedule), 0)

        # A seat has since freed up.
        self.policy.plan(self._state(pending=[waiting]), 5)
        self.assertEqual(self.policy.schedule.pickup_owners(), {"r1"})

    # ------------------------------------------------------------------
    # Precedence
    # ------------------------------------------------------------------

    def test_every_pickup_precedes_its_dropoff(self) -> None:
        state = self._state(pending=[
            _req("r1", _S1, _S2),
            _req("r2", _S3, _S1, created_at=1.0),
        ])
        self.policy.plan(state, 10)
        self.assertTrue(self.policy.schedule.precedence_ok())
