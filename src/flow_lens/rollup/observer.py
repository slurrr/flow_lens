from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Mapping

from flow_lens.dist_state.models import DistPanelSnapshot
from flow_lens.engine.state_engine import StateSnapshot
from flow_lens.models.event import Event

ROLLUP_MS = 15 * 60 * 1000
_HIST_SEGMENTS: tuple[str, ...] = (
    "total",
    "spot",
    "perp",
    "spot_buy",
    "spot_sell",
    "perp_buy",
    "perp_sell",
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiquidityRollupConfig:
    enabled: bool
    interval_minutes: int
    out_dir: str
    poc_bucket_pct: float
    xy_hist_bins: int
    y_deadband: float
    y_dwell_ms: int
    low_event_count: int


@dataclass
class _HistogramAccumulator:
    buckets: dict[int, float] = field(default_factory=dict)

    def add(self, bucket_id: int, weight: float) -> None:
        self.buckets[bucket_id] = self.buckets.get(bucket_id, 0.0) + weight


@dataclass
class _Highlight:
    ts_ms: int
    value: float
    context: dict[str, object]


@dataclass
class _YStateMachine:
    current: Literal["ACCEPT", "REJECT", "NEUT"] = "NEUT"
    candidate: Literal["ACCEPT", "REJECT", "NEUT"] = "NEUT"
    candidate_ms: int = 0
    run_ms: int = 0


@dataclass
class _IntervalAccumulator:
    symbol: str
    interval_id: int
    interval_start_ms: int
    interval_end_ms: int
    sample_count: int = 0
    duration_ms: int = 0
    quality_flags: set[str] = field(default_factory=set)
    late_event_dropped_count: int = 0
    event_count: int = 0
    poc_event_count: int = 0

    effort_total: float = 0.0
    effort_dir_net: float = 0.0
    effort_control_net: float = 0.0
    effort_spot_buy: float = 0.0
    effort_spot_sell: float = 0.0
    effort_perp_buy: float = 0.0
    effort_perp_sell: float = 0.0
    effort_per_key: dict[tuple[str, str, str], float] = field(default_factory=dict)
    effort_by_source: dict[str, float] = field(default_factory=dict)
    effort_by_source_side_aggr: dict[str, dict[str, float]] = field(default_factory=dict)

    poc_notional: dict[str, _HistogramAccumulator] = field(
        default_factory=lambda: {segment: _HistogramAccumulator() for segment in _HIST_SEGMENTS}
    )
    poc_base: dict[str, _HistogramAccumulator] = field(
        default_factory=lambda: {segment: _HistogramAccumulator() for segment in _HIST_SEGMENTS}
    )

    weighted_sums: dict[str, float] = field(default_factory=dict)
    x_hist_dt_ms: list[int] = field(default_factory=list)
    y_hist_dt_ms: list[int] = field(default_factory=list)

    price_open: float | None = None
    price_close: float | None = None
    price_high: float | None = None
    price_low: float | None = None

    share_ms_q_xpos_ypos: int = 0
    share_ms_q_xpos_yneg: int = 0
    share_ms_q_xneg_ypos: int = 0
    share_ms_q_xneg_yneg: int = 0
    share_ms_gate_low: int = 0
    share_ms_spot_fresh: int = 0
    share_ms_perp_fresh: int = 0

    price_series_time_ms: dict[str, int] = field(default_factory=dict)
    active_source_time_ms: dict[str, int] = field(default_factory=dict)
    selector_policy_time_ms: dict[str, int] = field(default_factory=dict)

    baseline_open: float | None = None
    baseline_close: float | None = None
    baseline_min: float | None = None
    baseline_max: float | None = None

    accept_event_count: int = 0
    reject_event_count: int = 0
    accept_time_ms: int = 0
    reject_time_ms: int = 0
    neut_time_ms: int = 0
    longest_accept_run_ms: int = 0
    longest_reject_run_ms: int = 0

    x_sign_flip_count: int = 0
    y_sign_flip_count: int = 0
    e_dir_sign_flip_count: int = 0
    price_series_switch_count: int = 0
    persistence_confirm_flip_count: int = 0

    highlights: dict[str, _Highlight] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.x_hist_dt_ms:
            self.x_hist_dt_ms = []
        if not self.y_hist_dt_ms:
            self.y_hist_dt_ms = []


@dataclass
class _SymbolState:
    last_now_ms: int | None = None
    last_state: StateSnapshot | None = None
    last_x_sign: int = 0
    last_y_sign: int = 0
    last_e_dir_sign: int = 0
    last_price_series_used: str | None = None
    last_persist_confirm_sign: int | None = None
    y_machine: _YStateMachine = field(default_factory=_YStateMachine)
    intervals: dict[int, _IntervalAccumulator] = field(default_factory=dict)
    last_emitted_interval_id: int | None = None


class _DailyJsonlWriter:
    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._current_day: str | None = None
        self._handle = None

    def write(self, *, interval_end_ms: int, record: dict[str, object]) -> None:
        day = time.strftime("%Y%m%d", time.gmtime(interval_end_ms / 1000.0))
        if day != self._current_day:
            if self._handle is not None:
                self._handle.close()
            path = self._out_dir / f"liquidity_rollup-{day}.jsonl"
            self._handle = path.open("a", encoding="utf-8")
            self._current_day = day
        assert self._handle is not None
        self._handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class LiquidityRollupObserver:
    def __init__(self, config: LiquidityRollupConfig) -> None:
        self._config = config
        self._enabled = True
        self._lock_handle = None
        out_dir = Path(config.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        lock_path = out_dir / "liquidity_rollup.lock"
        lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._enabled = False
            lock_handle.close()
            self._writer = None
            LOGGER.error(
                "Liquidity rollup writer disabled: lock already held (%s).",
                lock_path,
            )
        else:
            self._lock_handle = lock_handle
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(f"pid={os.getpid()}\n")
            lock_handle.flush()
            self._writer = _DailyJsonlWriter(out_dir)
            LOGGER.info(
                "Liquidity rollup writer lock acquired (%s).",
                lock_path,
            )
        self._symbols: dict[str, _SymbolState] = {}

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def on_tick(
        self,
        *,
        symbol: str,
        now_ms: int,
        events: Iterable[Event],
        state: StateSnapshot | None,
        dist_snapshot: DistPanelSnapshot | None = None,
    ) -> None:
        if not self._enabled or self._writer is None:
            return
        symbol_upper = symbol.upper()
        symbol_state = self._symbols.setdefault(symbol_upper, _SymbolState())
        step_events = tuple(events)
        now_interval_id = _interval_id(now_ms)
        current_acc = self._ensure_interval(symbol_state, symbol_upper, now_interval_id)
        current_acc.sample_count += 1

        self._route_events(
            symbol_state=symbol_state,
            symbol=symbol_upper,
            now_interval_id=now_interval_id,
            events=step_events,
        )

        last_now = symbol_state.last_now_ms
        if last_now is not None:
            dt_ms = now_ms - last_now
            if dt_ms <= 0:
                current_acc.quality_flags.add("NON_MONOTONIC_TIME")
            elif symbol_state.last_state is not None:
                self._integrate_outcomes(
                    symbol_state=symbol_state,
                    symbol=symbol_upper,
                    start_ms=last_now,
                    end_ms=now_ms,
                    state=symbol_state.last_state,
                )

        if state is not None:
            self._track_transitions(
                acc=current_acc,
                symbol_state=symbol_state,
                state=state,
            )
            self._track_highlights(
                acc=current_acc,
                now_ms=now_ms,
                state=state,
                events=step_events,
            )

        symbol_state.last_now_ms = now_ms
        symbol_state.last_state = state
        self._emit_closed_intervals(
            symbol_state=symbol_state,
            symbol=symbol_upper,
            now_interval_id=now_interval_id,
            dist_snapshot=dist_snapshot,
        )

    def _route_events(
        self,
        *,
        symbol_state: _SymbolState,
        symbol: str,
        now_interval_id: int,
        events: tuple[Event, ...],
    ) -> None:
        for event in events:
            event_interval_id = _interval_id(event.timestamp)
            if (
                symbol_state.last_emitted_interval_id is not None
                and event_interval_id <= symbol_state.last_emitted_interval_id
            ):
                current = self._ensure_interval(symbol_state, symbol, now_interval_id)
                current.late_event_dropped_count += 1
                current.quality_flags.add("LATE_EVENT_DROPPED")
                continue
            acc = self._ensure_interval(symbol_state, symbol, event_interval_id)
            self._accumulate_event(acc, event)

    def _accumulate_event(self, acc: _IntervalAccumulator, event: Event) -> None:
        acc.event_count += 1
        effort = float(event.effort_value)
        aggr_sign = 1.0 if event.aggressor_side == "buy" else -1.0
        ctrl_sign = 1.0 if event.side_type == "spot" else -1.0

        acc.effort_total += effort
        acc.effort_dir_net += aggr_sign * effort
        acc.effort_control_net += ctrl_sign * effort
        if event.side_type == "spot" and event.aggressor_side == "buy":
            acc.effort_spot_buy += effort
        elif event.side_type == "spot":
            acc.effort_spot_sell += effort
        elif event.aggressor_side == "buy":
            acc.effort_perp_buy += effort
        else:
            acc.effort_perp_sell += effort

        key = (event.source_id, event.side_type, event.aggressor_side)
        acc.effort_per_key[key] = acc.effort_per_key.get(key, 0.0) + effort
        acc.effort_by_source[event.source_id] = acc.effort_by_source.get(event.source_id, 0.0) + effort
        side_slot = acc.effort_by_source_side_aggr.setdefault(
            event.source_id,
            {"spot_buy": 0.0, "spot_sell": 0.0, "perp_buy": 0.0, "perp_sell": 0.0},
        )
        side_slot[f"{event.side_type}_{event.aggressor_side}"] += effort

        if not _is_valid_price(event.price):
            acc.quality_flags.add("INVALID_EVENT_PRICE")
            return
        acc.poc_event_count += 1
        bucket_id = _bucket_id(event.price, self._config.poc_bucket_pct)
        notional_weight = effort
        if event.base_qty is not None:
            base_weight = float(event.base_qty)
        else:
            base_weight = effort / float(event.price)
        segments = ["total", event.side_type, f"{event.side_type}_{event.aggressor_side}"]
        for segment in segments:
            acc.poc_notional[segment].add(bucket_id, notional_weight)
            acc.poc_base[segment].add(bucket_id, base_weight)

    def _integrate_outcomes(
        self,
        *,
        symbol_state: _SymbolState,
        symbol: str,
        start_ms: int,
        end_ms: int,
        state: StateSnapshot,
    ) -> None:
        crossed = _interval_id(start_ms) != _interval_id(end_ms - 1)
        cursor = start_ms
        while cursor < end_ms:
            interval_id = _interval_id(cursor)
            boundary_ms = (interval_id + 1) * ROLLUP_MS
            seg_end = min(end_ms, boundary_ms)
            dt_ms = seg_end - cursor
            acc = self._ensure_interval(symbol_state, symbol, interval_id)
            if crossed:
                acc.quality_flags.add("DT_CROSSED_BOUNDARY")
            self._integrate_segment(acc, state, dt_ms, symbol_state.y_machine)
            cursor = seg_end

    def _integrate_segment(
        self,
        acc: _IntervalAccumulator,
        state: StateSnapshot,
        dt_ms: int,
        y_machine: _YStateMachine,
    ) -> None:
        if dt_ms <= 0:
            return
        dt_float = float(dt_ms)
        acc.duration_ms += dt_ms
        sums = acc.weighted_sums
        for key, value in (
            ("x", state.x),
            ("y", state.y),
            ("dominance", state.dominance),
            ("e_spot_share", state.e_spot_share),
            ("total_effort_window", state.total_effort),
            ("e_dir_window", state.e_dir),
            ("halo", state.halo),
            ("gate", state.gate),
            ("max_source_share_window", state.max_source_share),
            ("eff_raw", state.eff_raw),
            ("disp", state.disp),
            ("disp_rate", state.disp_rate),
            ("effort_rate", state.effort_rate),
            ("control_baseline_x", state.control_baseline_x),
            ("x_rel_control_baseline", state.x - state.control_baseline_x),
            ("persist_raw", state.persist_raw),
            ("persist_dir_raw", state.persist_dir_raw),
            ("persist_slope", state.persist_slope),
            ("persist_activity_ms", 1.0 if state.persist_activity_flag else 0.0),
            ("persist_pivot_confirm_elapsed_s", state.persist_pivot_confirm_elapsed_s),
            ("persist_pivot_cooldown_remaining_s", state.persist_pivot_cooldown_remaining_s),
        ):
            sums[key] = sums.get(key, 0.0) + float(value) * dt_float

        if state.control_baseline_midnight_tick_x is not None:
            sums["x_rel_midnight"] = sums.get("x_rel_midnight", 0.0) + (
                (state.x - state.control_baseline_midnight_tick_x) * dt_float
            )
            sums["control_baseline_rel_midnight"] = sums.get(
                "control_baseline_rel_midnight", 0.0
            ) + ((state.control_baseline_x - state.control_baseline_midnight_tick_x) * dt_float)
            sums["midnight_tick_x"] = sums.get("midnight_tick_x", 0.0) + (
                state.control_baseline_midnight_tick_x * dt_float
            )
            sums["midnight_tick_duration"] = sums.get("midnight_tick_duration", 0.0) + dt_float

        tick_price = state.price_end
        if acc.price_open is None:
            acc.price_open = tick_price
        acc.price_close = tick_price
        acc.price_high = tick_price if acc.price_high is None else max(acc.price_high, tick_price)
        acc.price_low = tick_price if acc.price_low is None else min(acc.price_low, tick_price)

        if state.x >= 0 and state.y >= 0:
            acc.share_ms_q_xpos_ypos += dt_ms
        elif state.x >= 0 and state.y < 0:
            acc.share_ms_q_xpos_yneg += dt_ms
        elif state.x < 0 and state.y >= 0:
            acc.share_ms_q_xneg_ypos += dt_ms
        else:
            acc.share_ms_q_xneg_yneg += dt_ms
        if state.gate < 1.0:
            acc.share_ms_gate_low += dt_ms
        if state.spot_fresh:
            acc.share_ms_spot_fresh += dt_ms
        if state.perp_fresh:
            acc.share_ms_perp_fresh += dt_ms

        acc.price_series_time_ms[state.price_series_used] = (
            acc.price_series_time_ms.get(state.price_series_used, 0) + dt_ms
        )
        if state.active_price_source_id is not None:
            acc.active_source_time_ms[state.active_price_source_id] = (
                acc.active_source_time_ms.get(state.active_price_source_id, 0) + dt_ms
            )
        acc.selector_policy_time_ms[state.selector_policy] = (
            acc.selector_policy_time_ms.get(state.selector_policy, 0) + dt_ms
        )

        baseline = state.control_baseline_x
        if acc.baseline_open is None:
            acc.baseline_open = baseline
        acc.baseline_close = baseline
        acc.baseline_min = baseline if acc.baseline_min is None else min(acc.baseline_min, baseline)
        acc.baseline_max = baseline if acc.baseline_max is None else max(acc.baseline_max, baseline)

        self._integrate_xy_hist(acc, state, dt_ms)
        self._integrate_accept_reject(acc, y_machine, state.y, dt_ms)

    def _integrate_xy_hist(self, acc: _IntervalAccumulator, state: StateSnapshot, dt_ms: int) -> None:
        x_bin = _hist_bin(state.x, self._config.xy_hist_bins)
        y_bin = _hist_bin(state.y, self._config.xy_hist_bins)
        _ensure_hist_size(acc.x_hist_dt_ms, self._config.xy_hist_bins)
        _ensure_hist_size(acc.y_hist_dt_ms, self._config.xy_hist_bins)
        acc.x_hist_dt_ms[x_bin] += dt_ms
        acc.y_hist_dt_ms[y_bin] += dt_ms

    def _integrate_accept_reject(
        self,
        acc: _IntervalAccumulator,
        machine: _YStateMachine,
        y_value: float,
        dt_ms: int,
    ) -> None:
        target = _y_target(y_value, self._config.y_deadband)
        if self._config.y_dwell_ms <= 0:
            self._apply_y_state_duration(acc, machine, 0)
            self._switch_y_state(acc, machine, target)
            self._apply_y_state_duration(acc, machine, dt_ms)
            return

        if target == machine.current:
            machine.candidate = target
            machine.candidate_ms = 0
            self._apply_y_state_duration(acc, machine, dt_ms)
            return

        if machine.candidate != target:
            machine.candidate = target
            machine.candidate_ms = 0

        remaining = dt_ms
        threshold_left = self._config.y_dwell_ms - machine.candidate_ms
        if remaining < threshold_left:
            machine.candidate_ms += remaining
            self._apply_y_state_duration(acc, machine, remaining)
            return

        if threshold_left > 0:
            self._apply_y_state_duration(acc, machine, threshold_left)
            remaining -= threshold_left
        self._switch_y_state(acc, machine, target)
        machine.candidate_ms = 0
        if remaining > 0:
            self._apply_y_state_duration(acc, machine, remaining)

    def _switch_y_state(
        self,
        acc: _IntervalAccumulator,
        machine: _YStateMachine,
        target: Literal["ACCEPT", "REJECT", "NEUT"],
    ) -> None:
        machine.current = target
        machine.run_ms = 0
        if target == "ACCEPT":
            acc.accept_event_count += 1
        elif target == "REJECT":
            acc.reject_event_count += 1

    def _apply_y_state_duration(
        self,
        acc: _IntervalAccumulator,
        machine: _YStateMachine,
        dt_ms: int,
    ) -> None:
        if dt_ms <= 0:
            return
        state = machine.current
        machine.run_ms += dt_ms
        if state == "ACCEPT":
            acc.accept_time_ms += dt_ms
        elif state == "REJECT":
            acc.reject_time_ms += dt_ms
        else:
            acc.neut_time_ms += dt_ms
        if state == "ACCEPT":
            acc.longest_accept_run_ms = max(acc.longest_accept_run_ms, machine.run_ms)
        elif state == "REJECT":
            acc.longest_reject_run_ms = max(acc.longest_reject_run_ms, machine.run_ms)

    def _track_transitions(
        self,
        *,
        acc: _IntervalAccumulator,
        symbol_state: _SymbolState,
        state: StateSnapshot,
    ) -> None:
        x_sign = _nonzero_sign(state.x)
        y_sign = _nonzero_sign(state.y)
        e_dir_sign = _nonzero_sign(state.e_dir)
        if symbol_state.last_x_sign and x_sign and x_sign != symbol_state.last_x_sign:
            acc.x_sign_flip_count += 1
        if symbol_state.last_y_sign and y_sign and y_sign != symbol_state.last_y_sign:
            acc.y_sign_flip_count += 1
        if symbol_state.last_e_dir_sign and e_dir_sign and e_dir_sign != symbol_state.last_e_dir_sign:
            acc.e_dir_sign_flip_count += 1
        if (
            symbol_state.last_price_series_used is not None
            and state.price_series_used != symbol_state.last_price_series_used
        ):
            acc.price_series_switch_count += 1
        if (
            symbol_state.last_persist_confirm_sign is not None
            and state.persist_last_confirmed_dir_sign != symbol_state.last_persist_confirm_sign
        ):
            acc.persistence_confirm_flip_count += 1
        symbol_state.last_x_sign = x_sign if x_sign != 0 else symbol_state.last_x_sign
        symbol_state.last_y_sign = y_sign if y_sign != 0 else symbol_state.last_y_sign
        symbol_state.last_e_dir_sign = e_dir_sign if e_dir_sign != 0 else symbol_state.last_e_dir_sign
        symbol_state.last_price_series_used = state.price_series_used
        symbol_state.last_persist_confirm_sign = state.persist_last_confirmed_dir_sign

    def _track_highlights(
        self,
        *,
        acc: _IntervalAccumulator,
        now_ms: int,
        state: StateSnapshot,
        events: tuple[Event, ...],
    ) -> None:
        tick_eff = {"spot_buy": 0.0, "spot_sell": 0.0, "perp_buy": 0.0, "perp_sell": 0.0}
        for event in events:
            tick_eff[f"{event.side_type}_{event.aggressor_side}"] += event.effort_value
        top_source = state.top_source_id if state.top_source_id is not None else "~"
        context = {
            "x": state.x,
            "y": state.y,
            "dominance": state.dominance,
            "e_spot_share": state.e_spot_share,
            "total_effort": state.total_effort,
            "e_dir": state.e_dir,
            "halo": state.halo,
            "gate": state.gate,
            "eff_raw": state.eff_raw,
            "disp": state.disp,
            "max_source_share": state.max_source_share,
            "top_source_id": None if top_source == "~" else top_source,
            "spot_fresh": state.spot_fresh,
            "perp_fresh": state.perp_fresh,
            "price_series_used": state.price_series_used,
            "persist_last_confirmed_dir_sign": state.persist_last_confirmed_dir_sign,
            "spot_buy_effort": tick_eff["spot_buy"],
            "spot_sell_effort": tick_eff["spot_sell"],
            "perp_buy_effort": tick_eff["perp_buy"],
            "perp_sell_effort": tick_eff["perp_sell"],
        }
        self._maybe_set_highlight(acc, "max_total_effort", now_ms, state.total_effort, context)
        self._maybe_set_highlight(acc, "max_halo", now_ms, state.halo, context)
        self._maybe_set_highlight(acc, "max_abs_x", now_ms, abs(state.x), context)
        self._maybe_set_highlight(acc, "max_abs_y", now_ms, abs(state.y), context)
        self._maybe_set_highlight(acc, "max_abs_e_dir", now_ms, abs(state.e_dir), context)
        self._maybe_set_highlight(acc, "max_max_source_share", now_ms, state.max_source_share, context)
        self._maybe_set_highlight(acc, "min_gate", now_ms, state.gate, context, minimize=True)

    def _maybe_set_highlight(
        self,
        acc: _IntervalAccumulator,
        category: str,
        ts_ms: int,
        value: float,
        context: Mapping[str, object],
        *,
        minimize: bool = False,
    ) -> None:
        current = acc.highlights.get(category)
        candidate = _Highlight(ts_ms=ts_ms, value=value, context=dict(context))
        if current is None:
            acc.highlights[category] = candidate
            return
        if _highlight_better(candidate, current, minimize=minimize):
            acc.highlights[category] = candidate

    def _emit_closed_intervals(
        self,
        *,
        symbol_state: _SymbolState,
        symbol: str,
        now_interval_id: int,
        dist_snapshot: DistPanelSnapshot | None,
    ) -> None:
        if self._writer is None:
            return
        if symbol_state.last_emitted_interval_id is not None:
            start = symbol_state.last_emitted_interval_id + 1
        elif symbol_state.intervals:
            # On first emission, start from the earliest observed interval so the
            # first fully-closed interval is not skipped.
            start = min(symbol_state.intervals.keys())
        else:
            start = now_interval_id
        for interval_id in range(start, now_interval_id):
            acc = self._ensure_interval(symbol_state, symbol, interval_id)
            symbol_state.last_emitted_interval_id = interval_id
            if acc.duration_ms <= 0:
                symbol_state.intervals.pop(interval_id, None)
                continue
            record = self._finalize_interval(acc, dist_snapshot=dist_snapshot)
            self._writer.write(interval_end_ms=acc.interval_end_ms, record=record)
            symbol_state.intervals.pop(interval_id, None)

    def _ensure_interval(
        self,
        symbol_state: _SymbolState,
        symbol: str,
        interval_id: int,
    ) -> _IntervalAccumulator:
        existing = symbol_state.intervals.get(interval_id)
        if existing is not None:
            return existing
        start_ms = interval_id * ROLLUP_MS
        acc = _IntervalAccumulator(
            symbol=symbol,
            interval_id=interval_id,
            interval_start_ms=start_ms,
            interval_end_ms=start_ms + ROLLUP_MS,
        )
        symbol_state.intervals[interval_id] = acc
        return acc

    def _finalize_interval(
        self,
        acc: _IntervalAccumulator,
        *,
        dist_snapshot: DistPanelSnapshot | None,
    ) -> dict[str, object]:
        duration = float(acc.duration_ms)
        means = {
            "mean_x": _safe_div(acc.weighted_sums.get("x", 0.0), duration),
            "mean_y": _safe_div(acc.weighted_sums.get("y", 0.0), duration),
            "mean_dominance": _safe_div(acc.weighted_sums.get("dominance", 0.0), duration),
            "mean_e_spot_share": _safe_div(acc.weighted_sums.get("e_spot_share", 0.0), duration),
            "mean_total_effort_window": _safe_div(
                acc.weighted_sums.get("total_effort_window", 0.0), duration
            ),
            "mean_e_dir_window": _safe_div(acc.weighted_sums.get("e_dir_window", 0.0), duration),
            "mean_halo": _safe_div(acc.weighted_sums.get("halo", 0.0), duration),
            "mean_gate": _safe_div(acc.weighted_sums.get("gate", 0.0), duration),
            "mean_max_source_share_window": _safe_div(
                acc.weighted_sums.get("max_source_share_window", 0.0), duration
            ),
            "mean_eff_raw": _safe_div(acc.weighted_sums.get("eff_raw", 0.0), duration),
            "mean_disp": _safe_div(acc.weighted_sums.get("disp", 0.0), duration),
            "mean_disp_rate": _safe_div(acc.weighted_sums.get("disp_rate", 0.0), duration),
            "mean_effort_rate": _safe_div(acc.weighted_sums.get("effort_rate", 0.0), duration),
            "mean_x_rel_control_baseline": _safe_div(
                acc.weighted_sums.get("x_rel_control_baseline", 0.0), duration
            ),
            "mean_control_baseline_x": _safe_div(
                acc.weighted_sums.get("control_baseline_x", 0.0), duration
            ),
            "mean_persist_raw": _safe_div(acc.weighted_sums.get("persist_raw", 0.0), duration),
            "mean_persist_dir_raw": _safe_div(acc.weighted_sums.get("persist_dir_raw", 0.0), duration),
            "mean_persist_slope": _safe_div(acc.weighted_sums.get("persist_slope", 0.0), duration),
            "mean_persist_pivot_confirm_elapsed_s": _safe_div(
                acc.weighted_sums.get("persist_pivot_confirm_elapsed_s", 0.0), duration
            ),
            "mean_persist_pivot_cooldown_remaining_s": _safe_div(
                acc.weighted_sums.get("persist_pivot_cooldown_remaining_s", 0.0), duration
            ),
            "share_q_xpos_ypos": _safe_div(float(acc.share_ms_q_xpos_ypos), duration),
            "share_q_xpos_yneg": _safe_div(float(acc.share_ms_q_xpos_yneg), duration),
            "share_q_xneg_ypos": _safe_div(float(acc.share_ms_q_xneg_ypos), duration),
            "share_q_xneg_yneg": _safe_div(float(acc.share_ms_q_xneg_yneg), duration),
            "share_gate_low": _safe_div(float(acc.share_ms_gate_low), duration),
            "share_spot_fresh": _safe_div(float(acc.share_ms_spot_fresh), duration),
            "share_perp_fresh": _safe_div(float(acc.share_ms_perp_fresh), duration),
            "share_persist_activity": _safe_div(
                acc.weighted_sums.get("persist_activity_ms", 0.0), duration
            ),
            "mean_midnight_tick_x": (
                _safe_div(
                    acc.weighted_sums.get("midnight_tick_x", 0.0),
                    acc.weighted_sums.get("midnight_tick_duration", 0.0),
                )
                if acc.weighted_sums.get("midnight_tick_duration", 0.0) > 0.0
                else None
            ),
            "mean_x_rel_midnight": (
                _safe_div(
                    acc.weighted_sums.get("x_rel_midnight", 0.0),
                    acc.weighted_sums.get("midnight_tick_duration", 0.0),
                )
                if acc.weighted_sums.get("midnight_tick_duration", 0.0) > 0.0
                else None
            ),
            "mean_control_baseline_rel_midnight": (
                _safe_div(
                    acc.weighted_sums.get("control_baseline_rel_midnight", 0.0),
                    acc.weighted_sums.get("midnight_tick_duration", 0.0),
                )
                if acc.weighted_sums.get("midnight_tick_duration", 0.0) > 0.0
                else None
            ),
        }
        control_drift = (
            (acc.baseline_close - acc.baseline_open)
            if acc.baseline_close is not None and acc.baseline_open is not None
            else 0.0
        )
        control_range = (
            (acc.baseline_max - acc.baseline_min)
            if acc.baseline_max is not None and acc.baseline_min is not None
            else 0.0
        )
        liquidity_interval = self._finalize_liquidity_interval(acc)
        x_edges = _hist_edges(self._config.xy_hist_bins)
        y_edges = _hist_edges(self._config.xy_hist_bins)
        outcome_interval = {
            **means,
            "price_open": acc.price_open,
            "price_close": acc.price_close,
            "log_return": _safe_log_return(acc.price_open, acc.price_close),
            "range_high": acc.price_high,
            "range_low": acc.price_low,
            "x_hist": {
                "bin_edges": x_edges,
                "dt_ms": acc.x_hist_dt_ms,
                "poc_bin": _argmax(acc.x_hist_dt_ms),
            },
            "y_hist": {
                "bin_edges": y_edges,
                "dt_ms": acc.y_hist_dt_ms,
                "poc_bin": _argmax(acc.y_hist_dt_ms),
            },
            "price_series_used_share": _share_items(acc.price_series_time_ms, duration),
            "active_price_source_id_share": _share_items(acc.active_source_time_ms, duration, key_name="source_id"),
            "selector_policy_share": _share_items(acc.selector_policy_time_ms, duration, key_name="selector_policy"),
            "control_baseline_drift": control_drift,
            "control_baseline_range": control_range,
            "accept_event_count": acc.accept_event_count,
            "reject_event_count": acc.reject_event_count,
            "accept_time_share": _safe_div(float(acc.accept_time_ms), duration),
            "reject_time_share": _safe_div(float(acc.reject_time_ms), duration),
            "neut_time_share": _safe_div(float(acc.neut_time_ms), duration),
            "longest_accept_run_ms": acc.longest_accept_run_ms,
            "longest_reject_run_ms": acc.longest_reject_run_ms,
            "x_sign_flip_count": acc.x_sign_flip_count,
            "y_sign_flip_count": acc.y_sign_flip_count,
            "e_dir_sign_flip_count": acc.e_dir_sign_flip_count,
        }

        if acc.sample_count < self._config.low_event_count:
            acc.quality_flags.add("SPARSE_SAMPLING")
        if acc.poc_event_count < self._config.low_event_count:
            acc.quality_flags.add("LOW_EVENT_COUNT")

        dist_payload: dict[str, object] | None = None
        if (
            dist_snapshot is not None
            and dist_snapshot.symbol.upper() == acc.symbol
            and dist_snapshot.narrative_as_of_close_ms == acc.interval_end_ms
        ):
            dist_payload = {
                "driver_tf": dist_snapshot.narrative_driver_tf,
                "as_of_close_ms": dist_snapshot.narrative_as_of_close_ms,
                "narrative_state_id": dist_snapshot.narrative_state_id,
                "narrative_template_id": dist_snapshot.narrative_template_id,
                "narrative_params": dist_snapshot.narrative_params,
                "narrative_reason_codes": dist_snapshot.narrative_reason_codes,
                "narrative_quality_flags": dist_snapshot.narrative_quality_flags,
            }

        return {
            "ts_ms": int(time.time_ns() // 1_000_000),
            "symbol": acc.symbol,
            "interval_start_ms": acc.interval_start_ms,
            "interval_end_ms": acc.interval_end_ms,
            "liquidity_state": {
                "symbol": acc.symbol,
                "interval_start_ms": acc.interval_start_ms,
                "interval_end_ms": acc.interval_end_ms,
                "duration_ms": acc.duration_ms,
                "sample_count": acc.sample_count,
                "liquidity_interval": liquidity_interval,
                "outcome_interval": outcome_interval,
                "price_series_switch_count": acc.price_series_switch_count,
                "persistence_confirm_flip_count": acc.persistence_confirm_flip_count,
                "highlights": {
                    name: {
                        "ts_ms": high.ts_ms,
                        "value": high.value,
                        "context": high.context,
                    }
                    for name, high in sorted(acc.highlights.items())
                },
                "quality_flags": sorted(acc.quality_flags),
            },
            "dist_state": dist_payload,
        }

    def _finalize_liquidity_interval(self, acc: _IntervalAccumulator) -> dict[str, object]:
        effort_total = acc.effort_total
        sources = sorted(
            acc.effort_by_source.items(),
            key=lambda item: (-item[1], item[0]),
        )
        shares = [value / effort_total for _, value in sources] if effort_total > 0.0 else []
        source_hhi = sum(share * share for share in shares)
        source_entropy = -sum(share * math.log(share) for share in shares if share > 0.0)
        top_source_id = sources[0][0] if sources else None
        top_source_share = shares[0] if shares else 0.0
        effort_matrix = [
            {
                "source_id": source_id,
                "side_type": side_type,
                "aggressor_side": aggressor_side,
                "effort": effort,
            }
            for (source_id, side_type, aggressor_side), effort in sorted(
                acc.effort_per_key.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        by_source_side = [
            {
                "source_id": source_id,
                "spot_buy": slots["spot_buy"],
                "spot_sell": slots["spot_sell"],
                "perp_buy": slots["perp_buy"],
                "perp_sell": slots["perp_sell"],
            }
            for source_id, slots in sorted(
                acc.effort_by_source_side_aggr.items(),
                key=lambda item: (
                    -(
                        item[1]["spot_buy"]
                        + item[1]["spot_sell"]
                        + item[1]["perp_buy"]
                        + item[1]["perp_sell"]
                    ),
                    item[0],
                ),
            )
        ]
        notional = {
            segment: _finalize_histogram(hist, self._config.poc_bucket_pct)
            for segment, hist in acc.poc_notional.items()
        }
        base = {
            segment: _finalize_histogram(hist, self._config.poc_bucket_pct)
            for segment, hist in acc.poc_base.items()
        }
        return {
            "effort_total": effort_total,
            "effort_dir_net": acc.effort_dir_net,
            "effort_control_net": acc.effort_control_net,
            "effort_spot_buy": acc.effort_spot_buy,
            "effort_spot_sell": acc.effort_spot_sell,
            "effort_perp_buy": acc.effort_perp_buy,
            "effort_perp_sell": acc.effort_perp_sell,
            "effort_matrix": effort_matrix,
            "effort_by_source": [
                {"source_id": source_id, "effort": effort}
                for source_id, effort in sources
            ],
            "effort_by_source_side_aggr": by_source_side,
            "source_hhi": source_hhi,
            "source_entropy": source_entropy,
            "top_source_id": top_source_id,
            "top_source_share": top_source_share,
            "late_event_dropped_count": acc.late_event_dropped_count,
            "price_poc": {
                "bucket_pct": self._config.poc_bucket_pct,
                "notional": notional,
                "base": base,
                "quality_flags": sorted(
                    flag
                    for flag in acc.quality_flags
                    if flag in {"LOW_EVENT_COUNT", "INVALID_EVENT_PRICE"}
                ),
            },
        }


def _interval_id(timestamp_ms: int) -> int:
    return timestamp_ms // ROLLUP_MS


def _is_valid_price(price: float) -> bool:
    return math.isfinite(price) and price > 0.0


def _bucket_id(price: float, bucket_pct: float) -> int:
    return int(math.floor(math.log(price) / math.log(1.0 + bucket_pct)))


def _bucket_mid_price(bucket_id: int, bucket_pct: float) -> float:
    low = (1.0 + bucket_pct) ** bucket_id
    high = (1.0 + bucket_pct) ** (bucket_id + 1)
    return (low + high) / 2.0


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return numerator / denominator


def _safe_log_return(price_open: float | None, price_close: float | None) -> float:
    if price_open is None or price_close is None:
        return 0.0
    if not _is_valid_price(price_open) or not _is_valid_price(price_close):
        return 0.0
    return math.log(price_close / price_open)


def _hist_bin(value: float, bins: int) -> int:
    clipped = max(-1.0, min(1.0, value))
    pos = (clipped + 1.0) / 2.0
    idx = int(pos * bins)
    if idx >= bins:
        idx = bins - 1
    if idx < 0:
        idx = 0
    return idx


def _ensure_hist_size(values: list[int], size: int) -> None:
    if len(values) == size:
        return
    values.clear()
    values.extend(0 for _ in range(size))


def _hist_edges(bins: int) -> list[float]:
    step = 2.0 / float(bins)
    return [(-1.0 + i * step) for i in range(bins + 1)]


def _argmax(values: list[int]) -> int | None:
    if not values:
        return None
    best_idx = 0
    best = values[0]
    for idx, value in enumerate(values[1:], start=1):
        if value > best:
            best = value
            best_idx = idx
    return best_idx


def _finalize_histogram(hist: _HistogramAccumulator, bucket_pct: float) -> dict[str, object]:
    if not hist.buckets:
        return {
            "start_bucket_id": 0,
            "values": [],
            "poc_bucket_id": None,
            "poc_price_mid": None,
            "poc_value": 0.0,
        }
    bucket_ids = sorted(hist.buckets.keys())
    start = bucket_ids[0]
    end = bucket_ids[-1]
    values = [hist.buckets.get(bucket_id, 0.0) for bucket_id in range(start, end + 1)]
    poc_bucket = max(
        bucket_ids,
        key=lambda bucket_id: (hist.buckets[bucket_id], -bucket_id),
    )
    poc_value = hist.buckets[poc_bucket]
    return {
        "start_bucket_id": start,
        "values": values,
        "poc_bucket_id": poc_bucket,
        "poc_price_mid": _bucket_mid_price(poc_bucket, bucket_pct),
        "poc_value": poc_value,
    }


def _y_target(y_value: float, deadband: float) -> Literal["ACCEPT", "REJECT", "NEUT"]:
    if y_value > deadband:
        return "ACCEPT"
    if y_value < -deadband:
        return "REJECT"
    return "NEUT"


def _nonzero_sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _share_items(
    values: dict[str, int],
    duration_ms: float,
    *,
    key_name: str = "price_series_used",
) -> list[dict[str, object]]:
    items = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            key_name: key,
            "share_time": _safe_div(float(total_ms), duration_ms),
        }
        for key, total_ms in items
    ]


def _highlight_better(candidate: _Highlight, current: _Highlight, *, minimize: bool) -> bool:
    if candidate.value != current.value:
        return candidate.value < current.value if minimize else candidate.value > current.value
    if candidate.ts_ms != current.ts_ms:
        return candidate.ts_ms < current.ts_ms
    candidate_source = candidate.context.get("top_source_id")
    current_source = current.context.get("top_source_id")
    if candidate_source is None and current_source is not None:
        return False
    if candidate_source is not None and current_source is None:
        return True
    if candidate_source != current_source:
        return str(candidate_source) < str(current_source)
    return str(candidate.context.get("price_series_used", "")) < str(
        current.context.get("price_series_used", "")
    )
