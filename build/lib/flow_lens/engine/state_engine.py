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
    persist_input: str
    persist_input_value: float
    persist_a_eff: float
    persist_a_dir: float
    persist_raw: float
    persist_dir_raw: float
    persist_slope: float
    persist_sign: int
    persist_dir_sign: int
    persist_dt_s: float
    persist_gain_per_second: float
    persist_input_deadband: float
    persist_step_coeff: float
    persist_alpha_eff: float
    persist_alpha_dir: float
    persist_tau_eff_s: float
    persist_tau_dir_s: float
    persist_update_mode: str
    persist_activity_flag: bool
    persist_pivot_confirm_elapsed_s: float
    persist_pivot_cooldown_remaining_s: float
    persist_last_confirmed_dir_sign: int
    persist_pivot_target_dir_sign: int
    persist_neutral_dir_abs_flash: float
    persist_neutral_dir_abs_persist: float
    size_effort_norm: float
    size_scale: float
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
    active_price_source_id: str | None
    selector_policy: str
    price_series_side: str
    price_series_used: str
    spot_event_count_window: int
    perp_event_count_window: int
    last_spot_event_ts: int | None
    last_perp_event_ts: int | None
    source_count_active: int
    max_source_share: float
    top_source_id: str | None
    top_source_effort: float
    control_baseline_enabled: bool = False
    control_baseline_initialized: bool = False
    control_baseline_x: float = 0.0
    control_baseline_target_x: float = 0.0
    control_baseline_mode: str = "peg"
    control_baseline_breakout_age_s: float = 0.0
    control_baseline_delta: float = 0.0
    control_baseline_visible: bool = False
    control_baseline_midnight_tick_visible: bool = False
    control_baseline_midnight_tick_locked: bool = False
    control_baseline_midnight_tick_x: float | None = None
    control_baseline_midnight_tick_samples: int = 0


@dataclass(frozen=True)
class _PersistenceUpdate:
    a_eff: float
    a_dir: float
    s_eff: float
    s_dir: float
    slope_eff: float
    dt_s: float
    input_deadband: float
    alpha_eff: float
    alpha_dir: float
    tau_eff_s: float
    tau_dir_s: float
    mode: str
    activity_flag: bool
    pivot_confirm_elapsed_s: float
    pivot_cooldown_remaining_s: float
    last_confirmed_dir_sign: int
    pivot_target_dir_sign: int
    neutral_dir_abs_flash: float
    neutral_dir_abs_persist: float


