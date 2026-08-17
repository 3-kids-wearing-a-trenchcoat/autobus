"""map data objects"""

from dataclasses import dataclass
from typing import Optional, Any

DEFAULT_SPEED_LIMIT = 130.0 # if speed limit is unspecified, use this value as the speed limit

# How far a bus travels forward while moving one lane sideways. A lane change
# is a diagonal, not a teleport, and both the graph and the CARLA route
# planner have to charge it the same way or the bus beats its own estimates.
LANE_CHANGE_RAMP_M = 15.0

# Charged on top of the ramp. Without it a lane change is nearly free and a
# shortest-path search will weave between lanes to save a metre, planning a
# trajectory no bus would drive. Set to a large number to price lane changes
# out entirely and recover the behaviour from before they existed.
LANE_CHANGE_PENALTY_S = 3.0

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
    speed_limit_kph: Optional[float] = None
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