from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Deque, Iterable, Mapping

from flow_lens.engine.constants import Defaults
from flow_lens.engine.dispersion import hill_number_dispersion, update_halo
from flow_lens.models.flow_frame import EffortContribution, FlowFrame

EPSILON = 1e-9


@dataclass(frozen=True)
class StateSnapshot:
    e_spot: float
    e_perp: float
    e_dir: float
    e_spot_share: float
    x_raw: float
    y_raw: float
    y_gated: float
    x: float
    y: float
    persist_enabled: bool
    persist_raw: float
    persist_slope: float
    persist_sign: int
    size_raw: float
    size_bin: int
    halo_raw: float
    halo: float
    halo_bin: int
    lean: tuple[int, int] | None
    dominance: float
    total_effort: float
    effort_floor: float
    effort_median: float
    effort_norm: float
    gate: float
    eff_raw: float
    disp: float
    log_return: float
    price_start: float
    price_end: float
    window_seconds: float
    disp_rate: float
    effort_rate: float
    disp_scale: float
    effort_scale: float
    disp_deadband_active: bool
    spot_fresh: bool
    perp_fresh: bool
    price_series_used: str
    spot_event_count_window: int
    perp_event_count_window: int
    last_spot_event_ts: int | None
    last_perp_event_ts: int | None
    source_count_active: int
    max_source_share: float
    top_source_id: str | None
    top_source_effort: float


