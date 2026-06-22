"""Tests for NearestUnservedPolicy."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.RequestPool import Request
from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

# TransitGraph node IDs for the two stops in _PROVIDER:
#   s1 lives on road edge n1→n2  →  node "n1_n2"
#   s2 lives on road edge n3→n4  →  node "n3_n4"
_S1 = "n1_n2"
_S2 = "n3_n4"


def _req(req_id: str, pickup: str, dropoff: str) -> Request:
    """Build a minimal PENDING request."""
    return Request(
        id=req_id, pickup_stop_id=pickup,
        dropoff_stop_id=dropoff, created_at=0.0,
    )


class TestNearestUnservedPolicy(unittest.TestCase):
    """
    Verify NearestUnservedPolicy on the hand-authored map from
    test_transit_graph.py.

    Relevant travel costs (length / speed, all edges same speed limit):
      From "n4_n1":  _S1 costs 3 m,  _S2 costs 8 m  →  _S1 nearer.
      From "n2_n3":  _S2 costs 3 m,  _S1 costs 10 m →  _S2 nearer.
    """

    def setUp(self) -> None:
        self.policy = NearestUnservedPolicy(TransitGraph(_PROVIDER))

    # ------------------------------------------------------------------
    # Basic cases
    # ------------------------------------------------------------------

    def test_no_demand_returns_empty(self) -> None:
        """Returns [] immediately when there are no passengers to serve."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[],
            remaining_capacity=3,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3), [])

    def test_one_pending_visits_pickup_then_dropoff(self) -> None:
        """With one pending request, visits pickup before dropoff."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        self.assertEqual(
            self.policy.plan(state, n_stops=3),
            [_S1, _S2],
        )

    def test_one_onboard_visits_dropoff_only(self) -> None:
        """With only onboard passengers, routes straight to their dropoffs."""
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup=_S1, dropoff=_S2)],
            pending_requests=[],
            remaining_capacity=2,
        )
        self.assertEqual(
            self.policy.plan(state, n_stops=3),
            [_S2],
        )

    def test_demand_exhausted_returns_short_list(self) -> None:
        """Returns fewer than n_stops stops when demand runs out early."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        result = self.policy.plan(state, n_stops=10)
        self.assertEqual(result, [_S1, _S2])

    def test_multiple_pending_at_same_stop_all_board(self) -> None:
        """Multiple pending passengers at the same stop all board in one
        visit."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[
                _req("r1", pickup=_S1, dropoff=_S2),
                _req("r2", pickup=_S1, dropoff=_S2),
            ],
            remaining_capacity=2,
        )
        # Both board at _S1 in a single visit; both alight at _S2.
        self.assertEqual(
            self.policy.plan(state, n_stops=5),
            [_S1, _S2],
        )

    # ------------------------------------------------------------------
    # Nearest-neighbour selection
    # ------------------------------------------------------------------

    def test_dropoff_chosen_when_nearer_than_pickup(self) -> None:
        """
        When the onboard dropoff is nearer than the pending pickup, the
        dropoff is served first.
        """
        # From n4_n1: _S1 (3 m) < _S2 (8 m).
        # Onboard dropoff at _S1 (nearer); pending pickup at _S2 (farther).
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S1)],
            pending_requests=[_req("r2", pickup=_S2, dropoff=_S1)],
            remaining_capacity=1,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3)[0], _S1)

    def test_pickup_chosen_when_nearer_than_dropoff(self) -> None:
        """
        When the pending pickup is nearer than the onboard dropoff, the
        pickup is served first.
        """
        # From n2_n3: _S2 (3 m) < _S1 (10 m).
        # Onboard dropoff at _S1 (farther); pending pickup at _S2 (nearer).
        state = BusState(
            position="n2_n3",
            onboard=[_req("r1", pickup="n2_n3", dropoff=_S1)],
            pending_requests=[_req("r2", pickup=_S2, dropoff=_S1)],
            remaining_capacity=1,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3)[0], _S2)

    # ------------------------------------------------------------------
    # Capacity gate
    # ------------------------------------------------------------------

    def test_capacity_gate_skips_pickup_when_full(self) -> None:
        """
        When remaining_capacity is 0, pending pickups are excluded from
        candidates; the bus goes to a dropoff first.
        """
        # Without the gate the bus would head to _S1 (nearer pickup).
        # With capacity=0 it must visit the dropoff at _S2 first.
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S2)],
            pending_requests=[_req("r2", pickup=_S1, dropoff=_S2)],
            remaining_capacity=0,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3)[0], _S2)

    def test_capacity_gate_lifts_after_alight(self) -> None:
        """
        After a passenger alights and capacity opens, subsequent steps
        include pickup stops again.
        """
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S2)],
            pending_requests=[_req("r2", pickup=_S1, dropoff=_S2)],
            remaining_capacity=0,
        )
        result = self.policy.plan(state, n_stops=3)
        # _S2 is served first (gate active); _S1 is served after capacity opens.
        self.assertIn(_S1, result)
        self.assertGreater(result.index(_S1), result.index(_S2))


if __name__ == "__main__":
    unittest.main()
