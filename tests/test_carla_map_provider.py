"""Tests for CarlaMapProvider.

Three properties of TransitGraph._build_map fail *silently* rather than
loudly, and each gets a dedicated test here:

1. a TransitGraph node IS a road edge and holds at most one stop, so a
   segment carrying two stops must be split or one is lost;
2. edge A -> B is weighted by length(A), so arriving at a node means
   arriving at the *start* of its edge -- a stop belongs to the edge that
   begins at its split node, not the one that ends there;
3. the stop lookup is keyed by the frozen RoadEdgeRecord, so the record in
   get_stops must be the same object as in get_road_edges, and metadata must
   be None or the record is unhashable.

Everything runs offline against hand-built or dumped files.
"""

from pathlib import Path

import networkx as nx
import pytest

from src.algorithm.TravelModel import GraphTravelModel
from src.map.CarlaMapProvider import CarlaMapProvider
from src.map.TransitGraph import TransitGraph
from src.map.carla_stops import CarlaStop, save_stops
from src.map.carla_topology import (
    CarlaTopology,
    RoadSegment,
    SegmentPoint,
    save_topology,
)

# 30 km/h in metres per second, matching the town default.
_SPEED_MPS = 30.0 * 1000.0 / 3600.0


def _straight(
    road_id: int,
    lane_id: int,
    start: tuple[float, float],
    end: tuple[float, float],
    step: float = 10.0,
) -> RoadSegment:
    """
    Build a straight segment sampled every `step` metres, with s along it.
    :param road_id: OpenDRIVE road ID.
    :param lane_id: OpenDRIVE lane ID.
    :param start: (x, y) of the segment start.
    :param end: (x, y) of the segment end.
    :param step: Sampling interval in metres.
    :return: The segment.
    """
    length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    count = max(int(length // step), 1)
    points = []
    for index in range(count + 1):
        fraction = min(index * step / length, 1.0)
        points.append(SegmentPoint(
            x=start[0] + fraction * (end[0] - start[0]),
            y=start[1] + fraction * (end[1] - start[1]),
            z=0.0,
            s=fraction * length,
        ))
    if points[-1].s < length:
        points.append(SegmentPoint(x=end[0], y=end[1], z=0.0, s=length))
    return RoadSegment(road_id, lane_id, False, tuple(points))


def _write_map(
    directory: Path,
    segments: list[RoadSegment],
    stops: list[CarlaStop],
    town: str = "TownTest",
) -> CarlaMapProvider:
    """Write a topology and stop file, then build a provider over them."""
    save_topology(CarlaTopology(
        map=town,
        default_speed_limit_kph=30.0,
        speed_limit_overrides={},
        segments=tuple(segments),
    ), directory)
    save_stops(town, stops, directory)
    return CarlaMapProvider(town, directory)


def _loop(directory: Path, stops: list[CarlaStop]) -> CarlaMapProvider:
    """
    A closed two-road loop, so every node is reachable from every other.

        road 1 lane -1:  (0,0) --200m--> (200,0)
        road 2 lane -1:  (200,0) --200m--> (0,0)
    """
    return _write_map(directory, [
        _straight(1, -1, (0.0, 0.0), (200.0, 0.0)),
        _straight(2, -1, (200.0, 0.0), (0.0, 0.0)),
    ], stops)


def _stop(identifier: str, road_id: int, s: float, lane_id: int = -1
          ) -> CarlaStop:
    """Build an authored stop on a road."""
    return CarlaStop(
        id=identifier, road_id=road_id, lane_id=lane_id, s=s,
        x=s, y=0.0, z=0.0,
    )


# ---------------------------------------------------------------------------
# Trap 1 — two stops on one segment must both survive
# ---------------------------------------------------------------------------

def test_two_stops_on_one_segment_both_survive(tmp_path: Path) -> None:
    """
    The headline regression. Four of Town01's fifteen authored stops sit in
    pairs on a single topology segment; without splitting, TransitGraph keeps
    only one per edge and the other vanishes with no error at all.
    """
    provider = _loop(tmp_path, [
        _stop("sA", 1, 50.0), _stop("sB", 1, 150.0),
    ])
    graph = TransitGraph(provider)
    assert sorted(s.name for s in graph.get_stops()) == ["sA", "sB"]


def test_splitting_creates_one_edge_per_stop(tmp_path: Path) -> None:
    """A 200 m road with two stops becomes three edges."""
    provider = _loop(tmp_path, [
        _stop("sA", 1, 50.0), _stop("sB", 1, 150.0),
    ])
    road_one = [
        edge for edge in provider.get_road_edges()
        if edge.length_m < 199.0
    ]
    assert len(road_one) == 3
    assert sorted(e.length_m for e in road_one) == pytest.approx(
        [50.0, 50.0, 100.0]
    )


def test_a_segment_without_stops_is_left_whole(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    lengths = sorted(e.length_m for e in provider.get_road_edges())
    assert lengths == pytest.approx([50.0, 150.0, 200.0])


def test_three_stops_on_one_segment_all_survive(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [
        _stop("sA", 1, 40.0), _stop("sB", 1, 90.0), _stop("sC", 1, 160.0),
    ])
    graph = TransitGraph(provider)
    assert len(graph.get_stops()) == 3


def test_stop_order_within_a_segment_does_not_matter(tmp_path: Path) -> None:
    """Authoring order is arbitrary; the split must sort by position."""
    forwards = _loop(tmp_path / "a", [
        _stop("sA", 1, 50.0), _stop("sB", 1, 150.0),
    ])
    backwards = _loop(tmp_path / "b", [
        _stop("sB", 1, 150.0), _stop("sA", 1, 50.0),
    ])
    assert (sorted(e.id for e in forwards.get_road_edges())
            == sorted(e.id for e in backwards.get_road_edges()))


# ---------------------------------------------------------------------------
# Trap 2 — the stop owns the edge that BEGINS at its node
# ---------------------------------------------------------------------------

def test_stop_owns_the_edge_beginning_at_its_node(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    stop = provider.get_stops()[0]
    assert stop.edge.from_node_id == "n_sA"


def test_arrival_cost_equals_the_distance_to_the_stop(
    tmp_path: Path,
) -> None:
    """
    The numeric statement of trap 2, and the one that would catch an
    off-by-one-edge error. best_path_cost sums every edge except the
    destination, so reaching the stop's edge from the road's first edge must
    cost exactly the 50 m before it -- not the 150 m after.
    """
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    graph = TransitGraph(provider)
    stop = graph.get_stops()[0]

    first_edge = next(
        edge for edge in provider.get_road_edges()
        if edge.to_node_id == "n_sA"
    )
    cost = graph.best_path_cost(first_edge.id, stop.edge.id)
    assert cost == pytest.approx(50.0 / _SPEED_MPS)


def test_travel_between_two_stops_is_their_road_distance(
    tmp_path: Path,
) -> None:
    """Stops 100 m apart on one road must be 100 m apart to the model."""
    provider = _loop(tmp_path, [
        _stop("sA", 1, 50.0), _stop("sB", 1, 150.0),
    ])
    graph = TransitGraph(provider)
    model = GraphTravelModel(graph)
    by_name = {s.name: s.edge.id for s in graph.get_stops()}
    assert model.travel_time(by_name["sA"], by_name["sB"]) == pytest.approx(
        100.0 / _SPEED_MPS
    )


def test_stop_position_on_its_edge_is_the_start(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    assert provider.get_stops()[0].pos_on_edge == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Trap 3 — record identity and hashability
# ---------------------------------------------------------------------------

def test_stop_edge_is_the_same_object_as_in_road_edges(
    tmp_path: Path,
) -> None:
    """
    Equality would be enough for TransitGraph today, but only because the
    floats happen to match. Sharing the object makes it structural.
    """
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    edges = provider.get_road_edges()
    assert all(any(edge is stop.edge for edge in edges)
               for stop in provider.get_stops())


def test_every_edge_is_hashable(tmp_path: Path) -> None:
    """metadata must stay None: a dict makes _build_map raise TypeError."""
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    for edge in provider.get_road_edges():
        assert edge.metadata is None
        hash(edge)


def test_stop_id_is_its_edge_id(tmp_path: Path) -> None:
    """
    run_trial passes stops[0].id as the bus's starting position, which must
    be a TransitGraph node ID. PlanarMapProvider has the same property.
    """
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    stop = provider.get_stops()[0]
    assert stop.id == stop.edge.id
    assert stop.id in TransitGraph(provider).map.nodes


def test_authored_id_is_kept_as_the_name(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    assert provider.get_stops()[0].name == "sA"


# ---------------------------------------------------------------------------
# Speed limits
# ---------------------------------------------------------------------------

def test_edges_carry_the_town_speed_limit(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sA", 1, 50.0)])
    assert all(edge.speed_limit_kph == pytest.approx(30.0)
               for edge in provider.get_road_edges())


def test_a_road_override_reaches_its_edges(tmp_path: Path) -> None:
    save_topology(CarlaTopology(
        map="TownTest", default_speed_limit_kph=30.0,
        speed_limit_overrides={2: 90.0},
        segments=(
            _straight(1, -1, (0.0, 0.0), (200.0, 0.0)),
            _straight(2, -1, (200.0, 0.0), (0.0, 0.0)),
        ),
    ), tmp_path)
    save_stops("TownTest", [_stop("sA", 1, 50.0)], tmp_path)
    provider = CarlaMapProvider("TownTest", tmp_path)

    limits = {edge.speed_limit_kph for edge in provider.get_road_edges()}
    assert limits == {30.0, 90.0}


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

def test_a_healthy_map_reports_nothing(tmp_path: Path) -> None:
    assert _loop(tmp_path, [_stop("sA", 1, 50.0)]).validate() == []


def test_a_stop_on_a_missing_road_is_reported(tmp_path: Path) -> None:
    """Silent omission is exactly what validate exists to prevent."""
    provider = _loop(tmp_path, [_stop("sZ", 99, 50.0)])
    problems = provider.validate()
    assert any("sZ" in line and "matches no segment" in line
               for line in problems)
    assert provider.get_stops() == []


def test_a_stop_beyond_the_road_length_is_reported(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [_stop("sZ", 1, 999.0)])
    assert any("sZ" in line for line in provider.validate())


def test_a_stop_at_the_very_end_of_its_segment_is_reported(
    tmp_path: Path,
) -> None:
    """
    There is no edge beginning at a segment's end within that segment, so
    such a stop cannot own one. Authoring keeps 10 m of clearance, but the
    provider must say so rather than lose the stop quietly.
    """
    provider = _loop(tmp_path, [_stop("sZ", 1, 199.9)])
    assert any("sZ" in line for line in provider.validate())


def test_two_stops_at_the_same_point_are_reported(tmp_path: Path) -> None:
    provider = _loop(tmp_path, [
        _stop("sA", 1, 50.0), _stop("sB", 1, 50.0),
    ])
    assert any("sB" in line for line in provider.validate())
    assert len(provider.get_stops()) == 1


# ---------------------------------------------------------------------------
# The packaged Town01 map
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def town01() -> CarlaMapProvider:
    """The committed Town01 dump plus its authored stops."""
    return CarlaMapProvider("Town01")


def test_town01_is_healthy(town01: CarlaMapProvider) -> None:
    assert town01.validate() == []


def test_town01_keeps_all_fifteen_stops(town01: CarlaMapProvider) -> None:
    """
    The end-to-end version of trap 1: four of these sit in pairs on a single
    topology segment, so a regression in splitting shows up here.
    """
    graph = TransitGraph(town01)
    assert len(graph.get_stops()) == 15
    assert sorted(s.name for s in graph.get_stops()) == [
        f"s{n:02d}" for n in range(1, 16)
    ]


def test_town01_stops_are_mutually_reachable(
    town01: CarlaMapProvider,
) -> None:
    """Through the TransitGraph itself, not a scratch graph beside it."""
    graph = TransitGraph(town01)
    stop_ids = [stop.edge.id for stop in graph.get_stops()]
    for origin in stop_ids:
        for destination in stop_ids:
            if origin != destination:
                graph.best_path_cost(origin, destination)


def test_town01_is_one_strongly_connected_component(
    town01: CarlaMapProvider,
) -> None:
    graph = TransitGraph(town01)
    components = list(nx.strongly_connected_components(graph.map))
    assert len(components) == 1


def test_town01_leg_times_are_plausible(
    town01: CarlaMapProvider,
) -> None:
    """
    A sanity band, not a measurement. Wildly short legs mean the speed limit
    fell through to DEFAULT_SPEED_LIMIT's 130 km/h; wildly long ones mean the
    geometry is wrong. The spike estimated a median around 90 s.
    """
    graph = TransitGraph(town01)
    model = GraphTravelModel(graph)
    stop_ids = [stop.edge.id for stop in graph.get_stops()]
    legs = sorted(
        model.travel_time(a, b)
        for a in stop_ids for b in stop_ids if a != b
    )
    median = legs[len(legs) // 2]
    assert 40.0 < median < 150.0, f"median leg {median:.0f} s"


def test_town01_stop_ids_are_graph_nodes(
    town01: CarlaMapProvider,
) -> None:
    graph = TransitGraph(town01)
    for stop in town01.get_stops():
        assert stop.id in graph.map.nodes


# ---------------------------------------------------------------------------
# Trap 4 -- endpoints that are the same place must share one node
#
# Node identity was a decimetre grid until 2026-08-17. Two endpoints that
# straddle a bucket boundary then get separate IDs, the segments never join,
# and the graph is silently poorer than the road it describes: measured, that
# left 9 points on Town03 and 4 on Town05 split into halves 0.1-0.2 m apart,
# routing 16% and 17% of their stop pairs the long way round.
# ---------------------------------------------------------------------------

def test_endpoints_a_fraction_apart_are_one_node(tmp_path: Path) -> None:
    """
    A join must survive the second segment starting a few centimetres off.

    0.15 m is inside NODE_SNAP_M and, importantly, straddles a decimetre
    boundary -- 199.95 and 200.10 round to 200.0 and 200.1 -- which is
    exactly the case the old grid lost.
    """
    provider = _write_map(tmp_path, [
        _straight(1, -1, (0.0, 0.0), (199.95, 0.0)),
        _straight(2, -1, (200.10, 0.0), (400.0, 0.0)),
    ], [])

    graph = TransitGraph(provider)
    edges = provider.get_road_edges()
    first = [edge for edge in edges if edge.from_node_id == "n0000"]
    assert first, "the first segment should start at the first node"

    # The join is what matters: the node road 1 ends at must be the node
    # road 2 starts from, so the far end is reachable from the near one.
    joined = {edge.to_node_id for edge in edges} & {
        edge.from_node_id for edge in edges}
    assert joined, "the two segments should meet at a shared node"
    assert nx.has_path(graph.map, first[0].id, edges[-1].id), \
        "the far segment should be reachable across the join"


def test_a_gap_wider_than_the_tolerance_stays_two_nodes(tmp_path: Path
                                                        ) -> None:
    """
    Snapping must not weld genuinely separate places together.

    A metre is far wider than any split measured on a real town, and welding
    at that range would invent transitions the road does not have.
    """
    provider = _write_map(tmp_path, [
        _straight(1, -1, (0.0, 0.0), (200.0, 0.0)),
        _straight(2, -1, (201.0, 0.0), (400.0, 0.0)),
    ], [])

    edges = provider.get_road_edges()
    assert not ({edge.to_node_id for edge in edges}
                & {edge.from_node_id for edge in edges}), \
        "a metre apart is two places, not one"


def test_snapping_cannot_chain_across_a_road(tmp_path: Path) -> None:
    """
    Three points each within the tolerance of the next must not collapse
    into one: a position joins a claimed position or becomes one, never
    both, so no cluster grows wider than NODE_SNAP_M.
    """
    provider = _write_map(tmp_path, [
        _straight(1, -1, (0.0, 0.0), (200.00, 0.0)),
        _straight(2, -1, (200.20, 0.0), (300.0, 0.0)),
        _straight(3, -1, (200.40, 0.0), (400.0, 0.0)),
    ], [])

    starts = {edge.from_node_id for edge in provider.get_road_edges()}
    # Roads 2 and 3 start 0.4 m apart -- beyond the tolerance from each
    # other, so they must not have been chained through road 1's end.
    assert len(starts) >= 2