class StateEngine:
    def __init__(self, defaults: Defaults | None = None) -> None:
        self._defaults = defaults if defaults is not None else Defaults()
        self._recent_effort: Deque[tuple[int, float]] = deque()
        self._recent_disp_rate: Deque[tuple[int, float]] = deque()
        self._disp_scale_cache: float | None = None
        self._x_smoothed: float | None = None
        self._y_smoothed: float | None = None
        self._persist_state: float = 0.0
        self._persist_last_ts_ms: int | None = None
        self._halo: float | None = None
        self._size_bin: int | None = None
        self._halo_bin: int | None = None
        self._lean_dir: tuple[int, int] | None = None
        self._lean_frames_remaining: int = 0

    def compute(
        self,
        frame: FlowFrame,
        *,
        dispersion_sources: Mapping[str, float] | None = None,
    ) -> StateSnapshot:
        e_spot, e_perp, per_source = _aggregate_efforts(frame.efforts)
        e_dir = frame.e_dir
        total_effort = e_spot + e_perp
        dominance = e_spot - e_perp

        window_seconds = max(frame.window_seconds, EPSILON)
        log_return = _log_return(frame.price_start, frame.price) if frame.price_start > 0 else 0.0
        disp_rate = log_return / (window_seconds + EPSILON)
        effort_rate = total_effort / (window_seconds + EPSILON)
        self._update_scales(frame.timestamp, abs(disp_rate), effort_rate)

        effort_median = self._median_recent(self._recent_effort)
        effort_floor = effort_median * self._defaults.effort_floor.multiplier_alpha
        effort_scale = self._effort_scale()
        disp_scale = self._disp_scale()
        disp_threshold = self._defaults.effectiveness_deadband.disp_scale_multiplier * disp_scale
        disp_deadband_active = abs(disp_rate) <= disp_threshold
        disp_rate_dir = 0.0 if disp_deadband_active else _sign(e_dir) * disp_rate
        effort_norm = effort_rate / (effort_scale + EPSILON)
        gate = self._effort_gate(effort_rate)
        x_raw = _clamp(self._dominance_ratio(dominance, total_effort), -1.0, 1.0)
        disp = disp_rate_dir
        eff_raw = self._effectiveness_ratio(disp_rate_dir, disp_scale, effort_rate, effort_scale)
        y_raw = self._apply_tanh(eff_raw)
        y_gated = gate * y_raw
        persist_enabled = self._defaults.persistence.enabled
        persist_raw, persist_slope, persist_sign = self._update_persistence(
            frame.timestamp,
            y_raw,
            window_seconds,
        )

        prev_x = self._x_smoothed
        prev_y = self._y_smoothed
        x = _smooth(prev_x, x_raw, self._defaults.smoothing.dominance_alpha)
        y = _smooth(prev_y, y_gated, self._defaults.smoothing.effectiveness_alpha)
        self._x_smoothed = x
        self._y_smoothed = y

        size_raw = _clamp(self._force_magnitude(dominance, total_effort), 0.0, 1.0)
        size_bin = _bin_with_hysteresis(
            size_raw,
            self._size_bin,
            self._defaults.binning.dot_size_thresholds,
            self._defaults.binning.hysteresis_band,
        )
        self._size_bin = size_bin

        halo_sources = per_source if dispersion_sources is None else dispersion_sources
        halo_raw = hill_number_dispersion(halo_sources)
        halo = self._update_halo(halo_raw)
        halo_bin = _bin_with_hysteresis(
            halo,
            self._halo_bin,
            self._defaults.binning.halo_thresholds,
            self._defaults.binning.hysteresis_band,
        )
        self._halo_bin = halo_bin

        lean = self._derive_lean(prev_x, prev_y, x, y)

        source_count_active, max_source_share, top_source_id, top_source_effort = _source_stats(
            halo_sources
        )
        return StateSnapshot(
            e_spot=e_spot,
            e_perp=e_perp,
            e_dir=e_dir,
            e_spot_share=e_spot / (total_effort + EPSILON),
            x_raw=x_raw,
            y_raw=y_raw,
            y_gated=y_gated,
            x=x,
            y=y,
            persist_enabled=persist_enabled,
            persist_raw=persist_raw,
            persist_slope=persist_slope,
            persist_sign=persist_sign,
            size_raw=size_raw,
            size_bin=size_bin,
            halo_raw=halo_raw,
            halo=halo,
            halo_bin=halo_bin,
            lean=lean,
            dominance=dominance,
            total_effort=total_effort,
            effort_floor=effort_floor,
            effort_median=effort_median,
            effort_norm=effort_norm,
            gate=gate,
            eff_raw=eff_raw,
            disp=disp,
            log_return=log_return,
            price_start=frame.price_start,
            price_end=frame.price,
            window_seconds=window_seconds,
            disp_rate=disp_rate,
            effort_rate=effort_rate,
            disp_scale=disp_scale,
            effort_scale=effort_scale,
            disp_deadband_active=disp_deadband_active,
            spot_fresh=frame.spot_fresh,
            perp_fresh=frame.perp_fresh,
            price_series_used=frame.price_series_used,
            spot_event_count_window=frame.spot_event_count_window,
            perp_event_count_window=frame.perp_event_count_window,
            last_spot_event_ts=frame.last_spot_event_ts,
            last_perp_event_ts=frame.last_perp_event_ts,
            source_count_active=source_count_active,
            max_source_share=max_source_share,
            top_source_id=top_source_id,
            top_source_effort=top_source_effort,
        )

    def _dominance_ratio(self, dominance: float, total_effort: float) -> float:
        return dominance / (total_effort + EPSILON)

    def _effectiveness_ratio(
        self,
        disp_rate_dir: float,
        disp_scale: float,
        effort_rate: float,
        effort_scale: float,
    ) -> float:
        numerator = disp_rate_dir * (effort_scale + EPSILON)
        denominator = (effort_rate * (disp_scale + EPSILON)) + EPSILON
        return numerator / denominator

    def _apply_tanh(self, eff_raw: float) -> float:
        k = self._defaults.effectiveness_scaling.tanh_k
        return _tanh(k * eff_raw)

    def _apply_effort_floor_gate(self, y_raw: float, total_effort: float) -> float:
        gate = self._effort_gate(total_effort)
        return gate * y_raw

    def _effort_gate(self, effort_rate: float) -> float:
        if not self._recent_effort:
            return 1.0
        effort_floor = self._median_recent(self._recent_effort)
        floor = effort_floor * self._defaults.effort_floor.multiplier_alpha
        return _clamp(effort_rate / (floor + EPSILON), 0.0, 1.0)

    def _update_scales(self, now_ms: int, disp_rate_abs: float, effort_rate: float) -> None:
        window_ms = int(self._defaults.input_normalization.scale_window_seconds * 1000)
        cutoff = now_ms - window_ms
        self._recent_disp_rate.append((now_ms, disp_rate_abs))
        self._recent_effort.append((now_ms, effort_rate))
        while self._recent_disp_rate and self._recent_disp_rate[0][0] < cutoff:
            self._recent_disp_rate.popleft()
        while self._recent_effort and self._recent_effort[0][0] < cutoff:
            self._recent_effort.popleft()

    def _median_recent(self, values: Deque[tuple[int, float]]) -> float:
        if not values:
            return 0.0
        samples = [value for _, value in values if value > 0.0]
        if not samples:
            return 0.0
        return median(samples)

    def _disp_scale(self) -> float:
        samples = [value for _, value in self._recent_disp_rate if value > 0.0]
        if not samples:
            return self._disp_scale_cache or 0.0
        min_samples = self._defaults.disp_scale.min_samples
        if len(samples) < min_samples and self._disp_scale_cache is not None:
            return self._disp_scale_cache
        if len(samples) < min_samples:
            scale = median(samples)
            if scale > 0.0:
                self._disp_scale_cache = scale
            return scale
        samples.sort()
        scale = _percentile(samples, self._defaults.disp_scale.percentile)
        floor = _percentile(samples, self._defaults.disp_scale.floor_percentile)
        if floor > 0.0:
            scale = max(scale, floor)
        if scale > 0.0:
            self._disp_scale_cache = scale
        return scale

    def _effort_scale(self) -> float:
        samples = [value for _, value in self._recent_effort if value > 0.0]
        if not samples:
            return 0.0
        min_samples = self._defaults.effort_scale.min_samples
        if len(samples) < min_samples:
            return median(samples)
        samples.sort()
        return _percentile(samples, self._defaults.effort_scale.percentile)

    def _force_magnitude(self, dominance: float, total_effort: float) -> float:
        dom = abs(dominance) / (total_effort + EPSILON)
        return dom**0.5

    def _update_halo(self, halo_raw: float) -> float:
        if self._halo is None:
            self._halo = halo_raw
            return halo_raw
        self._halo = update_halo(self._halo, halo_raw, self._defaults)
        return self._halo

    def _update_persistence(
        self,
        now_ms: int,
        acceptance: float,
        fallback_dt_s: float,
    ) -> tuple[float, float, int]:
        if not self._defaults.persistence.enabled:
            self._persist_state = 0.0
            self._persist_last_ts_ms = now_ms
            return 0.0, 0.0, 0

        if self._persist_last_ts_ms is None:
            dt_s = max(fallback_dt_s, EPSILON)
        else:
            dt_s = max((now_ms - self._persist_last_ts_ms) / 1000.0, EPSILON)
        self._persist_last_ts_ms = now_ms

        tau_build = max(self._defaults.persistence.tau_build_s, EPSILON)
        tau_decay = max(self._defaults.persistence.tau_decay_s, EPSILON)
        build = 1.0 - _exp(-dt_s / tau_build)
        decay = 1.0 - _exp(-dt_s / tau_decay)

        prev = self._persist_state
        current = _clamp(prev * (1.0 - decay) + build * acceptance, -1.0, 1.0)
        self._persist_state = current
        slope = (current - prev) / dt_s
        return current, slope, _sign(current)

    def _derive_lean(
        self,
        prev_x: float | None,
        prev_y: float | None,
        x: float,
        y: float,
    ) -> tuple[int, int] | None:
        if prev_x is None or prev_y is None:
            self._lean_frames_remaining = 0
            return None

        if self._lean_frames_remaining > 0:
            self._lean_frames_remaining -= 1
            return self._lean_dir

        dx = x - prev_x
        dy = y - prev_y
        if dx == 0.0 and dy == 0.0:
            self._lean_dir = None
            return None

        self._lean_dir = (_sign(dx), _sign(dy))
        self._lean_frames_remaining = 2
        return self._lean_dir


