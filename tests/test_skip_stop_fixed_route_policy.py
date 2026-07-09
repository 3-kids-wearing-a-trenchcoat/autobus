"""Tests for SkipStopFixedRoutePolicy."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.SkipStopFixedRoutePolicy import SkipStopFixedRoutePolicy
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


class TestSkipStopFixedRoutePolicy(unittest.TestCase):
    """
    Verify SkipStopFixedRoutePolicy on the hand-authored map from
    test_transit_graph.py, using an explicit cycle order [_S1, _S2].

    From position "n4_n1" the nearest stop is _S1 (3 m < 8 m), so the cycle
    walk starts at _S1 and then reaches _S2.
    """

    def setUp(self) -> None:
        self.policy = SkipStopFixedRoutePolicy(
            TransitGraph(_PROVIDER), stop_order=[_S1, _S2]
        )

    def test_no_demand_returns_empty(self) -> None:
        """Returns [] when no stop has boarding or alighting demand."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[],
            remaining_capacity=3,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3), [])

    def test_empty_stop_is_skipped(self) -> None:
        """A cycle stop with no demand is skipped; order is preserved."""
        # Demand only at _S2; _S1 comes first in the cycle but is empty.
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S2, dropoff=_S1)],
            remaining_capacity=3,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3), [_S2])

    def test_alighting_stop_is_kept(self) -> None:
        """A stop where an onboard passenger alights is always kept."""
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S1)],
            pending_requests=[],
            remaining_capacity=2,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3), [_S1])

    def test_pickup_and_dropoff_both_kept(self) -> None:
        """A pickup then its dropoff are both kept; returns a short list."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        # Only two demanded stops exist, so fewer than n_stops are returned.
        self.assertEqual(self.policy.plan(state, n_stops=5), [_S1, _S2])

    def test_capacity_gate_skips_pickup_when_full(self) -> None:
        """
        A pickup-only stop is skipped while the bus is full; the dropoff
        that frees capacity is still served.
        """
        # Bus full: onboard passenger drops at _S2; a pending pickup sits at
        # _S1 (first in cycle). _S1 must be skipped, _S2 served.
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S2)],
            pending_requests=[_req("r2", pickup=_S1, dropoff=_S2)],
            remaining_capacity=0,
        )
        self.assertEqual(self.policy.plan(state, n_stops=3), [_S2])


if __name__ == "__main__":
    unittest.main()
