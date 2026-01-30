from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

from flow_lens.engine.state_engine import StateSnapshot

EPSILON = 1e-12


@dataclass(frozen=True)
class MetricSample:
    timestamp_ms: int
    y_raw: float
    y: float
    disp_rate: float
    disp_scale: float
    disp_deadband_active: bool
    e_dir: float
    price_series_used: str
    gate: float


@dataclass(frozen=True)
class LiveMetricsSnapshot:
    y_raw_p95: float
    y_raw_p99: float
    flip_rate_y_raw: float
    flip_rate_y: float
    deadband_active_rate: float
    disp_ratio: float
    e_dir_persistence: int
    price_series_switch_rate: float
    air_pocket_active_rate: float
    sample_count: int
    duration_s: float


class LiveMetrics:
    def __init__(self, *, window_seconds: float = 120.0) -> None:
        self._window_ms = int(window_seconds * 1000)
        self._history: dict[str, Deque[MetricSample]] = {}
        self._dir_sign: dict[str, int] = {}
        self._dir_persist: dict[str, int] = {}

    def update(self, symbol: str, state: StateSnapshot, now_ms: int) -> None:
        key = symbol.upper()
        samples = self._history.setdefault(key, deque())
        samples.append(
            MetricSample(
                timestamp_ms=now_ms,
                y_raw=state.y_raw,
                y=state.y,
                disp_rate=state.disp_rate,
                disp_scale=state.disp_scale,
                disp_deadband_active=state.disp_deadband_active,
                e_dir=state.e_dir,
                price_series_used=state.price_series_used,
                gate=state.gate,
            )
        )
        cutoff = now_ms - self._window_ms
        while samples and samples[0].timestamp_ms < cutoff:
            samples.popleft()

        sign = _sign(state.e_dir)
        last_sign = self._dir_sign.get(key, 0)
        if sign == 0:
            self._dir_persist[key] = 0
        elif sign == last_sign:
            self._dir_persist[key] = self._dir_persist.get(key, 0) + 1
        else:
            self._dir_persist[key] = 1
        self._dir_sign[key] = sign

    def snapshot(self, symbol: str) -> LiveMetricsSnapshot | None:
        key = symbol.upper()
        samples = self._history.get(key)
        if not samples:
            return None
        duration_s = _duration_seconds(samples)
        abs_y_raw = [abs(sample.y_raw) for sample in samples]
        y_raw_p95 = _percentile(abs_y_raw, 0.95)
        y_raw_p99 = _percentile(abs_y_raw, 0.99)
        flip_rate_y_raw = _flip_rate([sample.y_raw for sample in samples], duration_s)
        flip_rate_y = _flip_rate([sample.y for sample in samples], duration_s)
        deadband_active_rate = _ratio(
            sum(1 for sample in samples if sample.disp_deadband_active),
            len(samples),
        )
        last_sample = samples[-1]
        disp_ratio = 0.0
        if last_sample.disp_scale > EPSILON:
            disp_ratio = abs(last_sample.disp_rate) / last_sample.disp_scale
        price_series_switch_rate = _switch_rate(
            [sample.price_series_used for sample in samples], duration_s
        )
        air_pocket_active_rate = _ratio(
            sum(1 for sample in samples if sample.gate < 0.2),
            len(samples),
        )
        return LiveMetricsSnapshot(
            y_raw_p95=y_raw_p95,
            y_raw_p99=y_raw_p99,
            flip_rate_y_raw=flip_rate_y_raw,
            flip_rate_y=flip_rate_y,
            deadband_active_rate=deadband_active_rate,
            disp_ratio=disp_ratio,
            e_dir_persistence=self._dir_persist.get(key, 0),
            price_series_switch_rate=price_series_switch_rate,
            air_pocket_active_rate=air_pocket_active_rate,
            sample_count=len(samples),
            duration_s=duration_s,
        )


def _duration_seconds(samples: Deque[MetricSample]) -> float:
    if len(samples) < 2:
        return 0.0
    return max(0.0, (samples[-1].timestamp_ms - samples[0].timestamp_ms) / 1000.0)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 1:
        return ordered[-1]
    idx = int(round(pct * (len(ordered) - 1)))
    return ordered[idx]


def _flip_rate(values: list[float], duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    flips = 0
    last_sign = 0
    for value in values:
        sign = _sign(value)
        if sign == 0:
            continue
        if last_sign != 0 and sign != last_sign:
            flips += 1
        last_sign = sign
    return flips / (duration_s / 60.0)


def _switch_rate(series: list[str], duration_s: float) -> float:
    if duration_s <= 0 or not series:
        return 0.0
    switches = 0
    last = series[0]
    for current in series[1:]:
        if current != last:
            switches += 1
        last = current
    return switches / (duration_s / 60.0)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _sign(value: float) -> int:
    if value > EPSILON:
        return 1
    if value < -EPSILON:
        return -1
    return 0
