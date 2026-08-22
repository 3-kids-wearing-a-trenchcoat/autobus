"""Tests for DeviatedFixedRoutePolicy."""

import unittest

from src.algorithm.DeviatedFixedRoutePolicy import DeviatedFixedRoutePolicy
from src.algorithm.DispatchPolicy import BusState
from src.algorithm.RequestPool import Request
from src.algorithm.SkipStopFixedRoutePolicy import SkipStopFixedRoutePolicy
from src.map.TransitGraph import TransitGraph
from maps import SQUARE_MAP

# All four road-edge nodes are valid graph nodes usable as an explicit cycle.
# Relevant directed travel costs on the test map (source-side length sums;
# best_path_cost returns these scaled uniformly by speed, so cost ratios hold):
#   n4_n1 -> n1_n2 = 3    n1_n2 -> n2_n3 = 4    n4_n1 -> n2_n3 = 7
#   n4_n1 -> n3_n4 = 8 (via the n1_n3 diagonal)    n1_n2 -> n3_n4 = 7
#   n3_n4 -> n2_n3 = 11
_ALL = ["n1_n2", "n2_n3", "n3_n4", "n4_n1"]


def _req(req_id: str, pickup: str, dropoff: str) -> Request:
    """Build a minimal PENDING request."""
    return Request(
        id=req_id, pickup_stop_id=pickup,
        dropoff_stop_id=dropoff, created_at=0.0,
    )


class TestDeviatedFixedRoutePolicy(unittest.TestCase):
    """Verify DeviatedFixedRoutePolicy on the hand-authored test map."""

    def setUp(self) -> None:
        self.graph = TransitGraph(SQUARE_MAP)

    # ------------------------------------------------------------------
    # Reduces to skip-stop when no detour is allowed
    # ------------------------------------------------------------------
    def test_reduces_to_skip_stop_at_factor_one(self) -> None:
        """detour_factor=1.0 and max_detour_cost=0 both equal skip-stop."""
        # Cycle order lists n2_n3 before n1_n2, but n1_n2 sits exactly on the
        # way from n4_n1 to n2_n3 (extra 0). Skip-stop still visits in order.
        order = ["n4_n1", "n2_n3", "n1_n2", "n3_n4"]
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[
                _req("rb", pickup="n2_n3", dropoff="n3_n4"),
                _req("rs", pickup="n1_n2", dropoff="n3_n4"),
            ],
            remaining_capacity=3,
        )
        skip = SkipStopFixedRoutePolicy(self.graph, stop_order=order)
        expected = skip.plan(state, n_stops=5)

        factor_one = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, detour_factor=1.0
        )
        zero_cost = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, max_detour_cost=0.0
        )
        self.assertEqual(factor_one.plan(state, n_stops=5), expected)
        self.assertEqual(zero_cost.plan(state, n_stops=5), expected)
        # Sanity: skip-stop keeps cycle order, backbone stop first.
        self.assertEqual(expected[0], "n2_n3")

    # ------------------------------------------------------------------
    # Bounded deviation
    # ------------------------------------------------------------------

    def test_cheap_out_of_order_stop_served_first(self) -> None:
        """
        A near, on-the-way demanded stop that is later in cycle order is served
        before the next backbone stop (which skip-stop would visit first).
        """
        order = ["n4_n1", "n2_n3", "n1_n2", "n3_n4"]
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[
                _req("rb", pickup="n2_n3", dropoff="n3_n4"),
                _req("rs", pickup="n1_n2", dropoff="n3_n4"),
            ],
            remaining_capacity=3,
        )
        # extra(n1_n2) = 3 + 4 - 7 = 0 < allowed = 0.5 * 7; deviation taken.
        policy = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, detour_factor=1.5
        )
        route = policy.plan(state, n_stops=5)
        self.assertEqual(route[0], "n1_n2")
        self.assertEqual(route[:2], ["n1_n2", "n2_n3"])

    def test_expensive_deviation_not_taken(self) -> None:
        """A demanded stop beyond the detour cap is left in backbone order."""
        order = ["n1_n2", "n2_n3", "n3_n4", "n4_n1"]
        state = BusState(
            position="n4_n1",
            # Onboard rider alights at the near first backbone stop (n1_n2);
            # the only pending pickup (n3_n4) is a far, expensive detour and
            # later becomes the backbone target itself, never a cheap deviation.
            onboard=[_req("on", pickup="n4_n1", dropoff="n1_n2")],
            pending_requests=[_req("far", pickup="n3_n4", dropoff="n2_n3")],
            remaining_capacity=3,
        )
        # extra(n3_n4) = 8 + 7 - 3 = 12 > allowed = 0.5 * 3; no deviation.
        policy = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, detour_factor=1.5
        )
        skip = SkipStopFixedRoutePolicy(self.graph, stop_order=order)
        self.assertEqual(
            policy.plan(state, n_stops=5), skip.plan(state, n_stops=5)
        )

    # ------------------------------------------------------------------
    # Capacity gate
    # ------------------------------------------------------------------

    def test_pickup_deviation_gated_by_capacity(self) -> None:
        """A pickup deviation is taken only when a seat is free."""
        order = ["n3_n4", "n1_n2", "n2_n3", "n4_n1"]
        # Backbone stop is the onboard rider's dropoff (n3_n4), independent of
        # capacity; n1_n2 is a pickup deviation on the way (extra 3+7-8 = 2).
        onboard = [_req("on", pickup="n4_n1", dropoff="n3_n4")]
        pending = [_req("p", pickup="n1_n2", dropoff="n2_n3")]

        full = BusState(
            position="n4_n1", onboard=list(onboard),
            pending_requests=list(pending), remaining_capacity=0,
        )
        room = BusState(
            position="n4_n1", onboard=list(onboard),
            pending_requests=list(pending), remaining_capacity=1,
        )
        policy = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, detour_factor=1.5
        )
        # Full: pickup has no room, so no deviation; head to the dropoff.
        self.assertEqual(policy.plan(full, n_stops=5)[0], "n3_n4")
        # Room: the on-the-way pickup is served first.
        self.assertEqual(policy.plan(room, n_stops=5)[0], "n1_n2")

    def test_respects_n_stops_length(self) -> None:
        """The route never exceeds n_stops."""
        order = ["n4_n1", "n2_n3", "n1_n2", "n3_n4"]
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[
                _req("rb", pickup="n2_n3", dropoff="n3_n4"),
                _req("rs", pickup="n1_n2", dropoff="n3_n4"),
            ],
            remaining_capacity=3,
        )
        policy = DeviatedFixedRoutePolicy(
            self.graph, stop_order=order, detour_factor=1.5
        )
        self.assertEqual(len(policy.plan(state, n_stops=1)), 1)


if __name__ == "__main__":
    unittest.main()
