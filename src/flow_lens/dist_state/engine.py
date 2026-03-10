from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import cast

from flow_lens.dist_state.models import (
    DistAvailabilityMode,
    DistKlineCloseEvent,
    DistOiSamplerSnapshot,
    DistPanelSnapshot,
    DistRowBins,
    DistRowMetrics,
    DistRowSnapshot,
    DistRowToken,
    DistTimeframe,
    DistTimeMissingPolicy,
    NarrativeParamValue,
)

LOGGER = logging.getLogger(__name__)
EPSILON = 1e-12
BIN_LEVELS = 7
_NARRATIVE_CLASS_PRECEDENCE = ("EXP", "EXH", "CONT", "REVERT", "COMP", "NEUT")
_NARRATIVE_VECTOR_KEYS = ("EXP", "EXH", "CONT", "REVERT", "COMP", "NEUT")
_NARRATIVE_TF_WEIGHT = {"3m": 1.0, "15m": 1.5, "1h": 2.0, "4h": 2.5}
_NARRATIVE_TF_ORDER = {"3m": 0, "15m": 1, "1h": 2, "4h": 3}


@dataclass(frozen=True)
class DistStateConfig:
    enabled: bool
    symbol: str
    source_id: str
    timeframes: tuple[DistTimeframe, ...]
    warmup_kline_bars: int
    warmup_oi_hist_points: int
    ready_core_min_bars: int
    ready_p_min_deltas: int
    p_availability_mode: DistAvailabilityMode
    oi_tolerance_ms: int
    oi_time_missing_policy: DistTimeMissingPolicy
    oi_seed_points: int
    oi_seed_min_points: int
    v_scale_window_bars: int
    v_scale_percentile: float
    v_scale_min_samples: int
    hl_vol_bars: float
    hl_stretch_bars: float
    hl_oi_bars: float
    hl_atr_short_bars: float
    hl_atr_long_bars: float
    hl_a_bars: float
    k_s: float
    k_p: float
    k_t: float
    tokens_enabled: bool
    tokens_fail_fast_unknown: bool
    s_dir_deadband: float
    s_ext_enter: float
    s_ext_exit: float
    s_revert_min_stretch: float
    t_exp_enter: float
    t_exp_exit: float
    t_comp_enter: float
    t_comp_exit: float
    a_cont_enter: float
    a_cont_exit: float
    a_revert_enter: float
    a_revert_exit: float
    v_low_threshold: float
    t_rise_threshold: float
    s_neut_max: float
    a_neut_max: float
    t_neut_max: float
    v_neut_min: float
    v_neut_max: float
    t_exp_plus: float
    t_exp_plus_plus: float
    t_comp_plus: float
    t_comp_plus_plus: float
    a_cont_plus: float
    a_cont_plus_plus: float
    a_revert_plus: float
    a_revert_plus_plus: float
    s_exh_plus: float
    s_exh_plus_plus: float
    p_confirm_threshold: float
    token_min_hold_bars_3m: int
    token_min_hold_bars_15m: int
    token_min_hold_bars_1h: int
    token_min_hold_bars_4h: int
    narrative_enabled: bool
    narrative_driver_tf: DistTimeframe
    narrative_linger_reminder_closes: int
    narrative_max_chars: int
    narrative_secondary_min_ratio: float
    narrative_dir_ratio_min: float


@dataclass
class _DistRowState:
    tf: DistTimeframe
    bars_seen: int = 0
    oi_deltas_seen: int = 0
    oi_var_initialized: bool = False
    last_close_ms: int | None = None
    last_processed_close_ms: int | None = None
    processed_close_keys: deque[int] = field(default_factory=deque)
    processed_close_set: set[int] = field(default_factory=set)
    prev_close: float | None = None
    prev_return: float | None = None
    var_r: float = 0.0
    mu_x: float | None = None
    var_dx: float = 0.0
    p_same: float = 0.5
    atr_s: float | None = None
    atr_l: float | None = None
    var_oi: float = 0.0
    prev_oi: float | None = None
    v_scale_samples: deque[float] = field(default_factory=deque)
    token: DistRowToken | None = None
    token_strength: str | None = None
    token_bars_since_change: int = 0
    token_exp_latched: bool = False
    token_comp_latched: bool = False
    token_cont_latched: bool = False
    token_revert_latched: bool = False
    token_extended_latched: bool = False
    prev_metrics: DistRowMetrics | None = None
    metrics: DistRowMetrics = DistRowMetrics(None, None, None, None, None)
    bins: DistRowBins = DistRowBins(None, None, None, None, None)


@dataclass(frozen=True)
class _CloseSelection:
    snapshot: DistOiSamplerSnapshot | None
    reason: str | None
    source: str | None


@dataclass
class _NarrativeState:
    state_id: str | None = None
    template_id: str | None = None
    params: dict[str, NarrativeParamValue] = field(default_factory=dict)
    as_of_close_ms: int | None = None
    driver_tf: DistTimeframe | None = None
    started_close_ms: int | None = None
    age_closes: int | None = None
    reason_codes: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    text_template: str | None = None
    text_agent: str | None = None


