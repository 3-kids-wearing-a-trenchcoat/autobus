"""Tests for ConstrainedInsertionPolicy."""

import unittest

from src.algorithm.ConstrainedInsertionPolicy import ConstrainedInsertionPolicy
from src.algorithm.DispatchPolicy import BusState
from src.algorithm.RequestPool import Request
from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord
from src.map.TransitGraph import TransitGraph
from maps import SQUARE_MAP

# TransitGraph node IDs (road edges) usable as pickup/dropoff in SQUARE_MAP.
# Only _S1 and _S2 carry a StopRecord; the rest are plain nodes, which is fine
# since a request's pickup/dropoff is any node ID. Relevant travel costs:
#   n4_n1 -> n1_n2 ~0.083   n4_n1 -> n3_n4 ~0.222   n4_n1 -> n2_n3 ~0.194
#   n1_n2 <-> n3_n4 ~0.194 (the only two stops; median pair cost ~0.194)
_S1 = "n1_n2"
_S2 = "n3_n4"


def _req(req_id: str, pickup: str, dropoff: str, created_at: float = 0.0) -> Request:
    """Build a minimal PENDING request."""
    return Request(
        id=req_id, pickup_stop_id=pickup,
        dropoff_stop_id=dropoff, created_at=created_at,
    )


class TestConstrainedInsertionPolicy(unittest.TestCase):
    """Verify ConstrainedInsertionPolicy on the hand-authored test map."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    def test_cheap_request_inserted_pickup_before_dropoff(self) -> None:
        """A within-bounds request is served, pickup ahead of its dropoff."""
        policy = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=1000, ride_factor=10
        )
        state = BusState(
            position="n4_n1", onboard=[],
            pending_requests=[_req("r", _S1, _S2)], remaining_capacity=3,
        )
        route = policy.plan(state, n_stops=3)
        self.assertEqual(route, [_S1, _S2])
        self.assertLess(route.index(_S1), route.index(_S2))  # precedence

    def test_far_pickup_rejected_while_near_served(self) -> None:
        """A pickup beyond the wait bound is rejected while a near one is served."""
        policy = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=0.15, ride_factor=10
        )
        state = BusState(
            position="n4_n1", onboard=[],
            pending_requests=[
                _req("near", _S1, "n1_n3", created_at=0.0),   # wait ~0.083
                _req("far", "n2_n3", _S2, created_at=1.0),    # wait ~0.194
            ],
            remaining_capacity=3,
        )
        route = policy.plan(state, n_stops=5)
        # The near request is served; the far request's pickup never appears
        # (it stays PENDING). The route is non-empty, so the fallback is inert.
        self.assertEqual(route, [_S1, "n1_n3"])
        self.assertNotIn("n2_n3", route)

    def test_ride_bound_rejects_detour_of_existing_rider(self) -> None:
        """
        A pending request is rejected when the only wait-feasible way to serve
        it would detour an already-onboard passenger past the ride bound.

        Onboard 'o' rides n3_n4 -> n2_n3 (direct ~0.305). Serving pending 't'
        (pickup n1_n3) after dropping 'o' first would put its pickup ~0.582
        away — beyond the 0.2 wait bound — so the only wait-feasible slot picks
        't' up before 'o' alights, stretching 'o' to ~0.637 (~2.1x). A tight
        ride_factor rejects that; a loose one accepts it.
        """
        state = BusState(
            position="n3_n4",
            onboard=[_req("o", "n3_n4", "n2_n3")],
            pending_requests=[_req("t", "n1_n3", "n4_n1")],
            remaining_capacity=2,
        )
        tight = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=0.2, ride_factor=1.2
        )
        loose = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=0.2, ride_factor=3.0
        )
        # Tight: 't' rejected, only the committed dropoff remains.
        self.assertNotIn("n1_n3", tight.plan(state, n_stops=5))
        # Loose: the detour is tolerated, so 't' is served.
        self.assertIn("n1_n3", loose.plan(state, n_stops=5))

    def test_onboard_dropoff_never_rejected(self) -> None:
        """A committed dropoff is served even with a tiny wait bound."""
        policy = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=0.01, ride_factor=10
        )
        state = BusState(
            position="n4_n1",
            onboard=[_req("o", "n4_n1", _S2)],   # dropoff ~0.222 >> 0.01
            pending_requests=[], remaining_capacity=2,
        )
        self.assertEqual(policy.plan(state, n_stops=3), [_S2])

    def test_capacity_gate_defers_pickup_behind_dropoff(self) -> None:
        """With no free seat, a pickup waits until an onboard dropoff frees one."""
        policy = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=1000, ride_factor=10
        )
        state = BusState(
            position="n4_n1",
            onboard=[_req("o", "n4_n1", _S2)],       # fills the only seat
            pending_requests=[_req("r", _S1, "n2_n3")],
            remaining_capacity=0,                    # capacity_total == 1
        )
        route = policy.plan(state, n_stops=5)
        # The pickup _S1 cannot board until _S2 (the onboard dropoff) frees the
        # seat, so _S2 must come first.
        self.assertLess(route.index(_S2), route.index(_S1))

    def test_progress_fallback_serves_lone_over_bound_pickup(self) -> None:
        """A lone pickup beyond the wait bound is still served, not stranded."""
        policy = ConstrainedInsertionPolicy(
            self.graph, max_wait_cost=0.15, ride_factor=10
        )
        state = BusState(
            position="n4_n1", onboard=[],
            pending_requests=[_req("r", _S2, _S1)],   # pickup ~0.222 > 0.15
            remaining_capacity=3,
        )
        # Every slot exceeds the wait bound, so the request is rejected and the
        # progress guarantee falls back to the nearest pending pickup.
        self.assertEqual(policy.plan(state, n_stops=3), [_S2])

    def test_auto_wait_bound(self) -> None:
        """Auto-derivation yields wait_factor times the median pairwise cost."""
        policy = ConstrainedInsertionPolicy(self.graph)
        # median pairwise stop cost ~0.194; default factor 2.0 -> ~0.388.
        self.assertAlmostEqual(policy._max_wait_cost, 0.3877, places=4)

    def test_auto_wait_bound_requires_two_stops(self) -> None:
        """Auto-derivation raises when the graph has fewer than two stops."""
        one_stop_provider = StaticMapProvider(
            stops=[
                StopInput(
                    id="s1", from_node_id="n1", to_node_id="n2",
                    pos_on_edge=0.5,
                ),
            ],
            nodes=[
                RoadNodeRecord(id="n1", x=0.0, y=0.0),
                RoadNodeRecord(id="n2", x=4.0, y=0.0),
            ],
            edges=[("n1", "n2"), ("n2", "n1")],
        )
        with self.assertRaises(ValueError):
            ConstrainedInsertionPolicy(TransitGraph(one_stop_provider))


if __name__ == "__main__":
    unittest.main()