def _tanh(value: float) -> float:
    if value > 20:
        return 1.0
    if value < -20:
        return -1.0
    return (2.0 / (1.0 + _exp(-2.0 * value))) - 1.0


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _exp(value: float) -> float:
    from math import exp

    return float(exp(value))


def _log_return(price_start: float, price_end: float) -> float:
    from math import log

    return float(log(price_end / price_start))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return values[0]
    if pct >= 1:
        return values[-1]
    idx = int(round(pct * (len(values) - 1)))
    return values[idx]


def _aggregate_efforts(
    efforts: Iterable[EffortContribution],
) -> tuple[float, float, Mapping[str, float]]:
    e_spot = 0.0
    e_perp = 0.0
    per_source: dict[str, float] = {}
    for effort in efforts:
        if effort.side_type == "spot":
            e_spot += effort.effort_value
        elif effort.side_type == "perp":
            e_perp += effort.effort_value
        else:
            raise ValueError(f"Unknown side_type: {effort.side_type}")
        per_source[effort.source_id] = per_source.get(effort.source_id, 0.0) + effort.effort_value
    return e_spot, e_perp, per_source


def _smooth(previous: float | None, current: float, alpha: float) -> float:
    if previous is None:
        return current
    return previous + alpha * (current - previous)


def _source_stats(
    sources: Mapping[str, float],
) -> tuple[int, float, str | None, float]:
    total = sum(sources.values())
    if total <= 0.0:
        return 0, 0.0, None, 0.0
    top_source_id = None
    top_effort = 0.0
    active = 0
    for source_id, effort in sources.items():
        if effort > 0:
            active += 1
        if effort > top_effort:
            top_effort = effort
            top_source_id = source_id
    max_share = top_effort / (total + EPSILON)
    return active, max_share, top_source_id, top_effort


def _bin_with_hysteresis(
    value: float,
    previous_bin: int | None,
    thresholds: tuple[float, float],
    band: float,
) -> int:
    low, high = thresholds
    if previous_bin is None:
        if value < low:
            return 0
        if value < high:
            return 1
        return 2

    if previous_bin == 0:
        return 1 if value >= low + band else 0
    if previous_bin == 1:
        if value <= low - band:
            return 0
        if value >= high + band:
            return 2
        return 1
    if previous_bin == 2:
        return 1 if value <= high - band else 2
    return 1


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
