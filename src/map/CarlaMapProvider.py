"""CarlaMapProvider: MapProvider backed by a dumped CARLA town.

Reads two files, both under ``src/map/carla_maps/``: a machine-generated
topology dump (``carla_dump``) and a hand-authored stop list
(``carla_stops``). It imports no ``carla``, so the map layer works with no
server running: the transit graph builds, paths and tours between stops are
costed, and a whole trial can be simulated against the mock backend. Driving
the town needs a server, and so does the lane-level waypoint search in
``carla_routing`` — it takes a live ``carla.Map``.

Two consequences of that layout are worth knowing before reading
``_build``, and both are written up in the docs rather than repeated here —
see *Why segments are split* and *Which edge a stop belongs to* in
``autobus-docs/docs/concepts/map.md``:

* a topology segment carrying stops is **split** at each of them, because a
  ``TransitGraph`` node is a road edge and an edge holds at most one stop;
* a stop attaches to the edge that **begins** at its split node, never the
  one that ends there — the other reading is wrong by exactly one edge
  length on every estimate.
"""

import math

from pathlib import Path

from src.map.MapProvider import MapProvider
from src.map.carla_stops import CarlaStop, load_stops
from src.map.carla_topology import (
    CarlaTopology,
    RoadSegment,
    load_topology,
)
from src.map.map_data import RoadEdgeRecord, RoadNodeRecord, StopRecord

# Endpoints are rounded to a millimetre purely to make a hashable key. This
# is NOT the tolerance at which two segments are judged to meet -- see
# NODE_SNAP_M, which is.
POSITION_PRECISION = 3

# How close two segment endpoints must be to be treated as the same place.
#
# This was a decimetre grid until 2026-08-17, and rounding to a grid is the
# wrong shape for the question: two endpoints a millimetre apart still land
# in different buckets when they straddle a boundary, and the join is lost
# silently. Measured, that split 9 points on Town03 and 4 on Town05 into
# unreachable halves 0.1-0.2 m apart, leaving 16% and 17% of their stop pairs
# routed the long way round -- one Town03 leg was priced at 4.87x its true
# cost. Town01 and Town02 have no such split, which is why it went unnoticed.
#
# Snapping to the nearest already-claimed position has no boundary to
# straddle. 0.25 m is measured rather than guessed: every genuine split sits
# at 0.1-0.2 m, the next-closest distinct pair on Town03 is 0.3 m, and the
# shortest edge on any town is 0.51 m, so this cannot collapse a real edge.
# A point joins an existing position or becomes one, never both, so a cluster
# can never grow wider than this.
NODE_SNAP_M = 0.25

# How far apart two lanes' edges may start and still be neighbours.
# CARLA lanes sit ~3.5 m apart; the window is wide enough for a
# widened lane and far too narrow to reach across a carriageway.
LANE_GAP_M = (1.5, 6.0)

# A split is skipped when it would leave a sub-edge shorter than this, which
# would otherwise put a zero-weight edge in the graph.
MIN_EDGE_M = 0.5


