"""Tests for RideBoundedGreedyPolicy."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.RideBoundedGreedyPolicy import RideBoundedGreedyPolicy
from src.algorithm.RequestPool import Request
from src.map.TransitGraph import TransitGraph
from test_transit_graph import _PROVIDER

# TransitGraph node IDs for the two stops in _PROVIDER:
#   s1 lives on road edge n1→n2  →  node "n1_n2"
#   s2 lives on road edge n3→n4  →  node "n3_n4"
_S1 = "n1_n2"
_S2 = "n3_n4"

# Relevant travel costs on the directed test map (best_path_cost sums the
# source-side edge lengths along the shortest path):
#   n3_n4 -> n4_n1 = 4      n3_n4 -> n1_n2 = 7      n3_n4 -> n2_n3 = 11
#   n4_n1 -> n1_n2 = 3      n4_n1 -> n2_n3 = 8
#   direct(n1_n2, n2_n3) = 4   (a short onboard trip)
#   direct(n3_n4, n1_n2) = 7   (a longer onboard trip)


def _req(req_id: str, pickup: str, dropoff: str) -> Request:
    """Build a minimal PENDING request."""
    return Request(
        id=req_id, pickup_stop_id=pickup,
        dropoff_stop_id=dropoff, created_at=0.0,
    )


class TestRideBoundedGreedyPolicy(unittest.TestCase):
    """Verify RideBoundedGreedyPolicy on the hand-authored test map."""

    def setUp(self) -> None:
        self.graph = TransitGraph(_PROVIDER)

    # ------------------------------------------------------------------
    # Falls back to plain greedy when the gate cannot fire
    # ------------------------------------------------------------------

    def test_empty_bus_matches_nearest(self) -> None:
        """With no onboard riders the gate never fires; acts like Nearest."""
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        policy = RideBoundedGreedyPolicy(self.graph)
        self.assertEqual(policy.plan(state, n_stops=3), [_S1, _S2])

    def test_high_ride_factor_matches_nearest(self) -> None:
        """A huge ride_factor keeps the gate closed; route equals Nearest's."""
        state = self._gate_scenario()
        nearest = NearestUnservedPolicy(self.graph).plan(state, n_stops=5)
        bounded = RideBoundedGreedyPolicy(
            self.graph, ride_factor=100.0
        ).plan(state, n_stops=5)
        self.assertEqual(bounded, nearest)

    # ------------------------------------------------------------------
    # The ride-slack gate
    # ------------------------------------------------------------------

    def test_gate_forces_dropoff_over_nearer_pickup(self) -> None:
        """
        An over-budget onboard rider forces a dropoff even when a pending
        pickup is strictly nearer (which plain Nearest would take first).
        """
        state = self._gate_scenario()
        # Plain Nearest grabs the nearer pickup (n4_n1, cost 4) first.
        nearest = NearestUnservedPolicy(self.graph).plan(state, n_stops=1)
        self.assertEqual(nearest[0], "n4_n1")
        # Ride-bounded suppresses pickups and delivers r1 (dropoff n2_n3) first.
        bounded = RideBoundedGreedyPolicy(self.graph).plan(state, n_stops=1)
        self.assertEqual(bounded[0], "n2_n3")

    def test_deferred_pickup_still_served_no_rejection(self) -> None:
        """
        A pickup deferred by the gate is not dropped: once the rider is
        delivered and slack returns, the pickup is served later in the route.
        """
        state = self._gate_scenario()
        route = RideBoundedGreedyPolicy(self.graph).plan(state, n_stops=5)
        self.assertIn("n4_n1", route)  # the deferred pickup is still served
        self.assertGreater(route.index("n4_n1"), route.index("n2_n3"))

    def test_explicit_max_ride_cost_absolute_cap(self) -> None:
        """
        An explicit max_ride_cost overrides the ride_factor budget: a tiny cap
        (below the projected ride) fires the gate; a large cap leaves it open.
        """
        state = self._gate_scenario()
        # Projected ride to r1's dropoff is ~0.30; a 0.05 cap has no slack.
        tight = RideBoundedGreedyPolicy(
            self.graph, max_ride_cost=0.05
        ).plan(state, n_stops=1)
        self.assertEqual(tight[0], "n2_n3")
        # A generous cap keeps the gate closed; the nearer pickup wins.
        loose = RideBoundedGreedyPolicy(
            self.graph, max_ride_cost=10.0
        ).plan(state, n_stops=1)
        self.assertEqual(loose[0], "n4_n1")

    def test_budget_scales_with_trip_length(self) -> None:
        """
        Under the same ride_factor a short-trip rider trips the gate while a
        long-trip rider does not, so only the short trip forces a dropoff.
        """
        pending = [_req("r2", pickup="n4_n1", dropoff="n1_n2")]

        short_trip = BusState(
            position="n3_n4",
            onboard=[_req("r1", pickup="n1_n2", dropoff="n2_n3")],
            pending_requests=list(pending),
            remaining_capacity=1,
        )
        long_trip = BusState(
            position="n3_n4",
            onboard=[_req("r1", pickup="n3_n4", dropoff="n1_n2")],
            pending_requests=list(pending),
            remaining_capacity=1,
        )
        policy = RideBoundedGreedyPolicy(self.graph, ride_factor=1.5)
        # Short trip (direct 4, budget 6): projected 11 -> gate fires -> dropoff.
        self.assertEqual(policy.plan(short_trip, n_stops=1)[0], "n2_n3")
        # Long trip (direct 7, budget 10.5): projected 7 -> gate stays open ->
        # the nearer pickup is taken, exactly as plain Nearest would.
        self.assertEqual(policy.plan(long_trip, n_stops=1)[0], "n4_n1")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _gate_scenario(self) -> BusState:
        """
        A state where the gate fires under the default ride_factor.

        Onboard r1 (pickup n1_n2 -> dropoff n2_n3) has a short direct trip
        (cost 4, budget 6 at ride_factor 1.5) but the bus sits at n3_n4, a
        cost of 11 from that dropoff, so r1 has no slack. A pending r2 offers a
        strictly nearer pickup at n4_n1 (cost 4).
        """
        return BusState(
            position="n3_n4",
            onboard=[_req("r1", pickup="n1_n2", dropoff="n2_n3")],
            pending_requests=[_req("r2", pickup="n4_n1", dropoff="n1_n2")],
            remaining_capacity=1,
        )


if __name__ == "__main__":
    unittest.main()
