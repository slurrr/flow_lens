from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DistTimeframe = Literal["3m", "15m", "1h", "4h"]


@dataclass(frozen=True)
class DistKlineCloseEvent:
    ts_recv_ms: int
    symbol: str
    source_id: str
    tf: DistTimeframe
    kline_open_ms: int
    kline_close_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class DistOiSnapshotEvent:
    ts_recv_ms: int
    symbol: str
    source_id: str
    oi: float
    venue_time_ms: int | None


@dataclass(frozen=True)
class DistRowMetrics:
    v: float | None
    s: float | None
    a: float | None
    p: float | None
    t: float | None


@dataclass(frozen=True)
class DistRowBins:
    v: int | None
    s: int | None
    a: int | None
    p: int | None
    t: int | None


@dataclass(frozen=True)
class DistRowSnapshot:
    tf: DistTimeframe
    ready_core: bool
    ready_p: bool
    last_close_ms: int | None
    metrics: DistRowMetrics
    bins: DistRowBins
    token: str | None = None
    token_strength: str | None = None
    narrative_hint: str | None = None


@dataclass(frozen=True)
class DistPanelSnapshot:
    symbol: str
    source_id: str
    rows: dict[DistTimeframe, DistRowSnapshot]
    last_oi_ts_recv_ms: int | None
    last_oi_value: float | None