class DistStateEngine:
    def __init__(self, config: DistStateConfig) -> None:
        self._config = config
        self._rows: dict[DistTimeframe, _DistRowState] = {
            tf: _DistRowState(tf=tf) for tf in config.timeframes
        }
        self._kline_base = "https://fapi.binance.com/fapi/v1/klines"
        self._oi_hist_url = "https://fapi.binance.com/futures/data/openInterestHist"
        self._lambda_vol = _ewma_lambda(config.hl_vol_bars)
        self._lambda_stretch = _ewma_lambda(config.hl_stretch_bars)
        self._lambda_oi = _ewma_lambda(config.hl_oi_bars)
        self._lambda_atr_s = _ewma_lambda(config.hl_atr_short_bars)
        self._lambda_atr_l = _ewma_lambda(config.hl_atr_long_bars)
        self._lambda_a = _ewma_lambda(config.hl_a_bars)
        self._close_selections: dict[tuple[str, int], _CloseSelection] = {}
        self._close_selection_keys: deque[tuple[str, int]] = deque()
        self._last_oi_ts_recv_ms: int | None = None
        self._last_oi_value: float | None = None
        self._oi_initialized = False
        self._oi_bootstrap_source: str | None = None
        self._oi_bootstrap_age_ms: int | None = None
        self._narrative = _NarrativeState(driver_tf=config.narrative_driver_tf)

    def warmup(self) -> None:
        for tf in self._config.timeframes:
            self._warmup_klines(tf)
        self._warmup_oi()
        for row in self._rows.values():
            row.last_processed_close_ms = row.last_close_ms
            row.processed_close_set.clear()
            row.processed_close_keys.clear()

    def on_kline_close(self, event: DistKlineCloseEvent) -> DistPanelSnapshot:
        snapshot, _ = self.on_kline_close_with_diagnostics(event)
        return snapshot

    def on_kline_close_with_diagnostics(
        self,
        event: DistKlineCloseEvent,
    ) -> tuple[DistPanelSnapshot, dict[str, object]]:
        row = self._rows[event.tf]
        if event.kline_close_ms in row.processed_close_set:
            return self.snapshot(), {
                "processed": False,
                "drop_reason": "duplicate_close",
                "tf": event.tf,
                "kline_close_ms": event.kline_close_ms,
            }
        if row.last_processed_close_ms is not None and event.kline_close_ms < row.last_processed_close_ms:
            return self.snapshot(), {
                "processed": False,
                "drop_reason": "out_of_order_close",
                "tf": event.tf,
                "kline_close_ms": event.kline_close_ms,
            }
        if not self._accept_close(row, event.kline_close_ms):
            return self.snapshot(), {
                "processed": False,
                "drop_reason": "rejected_close",
                "tf": event.tf,
                "kline_close_ms": event.kline_close_ms,
            }

        selection, selection_debug = self._select_snapshot_for_close(event)
        p_available, p_missing_reason, token_debug = self._apply_close(row, event, selection)
        narrative_debug: dict[str, object] = {}
        if self._config.narrative_enabled and event.tf == self._config.narrative_driver_tf:
            narrative_debug = self._evaluate_narrative(event.kline_close_ms)
        snapshot = self.snapshot()
        row_snapshot = snapshot.rows[event.tf]

        oi_offset_ms: int | None = None
        oi_staleness_ms: int | None = None
        if selection.snapshot is not None and selection.snapshot.venue_time_ms is not None:
            oi_offset_ms = selection.snapshot.venue_time_ms - event.kline_close_ms
            oi_staleness_ms = event.kline_close_ms - selection.snapshot.venue_time_ms

        return snapshot, {
            "processed": True,
            "drop_reason": None,
            "tf": event.tf,
            "kline_close_ms": event.kline_close_ms,
            "p_availability_mode": self._config.p_availability_mode,
            "selection_source": selection.source,
            "selection_reason": selection.reason,
            "oi_tolerance_ms": self._config.oi_tolerance_ms,
            "oi_sample_present": selection.snapshot is not None,
            "oi_sample_venue_time_ms": (
                selection.snapshot.venue_time_ms if selection.snapshot is not None else None
            ),
            "oi_sample_oi": selection.snapshot.oi if selection.snapshot is not None else None,
            "oi_sample_recv_ms": selection.snapshot.ts_recv_ms if selection.snapshot is not None else None,
            "oi_sample_seq": selection.snapshot.sample_seq if selection.snapshot is not None else None,
            "oi_offset_ms": oi_offset_ms,
            "oi_staleness_ms": oi_staleness_ms,
            "oi_bootstrap_source": self._oi_bootstrap_source,
            "oi_bootstrap_age_ms": self._oi_bootstrap_age_ms,
            "p_available": p_available,
            "p_missing_reason": p_missing_reason,
            "ready_core": row_snapshot.ready_core,
            "ready_p": row_snapshot.ready_p,
            "metrics_v": row_snapshot.metrics.v,
            "metrics_s": row_snapshot.metrics.s,
            "metrics_a": row_snapshot.metrics.a,
            "metrics_p": row_snapshot.metrics.p,
            "metrics_t": row_snapshot.metrics.t,
            "bin_v": row_snapshot.bins.v,
            "bin_s": row_snapshot.bins.s,
            "bin_a": row_snapshot.bins.a,
            "bin_p": row_snapshot.bins.p,
            "bin_t": row_snapshot.bins.t,
            "token": row_snapshot.token,
            "token_strength": row_snapshot.token_strength,
            "token_changed": token_debug["changed"],
            "token_prev": token_debug["prev_token"],
            "token_prev_strength": token_debug["prev_strength"],
            "token_dwell_blocked": token_debug["dwell_blocked"],
            "token_override_reason": token_debug["override_reason"],
            "token_predicate_hits": token_debug["predicate_hits"],
            "token_inputs": token_debug["inputs"],
            **narrative_debug,
            **selection_debug,
        }

    def snapshot(self) -> DistPanelSnapshot:
        out_rows: dict[DistTimeframe, DistRowSnapshot] = {}
        for tf, row in self._rows.items():
            ready_core = (
                row.bars_seen >= self._config.ready_core_min_bars
                and len(row.v_scale_samples) >= self._config.v_scale_min_samples
                and row.atr_l is not None
            )
            ready_p = row.oi_var_initialized and row.oi_deltas_seen >= self._config.ready_p_min_deltas
            out_rows[tf] = DistRowSnapshot(
                tf=tf,
                ready_core=ready_core,
                ready_p=ready_p,
                last_close_ms=row.last_close_ms,
                metrics=row.metrics,
                bins=row.bins,
                token=row.token,
                token_strength=row.token_strength,
            )
        return DistPanelSnapshot(
            symbol=self._config.symbol,
            source_id=self._config.source_id,
            rows=out_rows,
            last_oi_ts_recv_ms=self._last_oi_ts_recv_ms,
            last_oi_value=self._last_oi_value,
            tokens_enabled=self._config.tokens_enabled,
            narrative_state_id=self._narrative.state_id,
            narrative_template_id=self._narrative.template_id,
            narrative_params=dict(self._narrative.params),
            narrative_as_of_close_ms=self._narrative.as_of_close_ms,
            narrative_driver_tf=self._narrative.driver_tf,
            narrative_started_close_ms=self._narrative.started_close_ms,
            narrative_age_closes=self._narrative.age_closes,
            narrative_reason_codes=list(self._narrative.reason_codes),
            narrative_quality_flags=list(self._narrative.quality_flags),
            narrative_text_template=self._narrative.text_template,
            narrative_text_agent=self._narrative.text_agent,
        )

    def _evaluate_narrative(self, driver_close_ms: int) -> dict[str, object]:
        result = _compute_narrative_payload(
            rows=self._rows,
            driver_tf=self._config.narrative_driver_tf,
            dir_ratio_min=self._config.narrative_dir_ratio_min,
            secondary_min_ratio=self._config.narrative_secondary_min_ratio,
        )
        prev_state = self._narrative.state_id
        new_state = result["narrative_state_id"]
        state_changed = new_state != prev_state
        if state_changed:
            started_close_ms = driver_close_ms
            age_closes = 1 if new_state is not None else None
        else:
            started_close_ms = self._narrative.started_close_ms
            if new_state is None:
                age_closes = None
            else:
                prior_age = self._narrative.age_closes or 0
                age_closes = prior_age + 1
        self._narrative.state_id = cast(str | None, result["narrative_state_id"])
        self._narrative.template_id = cast(str | None, result["narrative_template_id"])
        self._narrative.params = cast(dict[str, NarrativeParamValue], result["narrative_params"])
        self._narrative.as_of_close_ms = driver_close_ms
        self._narrative.driver_tf = self._config.narrative_driver_tf
        self._narrative.started_close_ms = started_close_ms
        self._narrative.age_closes = age_closes
        self._narrative.reason_codes = cast(list[str], result["narrative_reason_codes"])
        self._narrative.quality_flags = cast(list[str], result["narrative_quality_flags"])
        self._narrative.text_template = cast(str | None, result["narrative_text_template"])
        self._narrative.text_agent = None

        emitted = state_changed
        if (
            not emitted
            and self._config.narrative_linger_reminder_closes > 0
            and self._narrative.state_id is not None
            and self._narrative.age_closes is not None
            and self._narrative.age_closes > 0
            and self._narrative.age_closes % self._config.narrative_linger_reminder_closes == 0
        ):
            emitted = True

        stack_tokens = {
            tf: {
                "token": row.token,
                "token_strength": row.token_strength,
                "ready_core": _is_ready_core_row(row, self._config.ready_core_min_bars, self._config.v_scale_min_samples),
                "ready_p": row.oi_var_initialized and row.oi_deltas_seen >= self._config.ready_p_min_deltas,
                "p": row.metrics.p,
            }
            for tf, row in self._rows.items()
        }

        return {
            "narrative_emitted": emitted,
            "narrative_emission_reason": (
                "state_change" if state_changed else ("linger_reminder" if emitted else None)
            ),
            "narrative_state_id": self._narrative.state_id,
            "narrative_template_id": self._narrative.template_id,
            "narrative_params": dict(self._narrative.params),
            "narrative_as_of_close_ms": self._narrative.as_of_close_ms,
            "narrative_driver_tf": self._narrative.driver_tf,
            "narrative_started_close_ms": self._narrative.started_close_ms,
            "narrative_age_closes": self._narrative.age_closes,
            "narrative_reason_codes": list(self._narrative.reason_codes),
            "narrative_quality_flags": list(self._narrative.quality_flags),
            "narrative_text_template": self._narrative.text_template,
            "narrative_text_agent": self._narrative.text_agent,
            "narrative_stack_tokens": stack_tokens,
        }

    def _warmup_klines(self, tf: DistTimeframe) -> None:
        params = {
            "symbol": f"{self._config.symbol.upper()}USDT",
            "interval": tf,
            "limit": str(self._config.warmup_kline_bars),
        }
        payload = self._http_json(self._kline_base, params)
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, list) or len(item) < 7:
                continue
            event = DistKlineCloseEvent(
                ts_recv_ms=int(item[6]),
                symbol=self._config.symbol,
                source_id=self._config.source_id,
                tf=tf,
                kline_open_ms=int(item[0]),
                kline_close_ms=int(item[6]),
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
            )
            self._apply_close(self._rows[tf], event, _CloseSelection(None, "warmup_no_oi", None))

    def _warmup_oi(self) -> None:
        last_oi: float | None = None
        for tf in ("15m", "1h", "4h"):
            if tf not in self._rows:
                continue
            series = self._fetch_oi_hist(tf, self._config.warmup_oi_hist_points)
            self._seed_row_oi(self._rows[tf], series)
            if series:
                last_oi = series[-1]
        if "3m" in self._rows:
            series_5m = self._fetch_oi_hist("5m", self._config.oi_seed_points)
            self._seed_3m_from_5m(self._rows["3m"], series_5m)
            if series_5m:
                last_oi = series_5m[-1]
        if last_oi is not None:
            self._oi_initialized = True
            self._oi_bootstrap_source = "warmup_hist"
            self._oi_bootstrap_age_ms = 0
            self._last_oi_value = last_oi

    def _fetch_oi_hist(self, period: str, limit: int) -> list[float]:
        params = {
            "symbol": f"{self._config.symbol.upper()}USDT",
            "period": period,
            "limit": str(limit),
        }
        payload = self._http_json(self._oi_hist_url, params)
        out: list[float] = []
        if not isinstance(payload, list):
            return out
        for item in payload:
            if not isinstance(item, dict):
                continue
            value = item.get("sumOpenInterest")
            if not isinstance(value, (int, float, str)):
                continue
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out

    def _seed_row_oi(self, row: _DistRowState, series: list[float]) -> None:
        if len(series) < 2:
            return
        prev = series[0]
        for cur in series[1:]:
            delta = cur - prev
            row.var_oi = self._lambda_oi * row.var_oi + (1 - self._lambda_oi) * (delta * delta)
            row.oi_deltas_seen += 1
            prev = cur
        row.prev_oi = series[-1]
        row.oi_var_initialized = row.oi_deltas_seen >= self._config.oi_seed_min_points

    def _seed_3m_from_5m(self, row: _DistRowState, series_5m: list[float]) -> None:
        if len(series_5m) < 2:
            return
        hl_oi_bars_5m = self._config.hl_oi_bars * (3.0 / 5.0)
        lambda_5m = _ewma_lambda(hl_oi_bars_5m)
        var_oi_5m = 0.0
        deltas = 0
        prev = series_5m[0]
        for cur in series_5m[1:]:
            delta = cur - prev
            var_oi_5m = lambda_5m * var_oi_5m + (1 - lambda_5m) * (delta * delta)
            prev = cur
            deltas += 1
        if deltas < self._config.oi_seed_min_points:
            return
        row.var_oi = var_oi_5m * (3.0 / 5.0)
        row.prev_oi = series_5m[-1]
        row.oi_var_initialized = True

    def _accept_close(self, row: _DistRowState, close_ms: int) -> bool:
        if close_ms in row.processed_close_set:
            return False
        if row.last_processed_close_ms is not None and close_ms < row.last_processed_close_ms:
            return False
        row.last_processed_close_ms = close_ms
        row.processed_close_set.add(close_ms)
        row.processed_close_keys.append(close_ms)
        while len(row.processed_close_keys) > 4096:
            old = row.processed_close_keys.popleft()
            row.processed_close_set.discard(old)
        return True

    def _select_snapshot_for_close(
        self, event: DistKlineCloseEvent
    ) -> tuple[_CloseSelection, dict[str, object]]:
        key = (event.source_id, event.kline_close_ms)
        frozen = self._close_selections.get(key)
        if frozen is not None:
            return frozen, self._selection_debug_for_frozen(frozen, event.kline_close_ms)
        if not self._oi_initialized:
            selection = _CloseSelection(None, "not_initialized", None)
            self._freeze_close_selection(key, selection)
            return selection, self._selection_debug_for_frozen(selection, event.kline_close_ms)

        candidates: list[tuple[DistOiSamplerSnapshot, str]] = []
        if event.sampler_snapshot is not None:
            candidates.append((event.sampler_snapshot, "sampler"))
        if event.verify_snapshot is not None:
            candidates.append((event.verify_snapshot, "verify"))

        eligible: list[tuple[DistOiSamplerSnapshot, str, int]] = []
        missing_reason: str | None = "no_sampler_value"
        sampler_reason: str | None = None
        verify_reason: str | None = None
        for snapshot, source in candidates:
            reason = self._validate_snapshot(snapshot, event.kline_close_ms)
            if source == "sampler":
                sampler_reason = reason
            elif source == "verify":
                verify_reason = reason
            if reason is None:
                offset = abs((snapshot.venue_time_ms or event.kline_close_ms) - event.kline_close_ms)
                eligible.append((snapshot, source, offset))
            elif missing_reason == "no_sampler_value":
                missing_reason = reason

        best_candidate: DistOiSamplerSnapshot | None = None
        best_candidate_source: str | None = None
        best_candidate_abs_offset_ms: int | None = None
        if candidates:
            ranked = sorted(
                candidates,
                key=lambda item: _snapshot_abs_offset_ms(item[0], event.kline_close_ms),
            )
            best_candidate = ranked[0][0]
            best_candidate_source = ranked[0][1]
            best_candidate_abs_offset_ms = _snapshot_abs_offset_ms(best_candidate, event.kline_close_ms)

        if not eligible:
            selection = _CloseSelection(None, missing_reason, None)
            self._freeze_close_selection(key, selection)
            return selection, {
                "sampler_reason": sampler_reason,
                "verify_reason": verify_reason,
                "sampler_offset_ms": _snapshot_offset_ms(event.sampler_snapshot, event.kline_close_ms),
                "verify_offset_ms": _snapshot_offset_ms(event.verify_snapshot, event.kline_close_ms),
                "sampler_tolerance_margin_ms": _snapshot_tolerance_margin_ms(
                    event.sampler_snapshot, event.kline_close_ms, self._config.oi_tolerance_ms
                ),
                "verify_tolerance_margin_ms": _snapshot_tolerance_margin_ms(
                    event.verify_snapshot, event.kline_close_ms, self._config.oi_tolerance_ms
                ),
                "best_candidate_source": best_candidate_source,
                "best_candidate_abs_offset_ms": best_candidate_abs_offset_ms,
                "best_candidate_tolerance_margin_ms": (
                    self._config.oi_tolerance_ms - best_candidate_abs_offset_ms
                    if best_candidate_abs_offset_ms is not None
                    else None
                ),
                "selected_offset_ms": None,
                "selected_abs_offset_ms": None,
                "selected_tolerance_margin_ms": None,
            }

        eligible.sort(
            key=lambda item: (
                item[2],
                -(item[0].venue_time_ms if item[0].venue_time_ms is not None else -1),
                -item[0].sample_seq,
            )
        )
        best_snapshot, best_source, _ = eligible[0]
        self._last_oi_ts_recv_ms = best_snapshot.ts_recv_ms
        self._last_oi_value = best_snapshot.oi
        selection = _CloseSelection(best_snapshot, None, best_source)
        self._freeze_close_selection(key, selection)
        selected_offset_ms = _snapshot_offset_ms(best_snapshot, event.kline_close_ms)
        selected_abs_offset_ms = (
            abs(selected_offset_ms) if selected_offset_ms is not None else None
        )
        return selection, {
            "sampler_reason": sampler_reason,
            "verify_reason": verify_reason,
            "sampler_offset_ms": _snapshot_offset_ms(event.sampler_snapshot, event.kline_close_ms),
            "verify_offset_ms": _snapshot_offset_ms(event.verify_snapshot, event.kline_close_ms),
            "sampler_tolerance_margin_ms": _snapshot_tolerance_margin_ms(
                event.sampler_snapshot, event.kline_close_ms, self._config.oi_tolerance_ms
            ),
            "verify_tolerance_margin_ms": _snapshot_tolerance_margin_ms(
                event.verify_snapshot, event.kline_close_ms, self._config.oi_tolerance_ms
            ),
            "best_candidate_source": best_candidate_source,
            "best_candidate_abs_offset_ms": best_candidate_abs_offset_ms,
            "best_candidate_tolerance_margin_ms": (
                self._config.oi_tolerance_ms - best_candidate_abs_offset_ms
                if best_candidate_abs_offset_ms is not None
                else None
            ),
            "selected_offset_ms": selected_offset_ms,
            "selected_abs_offset_ms": selected_abs_offset_ms,
            "selected_tolerance_margin_ms": (
                self._config.oi_tolerance_ms - selected_abs_offset_ms
                if selected_abs_offset_ms is not None
                else None
            ),
        }

    def _selection_debug_for_frozen(
        self,
        selection: _CloseSelection,
        t_close: int,
    ) -> dict[str, object]:
        selected_offset_ms = _snapshot_offset_ms(selection.snapshot, t_close)
        selected_abs_offset_ms = abs(selected_offset_ms) if selected_offset_ms is not None else None
        return {
            "sampler_reason": None,
            "verify_reason": None,
            "sampler_offset_ms": None,
            "verify_offset_ms": None,
            "sampler_tolerance_margin_ms": None,
            "verify_tolerance_margin_ms": None,
            "best_candidate_source": selection.source,
            "best_candidate_abs_offset_ms": selected_abs_offset_ms,
            "best_candidate_tolerance_margin_ms": (
                self._config.oi_tolerance_ms - selected_abs_offset_ms
                if selected_abs_offset_ms is not None
                else None
            ),
            "selected_offset_ms": selected_offset_ms,
            "selected_abs_offset_ms": selected_abs_offset_ms,
            "selected_tolerance_margin_ms": (
                self._config.oi_tolerance_ms - selected_abs_offset_ms
                if selected_abs_offset_ms is not None
                else None
            ),
        }

    def _freeze_close_selection(self, key: tuple[str, int], selection: _CloseSelection) -> None:
        if key not in self._close_selections:
            self._close_selection_keys.append(key)
        self._close_selections[key] = selection
        while len(self._close_selection_keys) > 4096:
            old = self._close_selection_keys.popleft()
            self._close_selections.pop(old, None)

    def _validate_snapshot(self, snapshot: DistOiSamplerSnapshot, t_close: int) -> str | None:
        if snapshot.venue_time_ms is None:
            if self._config.oi_time_missing_policy == "reject":
                return "time_missing_policy"
            return None
        diff = snapshot.venue_time_ms - t_close
        if diff < -self._config.oi_tolerance_ms:
            return "stale_over_limit"
        if diff > self._config.oi_tolerance_ms:
            return "offset_over_limit"
        return None

    def _apply_close(
        self,
        row: _DistRowState,
        event: DistKlineCloseEvent,
        selection: _CloseSelection,
    ) -> tuple[bool, str | None, dict[str, object]]:
        prev_close = row.prev_close
        r_t = math.log(event.close / prev_close) if prev_close and prev_close > 0 else 0.0
        row.var_r = self._lambda_vol * row.var_r + (1 - self._lambda_vol) * (r_t * r_t)
        sigma_r = math.sqrt(max(row.var_r, 0.0))
        if sigma_r > 0:
            row.v_scale_samples.append(sigma_r)
            while len(row.v_scale_samples) > self._config.v_scale_window_bars:
                row.v_scale_samples.popleft()
        sigma_scale = _scale_value(
            row.v_scale_samples,
            percentile=self._config.v_scale_percentile,
            min_samples=self._config.v_scale_min_samples,
        )
        v_norm = sigma_r / (sigma_scale + EPSILON)
        v = v_norm / (1.0 + v_norm)

        x_t = math.log(event.close)
        if row.mu_x is None:
            row.mu_x = x_t
        dx_t = x_t - row.mu_x
        row.var_dx = self._lambda_stretch * row.var_dx + (1 - self._lambda_stretch) * (dx_t * dx_t)
        row.mu_x = self._lambda_stretch * row.mu_x + (1 - self._lambda_stretch) * x_t
        sigma_x = math.sqrt(max(row.var_dx, 0.0))
        s_raw = (x_t - row.mu_x) / (sigma_x + EPSILON)
        s = math.tanh(self._config.k_s * s_raw)

        same = 1.0 if _sign(r_t) != 0 and _sign(r_t) == _sign(row.prev_return or 0.0) else 0.0
        row.p_same = self._lambda_a * row.p_same + (1 - self._lambda_a) * same
        a = max(-1.0, min(1.0, 2.0 * (row.p_same - 0.5)))

        tr = event.high - event.low
        if prev_close is not None:
            tr = max(tr, abs(event.high - prev_close), abs(event.low - prev_close))
        row.atr_s = tr if row.atr_s is None else self._lambda_atr_s * row.atr_s + (1 - self._lambda_atr_s) * tr
        row.atr_l = tr if row.atr_l is None else self._lambda_atr_l * row.atr_l + (1 - self._lambda_atr_l) * tr
        t_raw = (row.atr_s or 0.0) / ((row.atr_l or 0.0) + EPSILON)
        t = math.tanh(self._config.k_t * math.log(max(t_raw, EPSILON)))

        p: float | None = None
        p_missing_reason: str | None = selection.reason
        if selection.snapshot is not None and row.prev_oi is not None:
            delta_oi = selection.snapshot.oi - row.prev_oi
            row.var_oi = self._lambda_oi * row.var_oi + (1 - self._lambda_oi) * (delta_oi * delta_oi)
            row.oi_deltas_seen += 1
            if row.oi_deltas_seen >= self._config.oi_seed_min_points:
                row.oi_var_initialized = True
            if row.oi_var_initialized:
                sigma_oi = math.sqrt(max(row.var_oi, 0.0))
                p_raw = (delta_oi / (sigma_oi + EPSILON)) * _sign(r_t)
                p = math.tanh(self._config.k_p * p_raw)
                p_missing_reason = None
            else:
                p_missing_reason = "not_initialized"
            row.prev_oi = selection.snapshot.oi
        elif selection.snapshot is not None and row.prev_oi is None:
            row.prev_oi = selection.snapshot.oi
            p_missing_reason = "not_initialized"

        row.prev_close = event.close
        row.prev_return = r_t
        row.last_close_ms = event.kline_close_ms
        row.bars_seen += 1
        current_metrics = DistRowMetrics(v=v, s=s, a=a, p=p, t=t)
        row.metrics = current_metrics
        row.bins = DistRowBins(
            v=_bin_unit(v),
            s=_bin_symmetric(s),
            a=_bin_symmetric(a),
            p=_bin_symmetric(p),
            t=_bin_symmetric(t),
        )
        token_debug = self._update_row_token(row)
        row.prev_metrics = current_metrics
        return p is not None, p_missing_reason, token_debug

    def _update_row_token(self, row: _DistRowState) -> dict[str, object]:
        prev_token = row.token
        prev_strength = row.token_strength
        metrics = row.metrics
        token_debug = {
            "changed": False,
            "prev_token": prev_token,
            "prev_strength": prev_strength,
            "dwell_blocked": False,
            "override_reason": None,
            "predicate_hits": {
                "exp": False,
                "exh": False,
                "cont": False,
                "revert": False,
                "comp": False,
                "neut": False,
                "extended": False,
            },
            "inputs": {
                "v": metrics.v,
                "s": metrics.s,
                "a": metrics.a,
                "t": metrics.t,
                "p_present": metrics.p is not None,
                "p": metrics.p,
                "t_delta": None,
            },
        }
        if not self._config.tokens_enabled:
            row.token = None
            row.token_strength = None
            return token_debug

        if not self._is_ready_core(row):
            row.token = None
            row.token_strength = None
            return token_debug

        v = metrics.v if metrics.v is not None else 0.0
        s = metrics.s if metrics.s is not None else 0.0
        a = metrics.a if metrics.a is not None else 0.0
        t = metrics.t if metrics.t is not None else 0.0
        p = metrics.p

        # Step 1: hysteresis latches.
        row.token_exp_latched = _hysteresis_update(
            row.token_exp_latched,
            t >= self._config.t_exp_enter,
            t <= self._config.t_exp_exit,
        )
        row.token_comp_latched = _hysteresis_update(
            row.token_comp_latched,
            t <= self._config.t_comp_enter,
            t >= self._config.t_comp_exit,
        )
        row.token_cont_latched = _hysteresis_update(
            row.token_cont_latched,
            a >= self._config.a_cont_enter,
            a <= self._config.a_cont_exit,
        )
        row.token_revert_latched = _hysteresis_update(
            row.token_revert_latched,
            a <= self._config.a_revert_enter,
            a >= self._config.a_revert_exit,
        )
        row.token_extended_latched = _hysteresis_update(
            row.token_extended_latched,
            abs(s) >= self._config.s_ext_enter,
            abs(s) <= self._config.s_ext_exit,
        )
        s_dir = _s_dir(s, self._config.s_dir_deadband)
        extended = row.token_extended_latched
        exp = row.token_exp_latched
        cont = row.token_cont_latched
        revert = row.token_revert_latched
        comp = row.token_comp_latched and (v <= self._config.v_low_threshold)
        neut = (
            abs(s) <= self._config.s_neut_max
            and abs(a) <= self._config.a_neut_max
            and abs(t) <= self._config.t_neut_max
            and self._config.v_neut_min <= v <= self._config.v_neut_max
        )

        # Step 2: precedence candidate.
        candidate: DistRowToken | None = None
        if exp:
            candidate = "EXP"
        elif extended and s_dir != 0 and (revert or row.token_comp_latched):
            candidate = "EXH↑" if s_dir > 0 else "EXH↓"
        elif cont and s_dir != 0 and not extended:
            candidate = "CONT↑" if s_dir > 0 else "CONT↓"
        elif revert and abs(s) >= self._config.s_revert_min_stretch:
            candidate = "REVERT"
        elif comp:
            candidate = "COMP"
        elif neut:
            candidate = "NEUT"

        token_debug["predicate_hits"] = {
            "exp": exp,
            "exh": extended and s_dir != 0 and (revert or row.token_comp_latched) and not exp,
            "cont": cont and s_dir != 0 and not extended and not exp,
            "revert": revert and abs(s) >= self._config.s_revert_min_stretch and not exp,
            "comp": comp and not exp,
            "neut": neut and not exp,
            "extended": extended,
        }

        # Step 3: dwell.
        final_token = candidate
        override_reason: str | None = None
        if candidate != prev_token:
            if prev_token is None:
                pass
            elif candidate in {"EXP", "EXH↑", "EXH↓"}:
                override_reason = "exp_override" if candidate == "EXP" else "exh_override"
            elif row.token_bars_since_change < _token_hold_bars(self._config, row.tf):
                final_token = prev_token
                token_debug["dwell_blocked"] = True

        # Step 4: modifiers.
        t_prev = row.prev_metrics.t if row.prev_metrics is not None else None
        t_delta: float | None = None
        if t_prev is not None:
            t_delta = t - t_prev
        token_debug["inputs"]["t_delta"] = t_delta
        final_strength = _token_strength(self._config, final_token, s=s, a=a, t=t)
        risk = False
        if final_token in {"CONT↑", "CONT↓", "EXP", "EXH↑", "EXH↓"} and p is not None:
            if p >= self._config.p_confirm_threshold:
                if final_token in {"CONT↑", "CONT↓", "EXP"}:
                    final_strength = _bump_strength(final_strength)
                elif final_token in {"EXH↑", "EXH↓"}:
                    risk = True
            elif p <= -self._config.p_confirm_threshold:
                if final_token in {"CONT↑", "CONT↓", "EXP"}:
                    risk = True
                elif final_token in {"EXH↑", "EXH↓"}:
                    final_strength = _bump_strength(final_strength)
        if final_token == "EXP" and v <= self._config.v_low_threshold:
            risk = True
        if (
            final_token == "COMP"
            and v <= self._config.v_low_threshold
            and t_delta is not None
            and t_delta >= self._config.t_rise_threshold
        ):
            risk = True
        final_strength_str = _merge_strength_and_risk(final_strength, risk)
        final_token = _guard_token(final_token, self._config.tokens_fail_fast_unknown)
        if final_token is None:
            final_strength_str = None

        if final_token != prev_token:
            row.token_bars_since_change = 0
        else:
            row.token_bars_since_change += 1
        row.token = final_token
        row.token_strength = final_strength_str

        token_debug["changed"] = (row.token != prev_token) or (row.token_strength != prev_strength)
        token_debug["override_reason"] = override_reason
        return token_debug

    def _is_ready_core(self, row: _DistRowState) -> bool:
        return (
            row.bars_seen >= self._config.ready_core_min_bars
            and len(row.v_scale_samples) >= self._config.v_scale_min_samples
            and row.atr_l is not None
        )

    def _http_json(self, url: str, params: dict[str, str]):
        query = urllib.parse.urlencode(params)
        full = f"{url}?{query}"
        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            LOGGER.debug("Dist-state HTTP call failed: %s", full, exc_info=True)
            return None


