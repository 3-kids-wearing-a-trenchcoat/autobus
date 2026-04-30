from abc import ABC, abstractmethod
from map_data import StopRecord, RoadNodeRecord, RoadEdgeRecord

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