class CarlaMapProvider(MapProvider):
    """
    MapProvider for a CARLA town, built from dumped roads and authored stops.

    :param town: Town name, with or without CARLA's path prefix.
    :param directory: Directory holding the two files. Defaults to the
        packaged ``carla_maps`` directory.
    :raises FileNotFoundError: If either file is missing for this town.
    :raises ValueError: If either file is malformed or names another town.
    """

    def __init__(
        self, town: str = "Town01", directory: Path | None = None
    ) -> None:
        self._topology: CarlaTopology = load_topology(town, directory)
        self._authored: list[CarlaStop] = load_stops(town, directory)
        self._edge_segment: dict[str, RoadSegment] = {}

        self._nodes: list[RoadNodeRecord] = []
        self._edges: list[RoadEdgeRecord] = []
        self._stops: list[StopRecord] = []
        self._problems: list[str] = []
        self._node_ids: dict[tuple[float, float, float], str] = {}

        self._build()

    # ------------------------------------------------------------------
    # MapProvider interface
    # ------------------------------------------------------------------

    def get_stops(self) -> list[StopRecord]:
        """
        Return a StopRecord per authored stop that could be placed.

        Each record's ``edge`` is the **same object** held in
        ``get_road_edges``, not an equal copy. ``TransitGraph`` keys stops by
        the frozen edge record, so a float differing in its last bit would
        silently detach the stop.
        """
        return list(self._stops)

    def get_road_nodes(self) -> list[RoadNodeRecord]:
        """Return every road node: segment endpoints, plus one per stop."""
        return list(self._nodes)

    def get_road_edges(self) -> list[RoadEdgeRecord]:
        """Return every directed road edge, segments split at their stops."""
        return list(self._edges)

    def get_lane_changes(self) -> list[tuple[str, str]]:
        """
        Road edges a bus may legally move sideways between.

        Two edges are lateral neighbours when they belong to adjacent lanes
        of one road, running the same way, and the markings on the origin
        lane permit crossing towards the target. Which side the target lies
        on is decided **geometrically**, from the origin's heading and the
        offset to the target, rather than from lane-ID arithmetic: the
        OpenDRIVE numbering flips meaning between positive and negative
        lanes, and transcribing that by hand is a known way to get it
        backwards.

        A lane change is placed where an edge begins, since that is where the
        graph can represent it. Real changes happen part way along a lane, so
        this under-counts the opportunities rather than inventing any.
        :return: ``(from_edge_id, to_edge_id)`` pairs.
        """
        by_road: dict[int, list[RoadEdgeRecord]] = {}
        for edge in self._edges:
            segment = self._edge_segment.get(edge.id)
            if segment is not None:
                by_road.setdefault(segment.road_id, []).append(edge)

        pairs: list[tuple[str, str]] = []
        for edges in by_road.values():
            for origin in edges:
                pairs.extend(self._lane_changes_from(origin, edges))
        return sorted(set(pairs))

    def _lane_changes_from(
        self, origin: RoadEdgeRecord, on_road: list[RoadEdgeRecord]
    ) -> list[tuple[str, str]]:
        """
        Legal sideways moves leaving one edge.
        :param origin: The edge being left.
        :param on_road: Every edge on the same road.
        :return: ``(from_edge_id, to_edge_id)`` pairs leaving ``origin``.
        """
        here = self._edge_segment[origin.id]
        start = self._node_position(origin.from_node_id)
        heading = self._edge_heading(origin)
        if heading is None:
            return []

        best: dict[int, tuple[float, RoadEdgeRecord]] = {}
        for target in on_road:
            there = self._edge_segment[target.id]
            if target.id == origin.id or there is here:
                continue
            if (there.lane_id > 0) != (here.lane_id > 0):
                continue                    # oncoming carriageway
            if abs(abs(there.lane_id) - abs(here.lane_id)) != 1:
                continue                    # not the next lane over
            offset = self._node_position(target.from_node_id)
            across = (offset[0] - start[0], offset[1] - start[1])
            gap = math.hypot(*across)
            if not LANE_GAP_M[0] <= gap <= LANE_GAP_M[1]:
                continue
            # z of the cross product: positive means the target lies to one
            # consistent side of the heading. Which side that is depends on
            # the handedness of the coordinates, so it is checked against
            # CARLA rather than assumed -- see the lane-change verification.
            side = heading[0] * across[1] - heading[1] * across[0]
            allowed = (here.may_change_left() if side < 0
                       else here.may_change_right())
            if not allowed:
                continue
            # One move per neighbouring lane: the nearest place to cross.
            keep = best.get(there.lane_id)
            if keep is None or gap < keep[0]:
                best[there.lane_id] = (gap, target)
        return [(origin.id, target.id) for _, target in best.values()]

    def _node_position(self, node_id: str) -> tuple[float, float, float]:
        """Coordinates of a road node."""
        node = next(n for n in self._nodes if n.id == node_id)
        return (node.x, node.y, node.z)

    def _edge_heading(self, edge: RoadEdgeRecord) -> tuple[float, float] | None:
        """Unit heading of an edge, or None if it has no length."""
        start = self._node_position(edge.from_node_id)
        end = self._node_position(edge.to_node_id)
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy)
        return (dx / norm, dy / norm) if norm > 1e-6 else None

    def traffic_efficiency(self) -> float:
        """
        Return the fraction of the posted limit a bus averages in this town.

        Read from the town's dump rather than assumed, and 1.0 for a town
        that has never been calibrated — which reproduces the free-flow
        estimates every earlier CARLA run was made under.
        :return: Multiplier on the posted limit, in (0, 1].
        """
        return self._topology.traffic_efficiency

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """
        Return everything wrong with this map, as human-readable lines.

        Covers the failures that are otherwise silent: an authored stop that
        matched no segment or could not be given its own edge, an edge naming
        a node that does not exist, and a node the bus can never leave.
        :return: One line per problem; empty when the map is healthy.
        """
        problems = list(self._problems)

        node_ids = {node.id for node in self._nodes}
        has_exit = {edge.from_node_id for edge in self._edges}

        for edge in self._edges:
            for role, node_id in (("from", edge.from_node_id),
                                  ("to", edge.to_node_id)):
                if node_id not in node_ids:
                    problems.append(
                        f"edge {edge.id} names a {role} node "
                        f"{node_id} that does not exist"
                    )
            if edge.metadata is not None:
                # Would make the record unhashable and break _build_map.
                problems.append(f"edge {edge.id} carries metadata")

        for node in self._nodes:
            if node.id not in has_exit:
                problems.append(
                    f"node {node.id} has no outgoing edge; "
                    f"a bus reaching it can never leave"
                )

        placed = {stop.name for stop in self._stops}
        for authored in self._authored:
            if authored.id not in placed:
                problems.append(f"authored stop {authored.id} was not placed")

        return problems

    @property
    def topology(self) -> CarlaTopology:
        """The dumped topology this provider was built from."""
        return self._topology

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Derive nodes, edges and stops from the topology and the stops."""
        stops_by_segment = self._assign_stops_to_segments()

        # Node IDs are assigned from a sorted position list so that the same
        # dump always produces the same IDs, whatever order segments arrive
        # in. They end up in animations and logs, so stability matters.
        positions = sorted({
            self._position_key(point)
            for segment in self._topology.segments
            for point in (segment.start, segment.end)
        })
        self._node_ids, places = self._snap_positions(positions)
        for node_id, place in places.items():
            self._nodes.append(RoadNodeRecord(
                id=node_id, x=place[0], y=place[1], z=place[2]
            ))

        for index, segment in enumerate(self._topology.segments):
            before = len(self._edges)
            self._split_segment(segment, stops_by_segment.get(index, []))
            for edge in self._edges[before:]:
                self._edge_segment[edge.id] = segment

    def _position_key(self, point: object) -> tuple[float, float, float]:
        """
        Round a point to a hashable key.

        This identifies a point, it does not decide which points are the same
        place -- ``_snap_positions`` does that, at ``NODE_SNAP_M``. The
        rounding here only has to be fine enough never to merge anything on
        its own.
        :param point: A point carrying x, y and z.
        :return: The rounded coordinates.
        """
        return (
            round(point.x, POSITION_PRECISION),
            round(point.y, POSITION_PRECISION),
            round(point.z, POSITION_PRECISION),
        )

    def _snap_positions(
        self, positions: list[tuple[float, float, float]]
    ) -> tuple[dict[tuple[float, float, float], str],
               dict[str, tuple[float, float, float]]]:
        """
        Give every endpoint a node ID, sharing one where they are the same
        place.

        Walks the positions in sorted order and joins each to an already-
        claimed position within ``NODE_SNAP_M``, or claims it as a new one.
        Because a position only ever joins a claimed position and never
        becomes one itself, no cluster can grow wider than the tolerance --
        so this cannot chain across a road.
        :param positions: Every endpoint key, sorted, deduplicated.
        :return: Endpoint key to node ID, and node ID to its coordinates.
        """
        node_ids: dict[tuple[float, float, float], str] = {}
        places: dict[str, tuple[float, float, float]] = {}

        for position in positions:
            for node_id, claimed in places.items():
                if math.dist(position, claimed) <= NODE_SNAP_M:
                    node_ids[position] = node_id
                    break
            else:
                node_id = f"n{len(places):04d}"
                places[node_id] = position
                node_ids[position] = node_id
        return node_ids, places

    def _assign_stops_to_segments(self) -> dict[int, list[CarlaStop]]:
        """
        Work out which segment each authored stop sits on.

        :return: Segment index to the stops on it, in driving order.
        """
        assigned: dict[int, list[CarlaStop]] = {}
        for stop in self._authored:
            matches = [
                index
                for index, segment in enumerate(self._topology.segments)
                if segment.road_id == stop.road_id
                and segment.lane_id == stop.lane_id
                and segment.covers_s(stop.s)
            ]
            if not matches:
                self._problems.append(
                    f"stop {stop.id} (road {stop.road_id}, "
                    f"lane {stop.lane_id}, s {stop.s}) "
                    f"matches no segment"
                )
                continue
            if len(matches) > 1:
                self._problems.append(
                    f"stop {stop.id} matches {len(matches)} segments; "
                    f"using the first"
                )
            assigned.setdefault(matches[0], []).append(stop)

        for index, stops in assigned.items():
            segment = self._topology.segments[index]
            stops.sort(key=lambda s: segment.distance_at_s(s.s))
        return assigned

    def _split_segment(
        self, segment: RoadSegment, stops: list[CarlaStop]
    ) -> None:
        """
        Turn one topology segment into one or more edges.

        :param segment: The segment to split.
        :param stops: Stops on it, already in driving order.
        """
        length = segment.length_m
        start_id = self._node_ids[self._position_key(segment.start)]
        end_id = self._node_ids[self._position_key(segment.end)]

        # A stop owns the edge that BEGINS at its node.
        owner_by_node: dict[str, CarlaStop] = {}
        cut_distances: list[tuple[float, str]] = []

        for stop in stops:
            distance = segment.distance_at_s(stop.s)

            if distance <= MIN_EDGE_M:
                # Already at the segment start; that node serves as the
                # stop's node, so no cut is needed.
                node_id = start_id
            elif distance >= length - MIN_EDGE_M:
                self._problems.append(
                    f"stop {stop.id} sits {length - distance:.2f} m from "
                    f"the end of its segment; too close to own an edge"
                )
                continue
            elif cut_distances and (
                    distance - cut_distances[-1][0] < MIN_EDGE_M):
                self._problems.append(
                    f"stop {stop.id} is within {MIN_EDGE_M} m of the "
                    f"previous stop on the same segment; not placed"
                )
                continue
            else:
                node_id = f"n_{stop.id}"
                cut_distances.append((distance, node_id))
                self._add_node_at(node_id, distance, segment)

            if node_id in owner_by_node:
                self._problems.append(
                    f"stop {stop.id} would share node {node_id} with "
                    f"{owner_by_node[node_id].id}; not placed"
                )
                continue
            owner_by_node[node_id] = stop

        boundaries = (
            [(0.0, start_id)] + cut_distances + [(length, end_id)]
        )

        for (from_distance, from_id), (to_distance, to_id) in zip(
                boundaries, boundaries[1:]):
            if to_distance - from_distance < MIN_EDGE_M:
                continue
            edge = RoadEdgeRecord(
                id=f"{from_id}_{to_id}",
                from_node_id=from_id,
                to_node_id=to_id,
                length_m=to_distance - from_distance,
                speed_limit_kph=self._topology.speed_limit_for(
                    segment.road_id
                ),
                # Must stay None: a dict makes the record unhashable, and
                # TransitGraph keys its stop lookup by the whole record.
                metadata=None,
            )
            self._edges.append(edge)

            owner = owner_by_node.pop(from_id, None)
            if owner is not None:
                self._stops.append(StopRecord(
                    id=edge.id,
                    x=owner.x,
                    y=owner.y,
                    z=owner.z,
                    edge=edge,
                    pos_on_edge=0.0,
                    name=owner.id,
                ))

        for node_id, orphan in owner_by_node.items():
            self._problems.append(
                f"stop {orphan.id} has node {node_id} but no edge "
                f"begins there; not placed"
            )

    def _add_node_at(
        self, node_id: str, distance: float, segment: RoadSegment
    ) -> None:
        """
        Record a node created by splitting a segment.

        Its position is taken from the nearest sampled point, which is within
        half a sampling step of the true split.
        :param node_id: ID of the new node.
        :param distance: Distance along the segment where it sits.
        :param segment: The segment being split.
        """
        totals = segment.cumulative_lengths()
        index = min(
            range(len(totals)), key=lambda i: abs(totals[i] - distance)
        )
        point = segment.points[index]
        self._nodes.append(RoadNodeRecord(
            id=node_id, x=point.x, y=point.y, z=point.z
        ))
