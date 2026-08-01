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
        # Travel on this map costs fractions of a second, so a window this
        # wide never binds. Window behaviour is exercised separately below.
        self.policy = WindowInsertionPolicy(self.graph, max_wait_s=1000.0)

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


class TestServiceWindow(unittest.TestCase):
    """
    Verify the hard pickup deadline.

    Relevant costs from _START on the test map: _S1 is ~0.083s away, _S3
    ~0.194s, _S2 ~0.222s. Windows below are chosen against those.
    """

    def setUp(self) -> None:
        self.graph = TransitGraph(_PROVIDER)

    def _policy(self, **kwargs) -> WindowInsertionPolicy:
        """Build a policy with the given window settings."""
        return WindowInsertionPolicy(self.graph, **kwargs)

    def _state(
        self,
        pending: list[Request] | None = None,
        onboard: list[Request] | None = None,
        now: float = 0.0,
        position: str = _START,
    ) -> BusState:
        """Build a BusState with sensible defaults."""
        return BusState(
            position=position,
            onboard=onboard or [],
            pending_requests=pending or [],
            remaining_capacity=4,
            now=now,
        )

    def test_reachable_pickup_is_accepted(self) -> None:
        policy = self._policy(max_wait_s=0.15)
        policy.plan(self._state(pending=[_req("r1", _S1, _S2)]), 5)
        self.assertEqual(policy.schedule.pickup_owners(), {"r1"})

    def test_pickup_beyond_the_window_is_rejected(self) -> None:
        policy = self._policy(max_wait_s=0.15)
        policy.plan(self._state(pending=[_req("r1", _S3, _S2)]), 5)
        self.assertEqual(len(policy.schedule), 0)

    def test_window_runs_from_request_creation(self) -> None:
        """An older request has less of its window left to spend."""
        policy = self._policy(max_wait_s=0.15)
        # Created at t=-1 relative to a bus departing at t=0, so the window
        # closed before the bus could ever get there.
        policy.plan(
            self._state(pending=[_req("r1", _S1, _S2, created_at=-1.0)]), 5
        )
        self.assertEqual(len(policy.schedule), 0)

    def test_rejected_request_does_not_block_a_reachable_one(self) -> None:
        policy = self._policy(max_wait_s=0.15)
        policy.plan(
            self._state(pending=[
                _req("r_far", _S3, _S2),
                _req("r_near", _S1, _S2, created_at=1.0),
            ]),
            5,
        )
        self.assertEqual(policy.schedule.pickup_owners(), {"r_near"})

    def test_default_window_derives_from_the_graph(self) -> None:
        self.assertGreater(self._policy().max_wait_s, 0.0)

    def test_explicit_window_overrides_the_default(self) -> None:
        self.assertEqual(self._policy(max_wait_s=42.0).max_wait_s, 42.0)

    # ------------------------------------------------------------------
    # Safety factor
    # ------------------------------------------------------------------

    def test_safety_factor_rejects_a_marginal_request(self) -> None:
        """Planning pessimistically refuses a pickup that only just fits."""
        optimistic = self._policy(max_wait_s=0.1)
        optimistic.plan(self._state(pending=[_req("r1", _S1, _S2)]), 5)
        self.assertEqual(optimistic.schedule.pickup_owners(), {"r1"})

        cautious = self._policy(max_wait_s=0.1, safety_factor=2.0)
        cautious.plan(self._state(pending=[_req("r1", _S1, _S2)]), 5)
        self.assertEqual(len(cautious.schedule), 0)

    # ------------------------------------------------------------------
    # Recovery from a commitment that became unkeepable
    # ------------------------------------------------------------------

    def test_commitment_released_when_bus_falls_behind(self) -> None:
        """
        The CARLA case: the bus runs slower than projected, so a promise made
        in good faith becomes impossible and must be released rather than
        driven to.
        """
        policy = self._policy(max_wait_s=0.15)
        request = _req("r1", _S1, _S2)
        policy.plan(self._state(pending=[request]), 5)
        self.assertEqual(policy.schedule.pickup_owners(), {"r1"})

        # Re-plan far later than projected: the window has since closed.
        policy.plan(self._state(pending=[request], now=100.0), 5)
        self.assertEqual(len(policy.schedule), 0)

    def test_released_rider_is_not_stranded_by_an_empty_route(self) -> None:
        """Dropping every commitment must not freeze the bus in place."""
        policy = self._policy(max_wait_s=0.15)
        request = _req("r1", _S1, _S2)
        policy.plan(self._state(pending=[request]), 5)
        route = policy.plan(self._state(pending=[request], now=100.0), 5)
        self.assertEqual(route, [_S1])

    def test_onboard_rider_is_never_released(self) -> None:
        """A passenger already aboard cannot be un-boarded, ever."""
        policy = self._policy(max_wait_s=0.15)
        aboard = _req(
            "r1", _S1, _S2, state=RequestState.ON_BUS, boarded_at=0.0
        )
        policy.plan(self._state(onboard=[aboard], now=100.0), 5)
        self.assertEqual(policy.schedule.dropoff_owners(), {"r1"})
