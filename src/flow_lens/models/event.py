from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideType = Literal["spot", "perp"]


@dataclass(frozen=True)
class Event:
    timestamp: int
    source_id: str
    side_type: SideType
    effort_value: float
    price: float
