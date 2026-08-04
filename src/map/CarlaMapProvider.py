"""CarlaMapProvider: MapProvider backed by a dumped CARLA town.

Reads two files, both under ``src/map/carla_maps/``: a machine-generated
topology dump (``carla_dump``) and a hand-authored stop list
(``carla_stops``). It imports no ``carla``, so a CARLA map can be routed,
tested and experimented on with no server running.

## Why segments get split

A ``TransitGraph`` node **is** a road edge, and ``_build_map`` keys stops by
edge — so an edge carries at most one stop and a second would be silently
overwritten. CARLA's topology segments run junction to junction and are
routinely hundreds of metres long: four of Town01's fifteen authored stops
sit in pairs on a single segment.

So a segment carrying stops is split at them, giving every stop its own edge.
This is what ``RoadNodeRecord`` already describes — *"any point where the bus
has a decision to make or otherwise needs to change its behavior"*.

## Which edge a stop belongs to

``TransitGraph._calc_weights`` gives edge ``A -> B`` the weight ``length(A)``,
so a shortest path sums every edge except its destination. Arriving at node
``B`` therefore means arriving at the **start** of road-edge ``B``.

A stop is consequently attached to the edge that *begins* at its split node,
never the one that ends there::

    j1 --- e1 ---> n_s08 --- e2 ---> n_s13 --- e3 ---> j2
          134 m           162 m            10 m

    cost to reach e2 == length(e1) == 134 m along the road, which is s08

Attaching it to ``e1`` is the intuitive reading and is wrong by exactly one
edge length on every estimate.
"""

from pathlib import Path

from src.map.MapProvider import MapProvider
from src.map.carla_stops import CarlaStop, load_stops
from src.map.carla_topology import (
    CarlaTopology,
    RoadSegment,
    load_topology,
)
from src.map.map_data import RoadEdgeRecord, RoadNodeRecord, StopRecord

# Positions are matched to a decimetre when deciding whether two segments
# meet. CARLA reports a junction's shared endpoints identically, so this only
# has to absorb float formatting in the dump.
POSITION_PRECISION = 1

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
        self._node_ids = {
            key: f"n{index:04d}" for index, key in enumerate(positions)
        }
        for key, node_id in self._node_ids.items():
            self._nodes.append(RoadNodeRecord(
                id=node_id, x=key[0], y=key[1], z=key[2]
            ))

        for index, segment in enumerate(self._topology.segments):
            self._split_segment(segment, stops_by_segment.get(index, []))

    def _position_key(self, point: object) -> tuple[float, float, float]:
        """Round a point to the precision at which segments are joined."""
        return (
            round(point.x, POSITION_PRECISION),
            round(point.y, POSITION_PRECISION),
            round(point.z, POSITION_PRECISION),
        )

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