class StateEngine:
    def __init__(self, defaults: Defaults | None = None) -> None:
        self._defaults = defaults if defaults is not None else Defaults()
        self._recent_effort: Deque[tuple[int, float]] = deque()
        self._recent_disp_rate: Deque[tuple[int, float]] = deque()
        self.reset_context()

    def reset_context(self) -> None:
        self._recent_effort.clear()
        self._recent_disp_rate.clear()
        self._disp_scale_cache: float | None = None
        self._x_smoothed: float | None = None
        self._y_smoothed: float | None = None
        self._persist_eff_state: float = 0.0
        self._persist_dir_state: float = 0.0
        self._persist_last_ts_ms: int | None = None
        self._persist_quiet_elapsed_s: float = 0.0
        self._persist_pivot_active: bool = False
        self._persist_pivot_elapsed_s: float = 0.0
        self._persist_pivot_rebuild_elapsed_s: float = 0.0
        self._persist_pivot_confirm_elapsed_s: float = 0.0
        self._persist_pivot_cooldown_remaining_s: float = 0.0
        self._persist_last_confirmed_dir_sign: int = 0
        self._persist_pivot_target_dir_sign: int = 0
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
        prev_x = self._x_smoothed
        prev_y = self._y_smoothed
        x = _smooth(prev_x, x_raw, self._defaults.smoothing.dominance_alpha)
        y = _smooth(prev_y, y_gated, self._defaults.smoothing.effectiveness_alpha)
        self._x_smoothed = x
        self._y_smoothed = y

        persist_enabled = self._defaults.persistence.enabled
        persist_input, persist_input_value = self._resolve_persistence_input(
            y_raw=y_raw,
            y_gated=y_gated,
            y=y,
        )
        persistence = self._update_persistence(
            frame.timestamp,
            persist_input_value,
            e_dir,
            total_effort,
            effort_norm,
            window_seconds,
        )

        size_scale = self._size_scale()
        size_effort_norm = effort_rate / (size_scale + EPSILON)
        size_raw = _clamp(size_effort_norm / (1.0 + size_effort_norm), 0.0, 1.0)
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
            persist_input=persist_input,
            persist_input_value=persist_input_value,
            persist_a_eff=persistence.a_eff,
            persist_a_dir=persistence.a_dir,
            persist_raw=persistence.s_eff,
            persist_dir_raw=persistence.s_dir,
            persist_slope=persistence.slope_eff,
            persist_sign=_sign(persistence.s_eff),
            persist_dir_sign=_sign(persistence.s_dir),
            persist_dt_s=persistence.dt_s,
            persist_gain_per_second=persistence.alpha_eff / (persistence.dt_s + EPSILON),
            persist_input_deadband=persistence.input_deadband,
            persist_step_coeff=persistence.alpha_eff,
            persist_alpha_eff=persistence.alpha_eff,
            persist_alpha_dir=persistence.alpha_dir,
            persist_tau_eff_s=persistence.tau_eff_s,
            persist_tau_dir_s=persistence.tau_dir_s,
            persist_update_mode=persistence.mode,
            persist_activity_flag=persistence.activity_flag,
            persist_pivot_confirm_elapsed_s=persistence.pivot_confirm_elapsed_s,
            persist_pivot_cooldown_remaining_s=persistence.pivot_cooldown_remaining_s,
            persist_last_confirmed_dir_sign=persistence.last_confirmed_dir_sign,
            persist_pivot_target_dir_sign=persistence.pivot_target_dir_sign,
            persist_neutral_dir_abs_flash=persistence.neutral_dir_abs_flash,
            persist_neutral_dir_abs_persist=persistence.neutral_dir_abs_persist,
            size_effort_norm=size_effort_norm,
            size_scale=size_scale,
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
            active_price_source_id=frame.active_price_source_id,
            selector_policy=frame.selector_policy,
            price_series_side=frame.price_series_side,
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

    def _size_scale(self) -> float:
        samples = [value for _, value in self._recent_effort if value > 0.0]
        if not samples:
            return 0.0
        min_samples = self._defaults.effort_scale.min_samples
        if len(samples) < min_samples:
            return median(samples)
        samples.sort()
        return _percentile(samples, self._defaults.size_scale.percentile)

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
        e_dir: float,
        total_effort: float,
        effort_norm: float,
        fallback_dt_s: float,
    ) -> _PersistenceUpdate:
        settings = self._defaults.persistence
        neutral_dir_abs_flash = _clamp(settings.neutral_dir_abs_flash, 0.0, 1.0)
        neutral_dir_abs_persist = _clamp(settings.neutral_dir_abs_persist, 0.0, 1.0)
        if not settings.enabled:
            self._persist_eff_state = 0.0
            self._persist_dir_state = 0.0
            self._persist_last_ts_ms = now_ms
            self._persist_quiet_elapsed_s = 0.0
            self._persist_pivot_active = False
            self._persist_pivot_elapsed_s = 0.0
            self._persist_pivot_rebuild_elapsed_s = 0.0
            self._persist_pivot_confirm_elapsed_s = 0.0
            self._persist_pivot_cooldown_remaining_s = 0.0
            self._persist_last_confirmed_dir_sign = 0
            self._persist_pivot_target_dir_sign = 0
            return _PersistenceUpdate(
                a_eff=0.0,
                a_dir=0.0,
                s_eff=0.0,
                s_dir=0.0,
                slope_eff=0.0,
                dt_s=0.0,
                input_deadband=0.0,
                alpha_eff=0.0,
                alpha_dir=0.0,
                tau_eff_s=0.0,
                tau_dir_s=0.0,
                mode="disabled",
                activity_flag=False,
                pivot_confirm_elapsed_s=0.0,
                pivot_cooldown_remaining_s=0.0,
                last_confirmed_dir_sign=0,
                pivot_target_dir_sign=0,
                neutral_dir_abs_flash=neutral_dir_abs_flash,
                neutral_dir_abs_persist=neutral_dir_abs_persist,
            )

        if self._persist_last_ts_ms is None:
            dt_s = max(fallback_dt_s, EPSILON)
        else:
            dt_s = max((now_ms - self._persist_last_ts_ms) / 1000.0, EPSILON)
        self._persist_last_ts_ms = now_ms
        self._persist_pivot_cooldown_remaining_s = max(
            0.0, self._persist_pivot_cooldown_remaining_s - dt_s
        )

        input_deadband = max(settings.input_deadband, 0.0)
        a_eff = acceptance if abs(acceptance) > input_deadband else 0.0
        activity_flag = abs(a_eff) > EPSILON
        e_dir_share = e_dir / (total_effort + EPSILON) if total_effort > 0 else 0.0
        dir_sign = _sign_deadband(e_dir_share, neutral_dir_abs_persist)
        a_dir = dir_sign * max(a_eff, 0.0)

        quiet = (
            abs(a_eff) < settings.dormant_quiet_abs
            and effort_norm < settings.dormant_effort_norm_threshold
            and not self._persist_pivot_active
        )
        if quiet:
            self._persist_quiet_elapsed_s += dt_s
        else:
            self._persist_quiet_elapsed_s = 0.0

        mode = "active"
        if self._persist_pivot_active:
            mode = "pivot"
        elif self._persist_quiet_elapsed_s >= settings.dormant_quiet_s:
            mode = "dormant"

        if mode == "dormant" and (
            abs(a_eff) >= settings.dormant_active_abs
            or effort_norm >= settings.dormant_effort_norm_threshold
        ):
            mode = "active"
            self._persist_quiet_elapsed_s = 0.0

        if mode == "active":
            mode = self._maybe_enter_pivot(a_dir=a_dir, dt_s=dt_s)

        prev_s_eff = self._persist_eff_state
        tau_eff = settings.tau_eff_active
        tau_dir = settings.tau_dir_active
        alpha_eff = _alpha(dt_s, tau_eff)
        alpha_dir = _alpha(dt_s, tau_dir)
        target_eff = a_eff
        target_dir = a_dir

        if mode == "pivot":
            tau_eff = settings.pivot_neutralize_tau
            tau_dir = settings.tau_dir_pivot
            alpha_eff = _alpha(dt_s, tau_eff)
            alpha_dir = _alpha(dt_s, tau_dir)
            target_eff = 0.0
            target_dir = a_dir
            self._persist_pivot_elapsed_s += dt_s
        elif mode == "dormant":
            tau_eff = settings.tau_dormant
            tau_dir = settings.tau_dormant
            alpha_eff = _alpha(dt_s, tau_eff)
            alpha_dir = _alpha(dt_s, tau_dir)
            target_eff = 0.0
            target_dir = 0.0

        next_eff = _clamp(
            self._persist_eff_state + alpha_eff * (target_eff - self._persist_eff_state),
            -1.0,
            1.0,
        )
        if mode == "pivot":
            max_delta = max(settings.max_delta_s_eff_per_second, 0.0) * dt_s
            if max_delta > 0.0:
                delta = _clamp(next_eff - self._persist_eff_state, -max_delta, max_delta)
                next_eff = _clamp(self._persist_eff_state + delta, -1.0, 1.0)
        self._persist_eff_state = next_eff
        self._persist_dir_state = _clamp(
            self._persist_dir_state + alpha_dir * (target_dir - self._persist_dir_state),
            -1.0,
            1.0,
        )

        if mode == "pivot":
            if (
                abs(self._persist_eff_state) <= settings.pivot_neutral_zone_abs
                and abs(a_dir) >= settings.pivot_active_abs
                and _sign(a_dir) == self._persist_pivot_target_dir_sign
            ):
                self._persist_pivot_rebuild_elapsed_s += dt_s
            else:
                self._persist_pivot_rebuild_elapsed_s = 0.0

            if (
                self._persist_pivot_rebuild_elapsed_s >= settings.rebuild_confirm_s
                or self._persist_pivot_elapsed_s >= settings.pivot_max_s
            ):
                self._persist_pivot_active = False
                self._persist_pivot_elapsed_s = 0.0
                self._persist_pivot_rebuild_elapsed_s = 0.0
                self._persist_pivot_confirm_elapsed_s = 0.0
                self._persist_pivot_cooldown_remaining_s = settings.pivot_cooldown_s
                if abs(a_dir) >= settings.pivot_active_abs and _sign(a_dir) != 0:
                    self._persist_last_confirmed_dir_sign = _sign(a_dir)
                elif self._persist_pivot_target_dir_sign != 0:
                    self._persist_last_confirmed_dir_sign = self._persist_pivot_target_dir_sign
                self._persist_pivot_target_dir_sign = 0
                mode = "active"

        slope = (self._persist_eff_state - prev_s_eff) / dt_s
        return _PersistenceUpdate(
            a_eff=a_eff,
            a_dir=a_dir,
            s_eff=self._persist_eff_state,
            s_dir=self._persist_dir_state,
            slope_eff=slope,
            dt_s=dt_s,
            input_deadband=input_deadband,
            alpha_eff=alpha_eff,
            alpha_dir=alpha_dir,
            tau_eff_s=tau_eff,
            tau_dir_s=tau_dir,
            mode=mode,
            activity_flag=activity_flag,
            pivot_confirm_elapsed_s=self._persist_pivot_confirm_elapsed_s,
            pivot_cooldown_remaining_s=self._persist_pivot_cooldown_remaining_s,
            last_confirmed_dir_sign=self._persist_last_confirmed_dir_sign,
            pivot_target_dir_sign=self._persist_pivot_target_dir_sign,
            neutral_dir_abs_flash=neutral_dir_abs_flash,
            neutral_dir_abs_persist=neutral_dir_abs_persist,
        )

    def _maybe_enter_pivot(self, *, a_dir: float, dt_s: float) -> str:
        settings = self._defaults.persistence
        dir_sign = _sign(a_dir)
        if abs(a_dir) < settings.pivot_active_abs or dir_sign == 0:
            self._persist_pivot_confirm_elapsed_s = 0.0
            return "active"

        if self._persist_last_confirmed_dir_sign == 0:
            self._persist_pivot_confirm_elapsed_s += dt_s
            if self._persist_pivot_confirm_elapsed_s >= settings.pivot_confirm_s:
                self._persist_last_confirmed_dir_sign = dir_sign
                self._persist_pivot_confirm_elapsed_s = 0.0
            return "active"

        if dir_sign == self._persist_last_confirmed_dir_sign:
            self._persist_pivot_confirm_elapsed_s = 0.0
            return "active"

        if self._persist_pivot_cooldown_remaining_s > 0.0:
            self._persist_pivot_confirm_elapsed_s = 0.0
            return "active"

        self._persist_pivot_confirm_elapsed_s += dt_s
        if self._persist_pivot_confirm_elapsed_s < settings.pivot_confirm_s:
            return "active"

        self._persist_pivot_active = True
        self._persist_pivot_elapsed_s = 0.0
        self._persist_pivot_rebuild_elapsed_s = 0.0
        self._persist_pivot_confirm_elapsed_s = 0.0
        self._persist_pivot_target_dir_sign = dir_sign
        return "pivot"

    def _resolve_persistence_input(
        self,
        *,
        y_raw: float,
        y_gated: float,
        y: float,
    ) -> tuple[str, float]:
        source = self._defaults.persistence.input_source
        if source == "y_raw":
            return "Y_raw", y_raw
        if source == "y":
            return "Y", y
        return "Y_gated", y_gated

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


def _alpha(dt_s: float, tau_s: float) -> float:
    tau = max(tau_s, EPSILON)
    return 1.0 - _exp(-dt_s / tau)


def _sign_deadband(value: float, deadband: float) -> int:
    threshold = max(deadband, 0.0)
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


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
