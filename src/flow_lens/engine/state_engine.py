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
    x_raw: float
    y_raw: float
    y_gated: float
    x: float
    y: float
    size_raw: float
    size_bin: int
    halo_raw: float
    halo: float
    halo_bin: int
    lean: tuple[int, int] | None
    dominance: float
    total_effort: float


class StateEngine:
    def __init__(self, defaults: Defaults = Defaults()) -> None:
        self._defaults = defaults
        self._recent_effort: Deque[float] = deque(maxlen=defaults.effort_floor.rolling_window_ticks)
        self._x_smoothed: float | None = None
        self._y_smoothed: float | None = None
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
        total_effort = e_spot + e_perp
        dominance = e_spot - e_perp

        self._recent_effort.append(total_effort)
        effort_norm = self._normalized_effort(total_effort)
        gate = self._effort_gate(total_effort)
        x_raw = _clamp(self._dominance_ratio(dominance, total_effort), -1.0, 1.0) * gate
        disp = self._directional_displacement(
            dominance, frame.price_start, frame.price
        )
        y_raw = self._effectiveness_raw(disp, effort_norm)
        y_gated = gate * y_raw

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

        return StateSnapshot(
            x_raw=x_raw,
            y_raw=y_raw,
            y_gated=y_gated,
            x=x,
            y=y,
            size_raw=size_raw,
            size_bin=size_bin,
            halo_raw=halo_raw,
            halo=halo,
            halo_bin=halo_bin,
            lean=lean,
            dominance=dominance,
            total_effort=total_effort,
        )

    def _dominance_ratio(self, dominance: float, total_effort: float) -> float:
        return dominance / (total_effort + EPSILON)

    def _directional_displacement(
        self, dominance: float, price_start: float, price_end: float
    ) -> float:
        if price_start <= 0.0 or price_end <= 0.0:
            return 0.0
        delta = _log_return(price_start, price_end)
        if dominance > 0:
            return delta
        if dominance < 0:
            return -delta
        return 0.0

    def _effectiveness_raw(self, displacement: float, effort_norm: float) -> float:
        eff_raw = displacement / (effort_norm + EPSILON)
        k = self._defaults.effectiveness_scaling.tanh_k
        return _tanh(k * eff_raw)

    def _apply_effort_floor_gate(self, y_raw: float, total_effort: float) -> float:
        gate = self._effort_gate(total_effort)
        return gate * y_raw

    def _effort_floor(self) -> float:
        floor = median(self._recent_effort)
        return floor * self._defaults.effort_floor.multiplier_alpha

    def _normalized_effort(self, total_effort: float) -> float:
        if not self._recent_effort:
            return total_effort
        baseline = median(self._recent_effort)
        if baseline <= 0.0:
            return total_effort
        return total_effort / baseline

    def _effort_gate(self, total_effort: float) -> float:
        if not self._recent_effort:
            return 1.0
        effort_floor = self._effort_floor()
        return _clamp(total_effort / (effort_floor + EPSILON), 0.0, 1.0)

    def _force_magnitude(self, dominance: float, total_effort: float) -> float:
        dom = abs(dominance) / (total_effort + EPSILON)
        return dom**0.5

    def _update_halo(self, halo_raw: float) -> float:
        if self._halo is None:
            self._halo = halo_raw
            return halo_raw
        self._halo = update_halo(self._halo, halo_raw, self._defaults)
        return self._halo

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
        self._lean_frames_remaining = 1
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
