"""map data objects"""

from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class StopRecord:
    id: str
    x: float
    y: float
    z: float = 0.0
    name: Optional[str] = None

@dataclass(frozen=True)
class RoadNodeRecord:
    id: str
    x: float
    y: float
    z: float = 0.0

@dataclass(frozen=True)
class RoadEdgeRecord:
    id: str
    from_node_id: str
    to_node_id: str
    length_m: float
    speed_limit_kph: Optional[float] = None
