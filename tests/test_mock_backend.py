"""Tests for MockBackend."""

import pytest
from unittest.mock import MagicMock

from src.algorithm.BusAgent import BusAgent
from src.algorithm.RequestPool import RequestPool
from src.map.StaticMapProvider import StaticMapProvider, StopInput
from src.map.map_data import RoadNodeRecord
from src.metrics.MetricsTracker import MetricsTracker
from src.simulation.DemandGenerator import DemandGenerator
from src.simulation.MockBackend import MockBackend


def test_arrival_fires_at_multihop_graph_travel_time() -> None:
    """
    on_arrival fires at the time derived from a multi-hop Dijkstra path.

    Graph: n1 ──1300m──► n2 ──1300m──► n3 ──1300m──► n4
    TransitGraph nodes: "n1_n2", "n2_n3", "n3_n4"

    Bus starts at "n1_n2"; next stop is "n3_n4".  There is no direct
    TransitGraph edge between them — Dijkstra must route through "n2_n3".
    At DEFAULT_SPEED_LIMIT (130 km/h), each 1300 m segment takes exactly 36 s,
    so the expected arrival time is 72 s.
    """
    provider = StaticMapProvider(
        stops=[
            StopInput("s1", "n1", "n2", 0.5),
            StopInput("s3", "n3", "n4", 0.5),
        ],
        nodes=[
            RoadNodeRecord("n1", x=0.0, y=0.0),
            RoadNodeRecord("n2", x=1300.0, y=0.0),
            RoadNodeRecord("n3", x=2600.0, y=0.0),
            RoadNodeRecord("n4", x=3900.0, y=0.0),
        ],
        edges=[("n1", "n2"), ("n2", "n3"), ("n3", "n4")],
    )

    agent = MagicMock(spec=BusAgent)
    agent.position = "n1_n2"
    # Call sequence:
    #   1 — __init__ → _compute_next_travel_time
    #   2 — _process_movement loop check
    #   3 — post-arrival _compute_next_travel_time (None → no more stops)
    agent.next_stop.side_effect = ["n3_n4", "n3_n4", None]

    backend = MockBackend(
        agent=agent,
        demand_generator=MagicMock(spec=DemandGenerator),
        metrics_tracker=MagicMock(spec=MetricsTracker),
        pool=MagicMock(spec=RequestPool),
        provider=provider,
    )

    backend.step(72.0)

    agent.on_arrival.assert_called_once_with("n3_n4", pytest.approx(72.0))