def _ewma_lambda(half_life_bars: float) -> float:
    bars = max(half_life_bars, 1e-6)
    return math.exp(-math.log(2.0) / bars)


def _scale_value(samples: deque[float], *, percentile: float, min_samples: int) -> float:
    if not samples:
        return 1.0
    values = sorted(samples)
    if len(values) < min_samples:
        return values[len(values) // 2]
    idx = int(round(max(0.0, min(1.0, percentile)) * (len(values) - 1)))
    return max(values[idx], EPSILON)


def _sign(value: float) -> float:
    if value > EPSILON:
        return 1.0
    if value < -EPSILON:
        return -1.0
    return 0.0


def _bin_symmetric(value: float | None) -> int | None:
    if value is None:
        return None
    v = max(-1.0, min(1.0, value))
    return int(round(((v + 1.0) * 0.5) * (BIN_LEVELS - 1)))


def _bin_unit(value: float | None) -> int | None:
    if value is None:
        return None
    v = max(0.0, min(1.0, value))
    return int(round(v * (BIN_LEVELS - 1)))


def _snapshot_offset_ms(snapshot: DistOiSamplerSnapshot | None, t_close: int) -> int | None:
    if snapshot is None or snapshot.venue_time_ms is None:
        return None
    return snapshot.venue_time_ms - t_close


def _snapshot_abs_offset_ms(snapshot: DistOiSamplerSnapshot, t_close: int) -> int:
    if snapshot.venue_time_ms is None:
        return 10**18
    return abs(snapshot.venue_time_ms - t_close)


def _snapshot_tolerance_margin_ms(
    snapshot: DistOiSamplerSnapshot | None,
    t_close: int,
    tolerance_ms: int,
) -> int | None:
    offset = _snapshot_offset_ms(snapshot, t_close)
    if offset is None:
        return None
    return tolerance_ms - abs(offset)


def _hysteresis_update(current: bool, enter: bool, exit: bool) -> bool:
    if not current and enter:
        return True
    if current and exit:
        return False
    return current


def _s_dir(s_value: float, deadband: float) -> int:
    threshold = max(0.0, deadband)
    if s_value >= threshold:
        return 1
    if s_value <= -threshold:
        return -1
    return 0


def _token_hold_bars(config: DistStateConfig, tf: DistTimeframe) -> int:
    if tf == "3m":
        return config.token_min_hold_bars_3m
    if tf == "15m":
        return config.token_min_hold_bars_15m
    if tf == "1h":
        return config.token_min_hold_bars_1h
    return config.token_min_hold_bars_4h


def _token_strength(
    config: DistStateConfig,
    token: DistRowToken | None,
    *,
    s: float,
    a: float,
    t: float,
) -> str | None:
    if token is None:
        return None
    if token == "EXP":
        if t >= config.t_exp_plus_plus:
            return "++"
        if t >= config.t_exp_plus:
            return "+"
        return None
    if token == "COMP":
        if t <= config.t_comp_plus_plus:
            return "++"
        if t <= config.t_comp_plus:
            return "+"
        return None
    if token in {"CONT↑", "CONT↓"}:
        if a >= config.a_cont_plus_plus:
            return "++"
        if a >= config.a_cont_plus:
            return "+"
        return None
    if token == "REVERT":
        if a <= config.a_revert_plus_plus:
            return "++"
        if a <= config.a_revert_plus:
            return "+"
        return None
    if token in {"EXH↑", "EXH↓"}:
        stretch = abs(s)
        if stretch >= config.s_exh_plus_plus:
            return "++"
        if stretch >= config.s_exh_plus:
            return "+"
        return None
    if token == "NEUT":
        return None
    return None


def _bump_strength(strength: str | None) -> str:
    if strength == "++":
        return "++"
    if strength == "+":
        return "++"
    return "+"


def _merge_strength_and_risk(strength: str | None, risk: bool) -> str | None:
    if strength is None and not risk:
        return None
    if strength is None and risk:
        return "!"
    if strength is None:
        return None
    if risk:
        return f"{strength}!"
    return strength


def _guard_token(token: DistRowToken | None, fail_fast_unknown: bool) -> DistRowToken | None:
    if token is None:
        return None
    allowed: set[str] = {"COMP", "EXP", "CONT↑", "CONT↓", "EXH↑", "EXH↓", "REVERT", "NEUT"}
    if token in allowed:
        return token
    if fail_fast_unknown:
        raise ValueError(f"Unsupported dist-state token: {token}")
    LOGGER.warning("Unsupported dist-state token=%s; mapping to None", token)
    return None


def _compute_narrative_payload(
    *,
    rows: dict[DistTimeframe, _DistRowState],
    driver_tf: DistTimeframe,
    dir_ratio_min: float,
    secondary_min_ratio: float,
) -> dict[str, object]:
    vector = {key: 0.0 for key in _NARRATIVE_VECTOR_KEYS}
    class_members: dict[str, list[tuple[DistTimeframe, float, DistRowToken, str | None]]] = {
        key: [] for key in _NARRATIVE_VECTOR_KEYS
    }
    token_classes: list[tuple[str, DistRowToken]] = []
    instability_weight = 0.0
    for tf in sorted(rows.keys(), key=lambda item: _NARRATIVE_TF_ORDER[item]):
        row = rows[tf]
        if row.token is None:
            continue
        klass = _token_to_class(row.token)
        if klass is None:
            continue
        mult = _strength_multiplier(row.token_strength)
        weight = _NARRATIVE_TF_WEIGHT[tf] * mult
        vector[klass] += weight
        class_members[klass].append((tf, weight, row.token, row.token_strength))
        token_classes.append((klass, row.token))
        if row.token_strength is not None and "!" in row.token_strength:
            instability_weight += weight

    primary_class, primary_score = _argmax_class(vector, exclude=None)
    secondary_class, secondary_score = _argmax_class(vector, exclude=primary_class)
    if primary_score <= 0.0:
        return {
            "narrative_state_id": None,
            "narrative_template_id": None,
            "narrative_params": {},
            "narrative_reason_codes": [],
            "narrative_quality_flags": [],
            "narrative_text_template": None,
        }

    direction = _class_direction(class_members[primary_class], dir_ratio_min)
    representative_tf = _representative_tf(class_members[primary_class])
    support_tfs = [tf for tf, _, _, _ in sorted(class_members[primary_class], key=lambda it: _NARRATIVE_TF_ORDER[it[0]], reverse=True)]
    confidence = (primary_score - secondary_score) / primary_score if primary_score > 0.0 else 0.0
    denom = primary_score + secondary_score
    if denom > 0.0:
        instability_ratio = max(0.0, min(1.0, instability_weight / denom))
        confidence *= 1.0 - (0.2 * instability_ratio)
    quality_flags = _narrative_quality_flags(rows, driver_tf, token_classes)
    state_id, template_id = _map_narrative_state(primary_class, direction)
    secondary_direction = _class_direction(class_members[secondary_class], dir_ratio_min)
    secondary_phrase = None
    secondary_ratio = secondary_score / primary_score if primary_score > 0.0 else 0.0
    include_secondary = (
        primary_score > 0.0
        and secondary_score > 0.0
        and secondary_class != "NEUT"
        and secondary_ratio >= secondary_min_ratio
    )
    if include_secondary:
        secondary_phrase = _secondary_phrase(secondary_class, secondary_direction)
        template_id = f"{template_id}_WITH_SECONDARY"
    reason_codes = [f"PRIMARY_CLASS_{primary_class}"]
    params: dict[str, NarrativeParamValue] = {
        "primary_class": primary_class,
        "secondary_class": secondary_class,
        "primary_score": primary_score,
        "secondary_score": secondary_score,
        "confidence": confidence,
        "direction": direction,
        "secondary_direction": secondary_direction,
        "secondary_phrase": secondary_phrase,
        "representative_tf": representative_tf,
        "support_tfs": support_tfs,
        "stack_vector": {key: float(vector[key]) for key in _NARRATIVE_VECTOR_KEYS},
    }
    text = _render_narrative_text(template_id, params)
    return {
        "narrative_state_id": state_id,
        "narrative_template_id": template_id,
        "narrative_params": params,
        "narrative_reason_codes": reason_codes,
        "narrative_quality_flags": quality_flags,
        "narrative_text_template": text,
    }


def _argmax_class(vector: dict[str, float], *, exclude: str | None) -> tuple[str, float]:
    best_class = "NEUT"
    best_score = -1.0
    for klass in _NARRATIVE_CLASS_PRECEDENCE:
        if klass == exclude:
            continue
        score = vector.get(klass, 0.0)
        if score > best_score:
            best_class = klass
            best_score = score
    return best_class, best_score


def _token_to_class(token: DistRowToken) -> str | None:
    mapping = {
        "EXP": "EXP",
        "EXH↑": "EXH",
        "EXH↓": "EXH",
        "CONT↑": "CONT",
        "CONT↓": "CONT",
        "REVERT": "REVERT",
        "COMP": "COMP",
        "NEUT": "NEUT",
    }
    return mapping.get(token)


def _strength_multiplier(token_strength: str | None) -> float:
    if token_strength is None:
        return 1.0
    if "++" in token_strength:
        return 2.0
    if "+" in token_strength:
        return 1.5
    return 1.0


def _class_direction(
    members: list[tuple[DistTimeframe, float, DistRowToken, str | None]],
    min_ratio: float,
) -> str | None:
    if not members:
        return None
    dir_sum = 0.0
    total_weight = 0.0
    for _, weight, token, _ in members:
        sign = _token_dir_sign(token)
        if sign is None:
            continue
        dir_sum += float(sign) * weight
        total_weight += weight
    if total_weight <= 0.0:
        return None
    ratio = abs(dir_sum) / total_weight
    if ratio < min_ratio:
        return None
    return "UP" if dir_sum > 0.0 else "DOWN"


def _token_dir_sign(token: DistRowToken) -> int | None:
    if token in {"CONT↑", "EXH↑"}:
        return 1
    if token in {"CONT↓", "EXH↓"}:
        return -1
    return None


def _representative_tf(
    members: list[tuple[DistTimeframe, float, DistRowToken, str | None]],
) -> str | None:
    if not members:
        return None
    best = sorted(members, key=lambda it: (it[1], _NARRATIVE_TF_ORDER[it[0]]), reverse=True)[0]
    return best[0]


def _narrative_quality_flags(
    rows: dict[DistTimeframe, _DistRowState],
    driver_tf: DistTimeframe,
    token_classes: list[tuple[str, DistRowToken]],
) -> list[str]:
    flags: list[str] = []
    driver_row = rows.get(driver_tf)
    if (
        driver_row is not None
        and driver_row.oi_var_initialized
        and driver_row.oi_deltas_seen >= 1
        and driver_row.metrics.p is None
    ):
        flags.append("P_MISSING_DRIVER")
    if any(
        row.oi_var_initialized and row.oi_deltas_seen >= 1 and row.metrics.p is None
        for row in rows.values()
    ):
        flags.append("P_MISSING_ANY")
    has_cont_up = any(token == "CONT↑" for _, token in token_classes)
    has_cont_down = any(token == "CONT↓" for _, token in token_classes)
    has_exh_up = any(token == "EXH↑" for _, token in token_classes)
    has_exh_down = any(token == "EXH↓" for _, token in token_classes)
    if has_cont_up and has_cont_down:
        flags.append("DIR_CONFLICT_CONT")
    if has_exh_up and has_exh_down:
        flags.append("DIR_CONFLICT_EXH")
    return flags


def _map_narrative_state(primary_class: str, direction: str | None) -> tuple[str, str]:
    if primary_class == "EXP":
        return "N_EXPANSION_ACTIVE", "TPL_EXPANSION_ACTIVE"
    if primary_class == "EXH":
        if direction == "UP":
            return "N_EXTENSION_DECAYING_UP", "TPL_EXTENSION_DECAYING_UP"
        if direction == "DOWN":
            return "N_EXTENSION_DECAYING_DOWN", "TPL_EXTENSION_DECAYING_DOWN"
        return "N_EXTENSION_DECAYING", "TPL_EXTENSION_DECAYING"
    if primary_class == "CONT":
        if direction == "UP":
            return "N_CONTINUATION_TRYING_UP", "TPL_CONTINUATION_TRYING_UP"
        if direction == "DOWN":
            return "N_CONTINUATION_TRYING_DOWN", "TPL_CONTINUATION_TRYING_DOWN"
        return "N_CONTINUATION_TRYING", "TPL_CONTINUATION_TRYING"
    if primary_class == "REVERT":
        return "N_REVERSION_ACTIVE", "TPL_REVERSION_ACTIVE"
    if primary_class == "COMP":
        return "N_COMPRESSION_COILING", "TPL_COMPRESSION_COILING"
    return "N_QUIET_NEUTRAL", "TPL_QUIET_NEUTRAL"


def _secondary_phrase(klass: str, direction: str | None) -> str | None:
    if klass == "EXP":
        return "expansion attempts"
    if klass == "COMP":
        return "compression pressure"
    if klass == "REVERT":
        return "reversion pressure"
    if klass == "EXH":
        if direction == "UP":
            return "extension decay ↑"
        if direction == "DOWN":
            return "extension decay ↓"
        return "extension decay"
    if klass == "CONT":
        if direction == "UP":
            return "continuation pressure ↑"
        if direction == "DOWN":
            return "continuation pressure ↓"
        return "continuation pressure"
    return None


def _render_narrative_text(
    template_id: str,
    params: dict[str, NarrativeParamValue],
) -> str | None:
    representative_tf = cast(str | None, params.get("representative_tf"))
    secondary_phrase = cast(str | None, params.get("secondary_phrase"))
    templates = {
        "TPL_EXPANSION_ACTIVE": f"Expansion active ({representative_tf}).",
        "TPL_EXTENSION_DECAYING_UP": f"Extension decaying ↑ ({representative_tf}).",
        "TPL_EXTENSION_DECAYING_DOWN": f"Extension decaying ↓ ({representative_tf}).",
        "TPL_EXTENSION_DECAYING": f"Extension decaying ({representative_tf}).",
        "TPL_CONTINUATION_TRYING_UP": f"Continuation bias ↑ ({representative_tf}).",
        "TPL_CONTINUATION_TRYING_DOWN": f"Continuation bias ↓ ({representative_tf}).",
        "TPL_CONTINUATION_TRYING": f"Continuation bias ({representative_tf}).",
        "TPL_REVERSION_ACTIVE": f"Reversion active ({representative_tf}).",
        "TPL_COMPRESSION_COILING": f"Compression coiling ({representative_tf}).",
        "TPL_QUIET_NEUTRAL": "Quiet neutral.",
        "TPL_EXPANSION_ACTIVE_WITH_SECONDARY": (
            f"Expansion active ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_EXTENSION_DECAYING_UP_WITH_SECONDARY": (
            f"Extension decaying ↑ ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_EXTENSION_DECAYING_DOWN_WITH_SECONDARY": (
            f"Extension decaying ↓ ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_EXTENSION_DECAYING_WITH_SECONDARY": (
            f"Extension decaying ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_CONTINUATION_TRYING_UP_WITH_SECONDARY": (
            f"Continuation bias ↑ ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_CONTINUATION_TRYING_DOWN_WITH_SECONDARY": (
            f"Continuation bias ↓ ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_CONTINUATION_TRYING_WITH_SECONDARY": (
            f"Continuation bias ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_REVERSION_ACTIVE_WITH_SECONDARY": (
            f"Reversion active ({representative_tf}) with {secondary_phrase}."
        ),
        "TPL_COMPRESSION_COILING_WITH_SECONDARY": (
            f"Compression coiling ({representative_tf}) with {secondary_phrase}."
        ),
    }
    return templates.get(template_id)


def _is_ready_core_row(row: _DistRowState, ready_core_min_bars: int, v_scale_min_samples: int) -> bool:
    return (
        row.bars_seen >= ready_core_min_bars
        and len(row.v_scale_samples) >= v_scale_min_samples
        and row.atr_l is not None
    )
