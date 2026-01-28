from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

SideType = Literal["spot", "perp"]


@dataclass(frozen=True)
class EffortContribution:
    source_id: str
    side_type: SideType
    effort_value: float


@dataclass(frozen=True)
class FlowFrame:
    symbol: str
    timestamp: int
    price: float
    price_start: float
    efforts: Sequence[EffortContribution]
