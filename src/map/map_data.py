"""map data objects"""

from dataclasses import dataclass
from typing import Optional, Any

DEFAULT_SPEED_LIMIT = 130.0 # if speed limit is unspecified, use this value as the speed limit

@dataclass(frozen=True)
class RoadNodeRecord:
    """Road node data.
    A road node is any point on the map where the bus has a decision to make regarding routes
    or otherwise needs to change its behavior. (speed limit change, intersections, map entry/exit, etc.)"""
    id: str
    x: float
    y: float
    z: float = 0.0
    metadata: Optional[dict[str, Any]] = None

@dataclass(frozen=True)
class RoadEdgeRecord:
    """Road edge data.
    A road edge is an uninterrupted stretch of road where the bus's behavior doesn't change."""
    id: str
    from_node_id: str
    to_node_id: str
    length_m: float
    speed_limit_kph: float = DEFAULT_SPEED_LIMIT
    metadata: Optional[dict[str, Any]] = None

@dataclass(frozen=True)
class StopRecord:
    """Bus stop data"""
    id: str
    x: float
    y: float
    edge: RoadEdgeRecord
    pos_on_edge: float
    z: float = 0.0
    name: Optional[str] = None