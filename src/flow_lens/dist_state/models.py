from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DistTimeframe = Literal["3m", "15m", "1h", "4h"]
DistAvailabilityMode = Literal["strict", "continuous"]
DistTimeMissingPolicy = Literal["reject"]
DistRowToken = Literal["COMP", "EXP", "CONT↑", "CONT↓", "EXH↑", "EXH↓", "REVERT", "NEUT"]
NarrativeScalar = str | int | float | bool | None
NarrativeParamValue = NarrativeScalar | list[str] | dict[str, float]


@dataclass(frozen=True)
class DistOiSamplerSnapshot:
    oi: float
    venue_time_ms: int | None
    ts_recv_ms: int
    sample_seq: int


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
    sampler_snapshot: DistOiSamplerSnapshot | None = None
    verify_snapshot: DistOiSamplerSnapshot | None = None


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
    tokens_enabled: bool = False
    narrative_state_id: str | None = None
    narrative_template_id: str | None = None
    narrative_params: dict[str, NarrativeParamValue] | None = None
    narrative_as_of_close_ms: int | None = None
    narrative_driver_tf: DistTimeframe | None = None
    narrative_started_close_ms: int | None = None
    narrative_age_closes: int | None = None
    narrative_reason_codes: list[str] | None = None
    narrative_quality_flags: list[str] | None = None
    narrative_text_template: str | None = None
    narrative_text_agent: str | None = None
