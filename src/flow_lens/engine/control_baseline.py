from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import median

from flow_lens.engine.constants import ControlBaseline


@dataclass(frozen=True)
class ControlBaselineSnapshot:
    enabled: bool
    initialized: bool
    baseline_x: float
    target_x: float
    mode: str
    breakout_age_s: float
    delta: float
    baseline_visible: bool
    midnight_tick_visible: bool
    midnight_tick_locked: bool
    midnight_tick_x: float | None
    midnight_tick_samples: int


class DynamicControlBaseline:
    def __init__(self, config: ControlBaseline) -> None:
        self._config = config
        self._window_ms = int(round(config.target_window_s * 1000.0))
        self._target_update_ms = max(1, int(round(config.target_update_s * 1000.0)))
        self._max_window_samples = (
            config.max_window_samples
            if config.max_window_samples is not None
            else int(math.ceil(config.target_window_s / config.target_update_s)) + 2
        )
        self.reset_context()

    def reset_context(self) -> None:
        self._initialized = False
        self._baseline_x = 0.0
        self._target_x = 0.0
        self._mode = "peg"
        self._breakout_age_s = 0.0
        self._last_frame_ts_ms: int | None = None
        self._last_sample_ts_ms: int | None = None
        self._first_valid_ts_ms: int | None = None
        self._samples: deque[tuple[int, float]] = deque()
        self._day_id: int | None = None
        self._day_samples: deque[tuple[int, float]] = deque()
        self._midnight_tick_locked = False
        self._midnight_tick_x: float | None = None
        self._midnight_tick_locked_day_id: int | None = None

    def snapshot(self) -> ControlBaselineSnapshot:
        delta = self._target_x - self._baseline_x
        baseline_visible = self._baseline_visible(self._last_frame_ts_ms)
        midnight_tick_visible = self._midnight_tick_visible()
        return ControlBaselineSnapshot(
            enabled=self._config.enabled,
            initialized=self._initialized,
            baseline_x=self._baseline_x,
            target_x=self._target_x,
            mode=self._mode,
            breakout_age_s=self._breakout_age_s,
            delta=delta,
            baseline_visible=baseline_visible,
            midnight_tick_visible=midnight_tick_visible,
            midnight_tick_locked=self._midnight_tick_locked,
            midnight_tick_x=self._midnight_tick_value(),
            midnight_tick_samples=len(self._day_samples),
        )

    def update(self, x: float, frame_ts_ms: int, *, state_valid: bool) -> ControlBaselineSnapshot:
        if not self._config.enabled:
            self._last_frame_ts_ms = frame_ts_ms
            return self.snapshot()

        dt_s = self._dt_seconds(frame_ts_ms)
        if not state_valid:
            return self.snapshot()

        x_clamped = _clamp(x, -1.0, 1.0)
        if not self._initialized:
            self._initialized = True
            self._baseline_x = x_clamped
            self._target_x = x_clamped
            self._samples.clear()
            self._first_valid_ts_ms = frame_ts_ms
            self._append_sample(frame_ts_ms, x_clamped)
            self._rollover_day_if_needed(frame_ts_ms)
            return self.snapshot()

        self._rollover_day_if_needed(frame_ts_ms)
        self._append_sample_if_due(frame_ts_ms, x_clamped)
        if self._samples:
            self._target_x = float(median(value for _, value in self._samples))

        delta = self._target_x - self._baseline_x
        abs_delta = abs(delta)
        breakout = abs_delta > self._config.breakout_band
        self._breakout_age_s = (self._breakout_age_s + dt_s) if breakout else 0.0

        if self._mode == "peg" and self._breakout_age_s >= self._config.confirm_s:
            self._mode = "reanchor"

        exit_band = self._config.breakout_band * self._config.exit_band_frac
        if self._mode == "reanchor" and abs_delta <= exit_band:
            self._mode = "peg"

        if self._mode == "peg":
            if abs_delta >= self._config.peg_deadband:
                self._baseline_x = _smooth_half_life(
                    self._baseline_x, self._target_x, dt_s, self._config.peg_half_life_s
                )
        else:
            self._baseline_x = _smooth_half_life(
                self._baseline_x, self._target_x, dt_s, self._config.reanchor_half_life_s
            )

        self._baseline_x = _clamp(self._baseline_x, -1.0, 1.0)
        return self.snapshot()

    def _dt_seconds(self, frame_ts_ms: int) -> float:
        if self._last_frame_ts_ms is None:
            self._last_frame_ts_ms = frame_ts_ms
            return 0.0
        dt_ms = max(0, frame_ts_ms - self._last_frame_ts_ms)
        self._last_frame_ts_ms = frame_ts_ms
        return dt_ms / 1000.0

    def _append_sample_if_due(self, frame_ts_ms: int, x: float) -> None:
        if self._last_sample_ts_ms is None:
            self._append_sample(frame_ts_ms, x)
            return
        if frame_ts_ms - self._last_sample_ts_ms < self._target_update_ms:
            return
        self._append_sample(frame_ts_ms, x)

    def _append_sample(self, ts_ms: int, x: float) -> None:
        self._samples.append((ts_ms, x))
        self._day_samples.append((ts_ms, x))
        self._last_sample_ts_ms = ts_ms
        self._evict_old(ts_ms)

    def _evict_old(self, now_ms: int) -> None:
        cutoff = now_ms - self._window_ms
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while len(self._samples) > self._max_window_samples:
            self._samples.popleft()
        day_cutoff = now_ms - 86_400_000
        while self._day_samples and self._day_samples[0][0] < day_cutoff:
            self._day_samples.popleft()

    def _rollover_day_if_needed(self, frame_ts_ms: int) -> None:
        day_id = frame_ts_ms // 86_400_000
        if self._day_id is None:
            self._day_id = day_id
            return
        if day_id == self._day_id:
            return
        if self._config.midnight_tick_enabled and self._day_samples:
            self._midnight_tick_x = float(median(value for _, value in self._day_samples))
            self._midnight_tick_locked = True
            self._midnight_tick_locked_day_id = day_id
        self._day_samples.clear()
        self._day_id = day_id

    def _baseline_visible(self, frame_ts_ms: int | None) -> bool:
        if not self._config.enabled or not self._initialized:
            return False
        if self._first_valid_ts_ms is None or frame_ts_ms is None:
            return False
        warmup_ms = int(round(self._config.line_hide_warmup_s * 1000.0))
        return frame_ts_ms - self._first_valid_ts_ms >= warmup_ms

    def _midnight_tick_value(self) -> float | None:
        if not self._config.midnight_tick_enabled:
            return None
        if self._midnight_tick_locked and self._midnight_tick_x is not None:
            return self._midnight_tick_x
        if not self._day_samples:
            return None
        return float(median(value for _, value in self._day_samples))

    def _midnight_tick_visible(self) -> bool:
        if not self._config.midnight_tick_enabled:
            return False
        tick_value = self._midnight_tick_value()
        if tick_value is None:
            return False
        if self._midnight_tick_locked:
            return True
        if len(self._day_samples) >= self._config.midnight_tick_min_samples:
            return True
        if len(self._day_samples) < 2:
            return False
        elapsed_s = (self._day_samples[-1][0] - self._day_samples[0][0]) / 1000.0
        return elapsed_s >= self._config.midnight_tick_min_elapsed_s


def _smooth_half_life(current: float, target: float, dt_s: float, half_life_s: float) -> float:
    if dt_s <= 0.0:
        return current
    tau_s = half_life_s / math.log(2.0)
    alpha = 1.0 - math.exp(-dt_s / tau_s)
    return current + alpha * (target - current)


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
