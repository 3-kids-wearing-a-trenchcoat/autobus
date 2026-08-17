from abc import ABC, abstractmethod
from src.map.map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord

class MapProvider(ABC):
    """Abstraction layer used to extract and reduce information from the road map into data
    that can be used by TransitGraph"""

    @abstractmethod
    def get_stops(self) -> list[StopRecord]:
        pass

    @abstractmethod
    def get_road_nodes(self) -> list[RoadNodeRecord]:
        pass

    @abstractmethod
    def get_road_edges(self) -> list[RoadEdgeRecord]:
        pass

    def traffic_efficiency(self) -> float:
        """
        Fraction of the posted speed limit a bus actually averages here.

        Deliberately **not abstract**, and defaulting to 1.0: a map source
        that drives exactly what it predicts — the mock backend on a planar
        graph, which is every synthetic result this project rests on — needs
        no correction and must keep producing identical numbers.

        A real world does not achieve its posted limits. Measured on CARLA's
        Town01, a bus averages 0.57 of them once time stationary at red
        lights is counted, and that is a *bias* rather than noise: execution
        noise there measures under 0.03. It belongs in the estimate, not in a
        safety margin, which is why it is reported by the map source rather
        than configured on a policy.
        :return: Multiplier on the posted limit, in (0, 1].
        """
        return 1.0

    def get_lane_changes(self) -> list[tuple[str, str]]:
        """
        Road edges a vehicle may move sideways between.

        Deciding this needs lane identity and the road markings that govern
        it, which only the map source has — so it is answered here rather
        than re-derived from geometry in TransitGraph.

        Deliberately **not abstract**, and defaulting to none, for the same
        reason as ``traffic_efficiency``: a planar graph has no lanes to
        change between, and every synthetic result this project rests on
        must keep producing identical numbers.
        :return: ``(from_edge_id, to_edge_id)`` pairs, each a legal lateral
            move. Directed: legality is not always symmetric.
        """
        return []

    # TODO: some kind of update method