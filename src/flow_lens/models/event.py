from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideType = Literal["spot", "perp"]
AggressorSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class Event:
    timestamp: int
    source_id: str
    side_type: SideType
    aggressor_side: AggressorSide
    effort_value: float
    price: float
    venue_timestamp_ms: int | None = None
    trade_id: str | None = None
