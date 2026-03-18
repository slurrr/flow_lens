from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

SideType = Literal["spot", "perp"]
AggressorSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class EffortContribution:
    source_id: str
    side_type: SideType
    aggressor_side: AggressorSide
    effort_value: float


@dataclass(frozen=True)
class FlowFrame:
    symbol: str
    timestamp: int
    price: float
    price_start: float
    window_seconds: float
    active_price_source_id: str | None
    selector_policy: str
    price_series_side: str
    price_series_used: str
    spot_fresh: bool
    perp_fresh: bool
    spot_event_count_window: int
    perp_event_count_window: int
    last_spot_event_ts: int | None
    last_perp_event_ts: int | None
    e_dir: float
    efforts: Sequence[EffortContribution]
