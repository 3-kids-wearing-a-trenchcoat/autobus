"""Tests for MarginalCostFilterPolicy."""

import unittest

from src.algorithm.DispatchPolicy import BusState
from src.algorithm.MarginalCostFilterPolicy import MarginalCostFilterPolicy
from src.algorithm.NearestUnservedPolicy import NearestUnservedPolicy
from src.algorithm.RequestPool import Request
from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord
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


class TestMarginalCostFilterPolicy(unittest.TestCase):
    """
    Verify MarginalCostFilterPolicy on the hand-authored map from
    test_transit_graph.py.

    Relevant travel costs (length / speed limit, so small fractional values):
      From "n4_n1":  _S1 costs ~0.083,  _S2 costs ~0.222.
      Stop-to-stop:  _S1 <-> _S2 costs ~0.194 each way, so the auto baseline
      (median nearest-neighbor) is ~0.194 and the default auto threshold is
      ~0.388.
    """

    def setUp(self) -> None:
        self.graph = TransitGraph(_PROVIDER)

    def test_far_pickup_skipped(self) -> None:
        """A pending pickup beyond the threshold is skipped (stays pending)."""
        policy = MarginalCostFilterPolicy(self.graph, max_pickup_cost=0.15)
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S2, dropoff=_S1)],
            remaining_capacity=3,
        )
        # _S2 costs ~0.222 > 0.15 → filtered out; no other candidates.
        self.assertEqual(policy.plan(state, n_stops=3), [])

    def test_near_pickup_served(self) -> None:
        """A pending pickup within the threshold is served."""
        policy = MarginalCostFilterPolicy(self.graph, max_pickup_cost=0.15)
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        # _S1 costs ~0.083 <= 0.15 → served; dropoff _S2 follows (unfiltered).
        self.assertEqual(policy.plan(state, n_stops=3), [_S1, _S2])

    def test_dropoff_never_filtered(self) -> None:
        """An onboard dropoff is served even when far beyond the threshold."""
        policy = MarginalCostFilterPolicy(self.graph, max_pickup_cost=0.01)
        state = BusState(
            position="n4_n1",
            onboard=[_req("r1", pickup="n4_n1", dropoff=_S2)],
            pending_requests=[],
            remaining_capacity=2,
        )
        # _S2 costs ~0.222 >> 0.01, but dropoffs bypass the pickup cost filter.
        self.assertEqual(policy.plan(state, n_stops=3), [_S2])

    def test_high_threshold_matches_nearest_unserved(self) -> None:
        """With a very high threshold, behaviour matches NearestUnserved."""
        filtered = MarginalCostFilterPolicy(
            self.graph, max_pickup_cost=1000
        )
        baseline = NearestUnservedPolicy(self.graph)
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        self.assertEqual(
            filtered.plan(state, n_stops=5),
            baseline.plan(state, n_stops=5),
        )

    def test_auto_threshold(self) -> None:
        """Auto-derivation yields the expected baseline and serves near demand."""
        policy = MarginalCostFilterPolicy(self.graph)
        # median nearest-neighbor stop cost ~0.194; default factor 2.0 → ~0.388.
        self.assertAlmostEqual(policy._threshold, 0.3877, places=4)
        state = BusState(
            position="n4_n1",
            onboard=[],
            pending_requests=[_req("r1", pickup=_S1, dropoff=_S2)],
            remaining_capacity=3,
        )
        self.assertEqual(policy.plan(state, n_stops=3)[0], _S1)

    def test_auto_threshold_requires_two_stops(self) -> None:
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
            MarginalCostFilterPolicy(TransitGraph(one_stop_provider))


if __name__ == "__main__":
    unittest.main()
