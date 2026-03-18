from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from flow_lens.config import DistStateRuntimeConfig, load_app_config
from flow_lens.dist_state.engine import DistStateConfig, DistStateEngine
from flow_lens.dist_state.models import DistKlineCloseEvent, DistTimeframe

TF_MS: dict[DistTimeframe, int] = {
    "3m": 3 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
TF_ORDER: dict[DistTimeframe, int] = {"3m": 0, "15m": 1, "1h": 2, "4h": 3}
POS_GRIDS = {
    "a": [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.24, 0.28, 0.32],
    "t": [0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.26, 0.30],
    "s": [0.18, 0.22, 0.26, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 0.80, 0.90],
    "v": [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
}
DEFAULT_HORIZONS: dict[DistTimeframe, tuple[int, ...]] = {
    "3m": (1, 3, 5),
    "15m": (1, 2, 4),
    "1h": (1, 2, 3),
    "4h": (1, 2),
}


@dataclass(frozen=True)
class OhlcBar:
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class RowRecord:
    tf: DistTimeframe
    index: int
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    log_return: float
    true_range: float
    ready_core: bool
    metrics_v: float | None
    metrics_s: float | None
    metrics_a: float | None
    metrics_t: float | None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round(max(0.0, min(1.0, q)) * (len(ordered) - 1)))
    return ordered[idx]


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or abs(den) < 1e-12:
        return None
    return num / den


def _sign(value: float) -> int:
    if value > 1e-12:
        return 1
    if value < -1e-12:
        return -1
    return 0


def _build_engine_config(dist: DistStateRuntimeConfig) -> DistStateConfig:
    # Metric calibration should read the raw bounded metric layer only.
    return DistStateConfig(
        enabled=True,
        symbol=dist.symbol,
        source_id=dist.source_id,
        timeframes=dist.timeframes,
        warmup_kline_bars=dist.warmup_kline_bars,
        warmup_oi_hist_points=dist.warmup_oi_hist_points,
        ready_core_min_bars=dist.ready_core_min_bars,
        ready_p_min_deltas=dist.ready_p_min_deltas,
        p_availability_mode=dist.p_availability_mode,
        oi_tolerance_ms=dist.oi_tolerance_ms,
        oi_time_missing_policy=dist.oi_time_missing_policy,
        oi_seed_points=dist.oi_seed_points,
        oi_seed_min_points=dist.oi_seed_min_points,
        v_scale_window_bars=dist.v_scale_window_bars,
        v_scale_percentile=dist.v_scale_percentile,
        v_scale_min_samples=dist.v_scale_min_samples,
        hl_vol_bars=dist.hl_vol_bars,
        hl_stretch_bars=dist.hl_stretch_bars,
        hl_oi_bars=dist.hl_oi_bars,
        hl_atr_short_bars=dist.hl_atr_short_bars,
        hl_atr_long_bars=dist.hl_atr_long_bars,
        hl_a_bars=dist.hl_a_bars,
        k_s=dist.k_s,
        k_p=dist.k_p,
        k_t=dist.k_t,
        tokens_enabled=False,
        tokens_fail_fast_unknown=dist.tokens_fail_fast_unknown,
        s_dir_deadband=dist.s_dir_deadband,
        s_ext_enter=dist.s_ext_enter,
        s_ext_exit=dist.s_ext_exit,
        s_revert_min_stretch=dist.s_revert_min_stretch,
        t_exp_enter=dist.t_exp_enter,
        t_exp_exit=dist.t_exp_exit,
        t_comp_enter=dist.t_comp_enter,
        t_comp_exit=dist.t_comp_exit,
        a_cont_enter=dist.a_cont_enter,
        a_cont_exit=dist.a_cont_exit,
        a_revert_enter=dist.a_revert_enter,
        a_revert_exit=dist.a_revert_exit,
        v_low_threshold=dist.v_low_threshold,
        t_rise_threshold=dist.t_rise_threshold,
        s_neut_max=dist.s_neut_max,
        a_neut_max=dist.a_neut_max,
        t_neut_max=dist.t_neut_max,
        v_neut_min=dist.v_neut_min,
        v_neut_max=dist.v_neut_max,
        t_exp_plus=dist.t_exp_plus,
        t_exp_plus_plus=dist.t_exp_plus_plus,
        t_comp_plus=dist.t_comp_plus,
        t_comp_plus_plus=dist.t_comp_plus_plus,
        a_cont_plus=dist.a_cont_plus,
        a_cont_plus_plus=dist.a_cont_plus_plus,
        a_revert_plus=dist.a_revert_plus,
        a_revert_plus_plus=dist.a_revert_plus_plus,
        s_exh_plus=dist.s_exh_plus,
        s_exh_plus_plus=dist.s_exh_plus_plus,
        p_confirm_threshold=dist.p_confirm_threshold,
        token_min_hold_bars_3m=dist.token_min_hold_bars_3m,
        token_min_hold_bars_15m=dist.token_min_hold_bars_15m,
        token_min_hold_bars_1h=dist.token_min_hold_bars_1h,
        token_min_hold_bars_4h=dist.token_min_hold_bars_4h,
        narrative_enabled=False,
        narrative_driver_tf=dist.narrative_driver_tf,
        narrative_linger_reminder_closes=dist.narrative_linger_reminder_closes,
        narrative_max_chars=dist.narrative_max_chars,
        narrative_secondary_min_ratio=dist.narrative_secondary_min_ratio,
        narrative_dir_ratio_min=dist.narrative_dir_ratio_min,
    )


def _iter_backfill_paths(data_dir: Path, *, symbol: str, month_prefix: str) -> list[Path]:
    pattern = f"binance_backfill-{symbol}USDT-perp-{month_prefix}*.jsonl.gz"
    return sorted(data_dir.glob(pattern))


def _iter_backfill_trades(paths: list[Path]):
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                ts = int(payload["timestamp"])
                price = float(payload["price"])
                yield ts, price


def _build_3m_bars(paths: list[Path]) -> list[OhlcBar]:
    bars: list[OhlcBar] = []
    bucket_ms = TF_MS["3m"]
    current_open_ms: int | None = None
    open_price = high_price = low_price = close_price = 0.0
    last_close: float | None = None

    def flush(open_ms: int, o_price: float, h_price: float, l_price: float, c_price: float) -> None:
        bars.append(
            OhlcBar(
                open_ms=open_ms,
                close_ms=open_ms + bucket_ms - 1,
                open=o_price,
                high=h_price,
                low=l_price,
                close=c_price,
            )
        )

    for ts, price in _iter_backfill_trades(paths):
        bucket_open_ms = (ts // bucket_ms) * bucket_ms
        if current_open_ms is None:
            current_open_ms = bucket_open_ms
            open_price = high_price = low_price = close_price = price
            continue
        if bucket_open_ms == current_open_ms:
            high_price = max(high_price, price)
            low_price = min(low_price, price)
            close_price = price
            continue

        flush(current_open_ms, open_price, high_price, low_price, close_price)
        last_close = close_price
        next_open_ms = current_open_ms + bucket_ms
        while last_close is not None and next_open_ms < bucket_open_ms:
            flush(next_open_ms, last_close, last_close, last_close, last_close)
            next_open_ms += bucket_ms
        current_open_ms = bucket_open_ms
        open_price = high_price = low_price = close_price = price

    if current_open_ms is not None:
        flush(current_open_ms, open_price, high_price, low_price, close_price)
    return bars


def _aggregate_bars(base_bars: list[OhlcBar], tf: DistTimeframe) -> list[OhlcBar]:
    interval_ms = TF_MS[tf]
    out: list[OhlcBar] = []
    cur_open_ms: int | None = None
    open_price = high_price = low_price = close_price = 0.0
    for bar in base_bars:
        bucket_open_ms = (bar.open_ms // interval_ms) * interval_ms
        if cur_open_ms is None:
            cur_open_ms = bucket_open_ms
            open_price = bar.open
            high_price = bar.high
            low_price = bar.low
            close_price = bar.close
            continue
        if bucket_open_ms == cur_open_ms:
            high_price = max(high_price, bar.high)
            low_price = min(low_price, bar.low)
            close_price = bar.close
            continue
        out.append(
            OhlcBar(
                open_ms=cur_open_ms,
                close_ms=cur_open_ms + interval_ms - 1,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
            )
        )
        cur_open_ms = bucket_open_ms
        open_price = bar.open
        high_price = bar.high
        low_price = bar.low
        close_price = bar.close
    if cur_open_ms is not None:
        out.append(
            OhlcBar(
                open_ms=cur_open_ms,
                close_ms=cur_open_ms + interval_ms - 1,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
            )
        )
    return out


def _replay_metric_rows(config_path: Path, bars_by_tf: dict[DistTimeframe, list[OhlcBar]]) -> dict[DistTimeframe, list[RowRecord]]:
    app = load_app_config(config_path)
    engine = DistStateEngine(_build_engine_config(app.dist_state))
    merged: list[tuple[int, DistTimeframe, OhlcBar]] = []
    for tf, bars in bars_by_tf.items():
        for bar in bars:
            merged.append((bar.close_ms, tf, bar))
    merged.sort(key=lambda item: (item[0], TF_ORDER[item[1]]))

    rows: dict[DistTimeframe, list[RowRecord]] = defaultdict(list)
    prev_close_by_tf: dict[DistTimeframe, float] = {}
    for _, tf, bar in merged:
        event = DistKlineCloseEvent(
            ts_recv_ms=bar.close_ms,
            symbol=app.dist_state.symbol,
            source_id=app.dist_state.source_id,
            tf=tf,
            kline_open_ms=bar.open_ms,
            kline_close_ms=bar.close_ms,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
        )
        snapshot, debug = engine.on_kline_close_with_diagnostics(event)
        if not bool(debug.get("processed")):
            continue
        row = snapshot.rows[tf]
        prev_close = prev_close_by_tf.get(tf)
        log_return = math.log(bar.close / prev_close) if prev_close and prev_close > 0 else 0.0
        true_range = bar.high - bar.low
        if prev_close is not None:
            true_range = max(true_range, abs(bar.high - prev_close), abs(bar.low - prev_close))
        rows[tf].append(
            RowRecord(
                tf=tf,
                index=len(rows[tf]),
                open_ms=bar.open_ms,
                close_ms=bar.close_ms,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                log_return=log_return,
                true_range=true_range,
                ready_core=row.ready_core,
                metrics_v=row.metrics.v,
                metrics_s=row.metrics.s,
                metrics_a=row.metrics.a,
                metrics_t=row.metrics.t,
            )
        )
        prev_close_by_tf[tf] = bar.close
    return rows


def _future_sum(records: list[RowRecord], idx: int, horizon: int) -> float | None:
    end = idx + 1 + horizon
    if end > len(records):
        return None
    return sum(record.log_return for record in records[idx + 1 : end])


def _future_tr_mean(records: list[RowRecord], idx: int, horizon: int) -> float | None:
    end = idx + 1 + horizon
    if end > len(records):
        return None
    return _mean([record.true_range for record in records[idx + 1 : end]])


def _past_tr_mean(records: list[RowRecord], idx: int, horizon: int) -> float | None:
    start = idx - horizon + 1
    if start < 0:
        return None
    return _mean([record.true_range for record in records[start : idx + 1]])


def _future_sigma(records: list[RowRecord], idx: int, horizon: int) -> float | None:
    end = idx + 1 + horizon
    if end > len(records):
        return None
    vals = [record.log_return for record in records[idx + 1 : end]]
    if not vals:
        return None
    return math.sqrt(sum(v * v for v in vals) / len(vals))


def _distribution_summary(records: list[RowRecord], value_getter) -> dict[str, float | int | None]:
    values = [value for record in records if record.ready_core for value in [value_getter(record)] if value is not None]
    if not values:
        return {"n": 0, "min": None, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "max": None}
    return {
        "n": len(values),
        "min": min(values),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "p50": _quantile(values, 0.50),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "max": max(values),
    }


def _study_a(records: list[RowRecord], horizons: tuple[int, ...]) -> dict[str, object]:
    eligible: dict[int, list[int]] = defaultdict(list)
    baseline_cont: dict[int, float | None] = {}
    baseline_revert: dict[int, float | None] = {}
    for horizon in horizons:
        cont_hits: list[float] = []
        revert_hits: list[float] = []
        for idx, record in enumerate(records):
            if not record.ready_core or record.metrics_a is None:
                continue
            cur_sign = _sign(record.log_return)
            future_sum = _future_sum(records, idx, horizon)
            fut_sign = _sign(future_sum or 0.0)
            if cur_sign == 0 or fut_sign == 0:
                continue
            eligible[horizon].append(idx)
            cont_hits.append(1.0 if fut_sign == cur_sign else 0.0)
            revert_hits.append(1.0 if fut_sign == -cur_sign else 0.0)
        baseline_cont[horizon] = _mean(cont_hits)
        baseline_revert[horizon] = _mean(revert_hits)

    positive_rows: list[dict[str, object]] = []
    negative_rows: list[dict[str, object]] = []
    for threshold in POS_GRIDS["a"]:
        pos_row: dict[str, object] = {"threshold": threshold}
        neg_row: dict[str, object] = {"threshold": -threshold}
        for horizon in horizons:
            pos_hits: list[float] = []
            neg_hits: list[float] = []
            pos_support = 0
            neg_support = 0
            for idx in eligible[horizon]:
                record = records[idx]
                a_value = record.metrics_a or 0.0
                cur_sign = _sign(record.log_return)
                fut_sign = _sign(_future_sum(records, idx, horizon) or 0.0)
                if a_value >= threshold:
                    pos_support += 1
                    pos_hits.append(1.0 if fut_sign == cur_sign else 0.0)
                if a_value <= -threshold:
                    neg_support += 1
                    neg_hits.append(1.0 if fut_sign == -cur_sign else 0.0)
            pos_rate = _mean(pos_hits)
            neg_rate = _mean(neg_hits)
            pos_row[f"h{horizon}_support"] = pos_support
            pos_row[f"h{horizon}_rate"] = pos_rate
            pos_row[f"h{horizon}_lift"] = _safe_ratio(pos_rate, baseline_cont[horizon])
            neg_row[f"h{horizon}_support"] = neg_support
            neg_row[f"h{horizon}_rate"] = neg_rate
            neg_row[f"h{horizon}_lift"] = _safe_ratio(neg_rate, baseline_revert[horizon])
        positive_rows.append(pos_row)
        negative_rows.append(neg_row)
    return {
        "baseline_continuation": baseline_cont,
        "baseline_reversion": baseline_revert,
        "positive_thresholds": positive_rows,
        "negative_thresholds": negative_rows,
    }


def _study_t(records: list[RowRecord], horizons: tuple[int, ...]) -> dict[str, object]:
    baseline_exp: dict[int, float | None] = {}
    baseline_comp: dict[int, float | None] = {}
    future_sigma_cut_low: dict[int, float | None] = {}
    future_sigma_cut_high: dict[int, float | None] = {}
    eligible_by_horizon: dict[int, list[int]] = defaultdict(list)
    for horizon in horizons:
        ratios: list[float] = []
        future_sigmas: list[float] = []
        for idx, record in enumerate(records):
            if not record.ready_core or record.metrics_t is None:
                continue
            fut = _future_tr_mean(records, idx, horizon)
            past = _past_tr_mean(records, idx, horizon)
            if fut is None or past is None or past <= 0:
                continue
            eligible_by_horizon[horizon].append(idx)
            ratios.append(fut / past)
            sigma = _future_sigma(records, idx, horizon)
            if sigma is not None:
                future_sigmas.append(sigma)
        baseline_exp[horizon] = _mean([1.0 if ratio >= 1.10 else 0.0 for ratio in ratios])
        baseline_comp[horizon] = _mean([1.0 if ratio <= 0.90 else 0.0 for ratio in ratios])
        future_sigma_cut_low[horizon] = _quantile(future_sigmas, 0.40)
        future_sigma_cut_high[horizon] = _quantile(future_sigmas, 0.60)

    positive_rows: list[dict[str, object]] = []
    negative_rows: list[dict[str, object]] = []
    for threshold in POS_GRIDS["t"]:
        pos_row: dict[str, object] = {"threshold": threshold}
        neg_row: dict[str, object] = {"threshold": -threshold}
        for horizon in horizons:
            pos_hits: list[float] = []
            neg_hits: list[float] = []
            pos_ratios: list[float] = []
            neg_ratios: list[float] = []
            pos_support = 0
            neg_support = 0
            for idx in eligible_by_horizon[horizon]:
                record = records[idx]
                t_value = record.metrics_t or 0.0
                fut = _future_tr_mean(records, idx, horizon)
                past = _past_tr_mean(records, idx, horizon)
                if fut is None or past is None or past <= 0:
                    continue
                ratio = fut / past
                if t_value >= threshold:
                    pos_support += 1
                    pos_ratios.append(ratio)
                    pos_hits.append(1.0 if ratio >= 1.10 else 0.0)
                if t_value <= -threshold:
                    neg_support += 1
                    neg_ratios.append(ratio)
                    neg_hits.append(1.0 if ratio <= 0.90 else 0.0)
            pos_rate = _mean(pos_hits)
            neg_rate = _mean(neg_hits)
            pos_row[f"h{horizon}_support"] = pos_support
            pos_row[f"h{horizon}_expansion_rate"] = pos_rate
            pos_row[f"h{horizon}_expansion_lift"] = _safe_ratio(pos_rate, baseline_exp[horizon])
            pos_row[f"h{horizon}_median_tr_ratio"] = median(pos_ratios) if pos_ratios else None
            neg_row[f"h{horizon}_support"] = neg_support
            neg_row[f"h{horizon}_compression_rate"] = neg_rate
            neg_row[f"h{horizon}_compression_lift"] = _safe_ratio(neg_rate, baseline_comp[horizon])
            neg_row[f"h{horizon}_median_tr_ratio"] = median(neg_ratios) if neg_ratios else None
        positive_rows.append(pos_row)
        negative_rows.append(neg_row)
    return {
        "baseline_expansion": baseline_exp,
        "baseline_compression": baseline_comp,
        "positive_thresholds": positive_rows,
        "negative_thresholds": negative_rows,
    }


def _study_s(records: list[RowRecord], horizons: tuple[int, ...]) -> dict[str, object]:
    baseline_reversion: dict[int, float | None] = {}
    eligible_by_horizon: dict[int, list[int]] = defaultdict(list)
    for horizon in horizons:
        hits: list[float] = []
        for idx, record in enumerate(records):
            if not record.ready_core or record.metrics_s is None:
                continue
            future_sum = _future_sum(records, idx, horizon)
            if future_sum is None:
                continue
            s_sign = _sign(record.metrics_s)
            if s_sign == 0:
                continue
            eligible_by_horizon[horizon].append(idx)
            hits.append(1.0 if _sign(future_sum) == -s_sign else 0.0)
        baseline_reversion[horizon] = _mean(hits)

    rows: list[dict[str, object]] = []
    for threshold in POS_GRIDS["s"]:
        row: dict[str, object] = {"abs_threshold": threshold}
        for horizon in horizons:
            support = 0
            rates: list[float] = []
            signed_moves: list[float] = []
            for idx in eligible_by_horizon[horizon]:
                record = records[idx]
                s_value = record.metrics_s or 0.0
                if abs(s_value) < threshold:
                    continue
                future_sum = _future_sum(records, idx, horizon)
                if future_sum is None:
                    continue
                support += 1
                signed_reversion = (-_sign(s_value)) * future_sum
                signed_moves.append(signed_reversion)
                rates.append(1.0 if signed_reversion > 0 else 0.0)
            rate = _mean(rates)
            row[f"h{horizon}_support"] = support
            row[f"h{horizon}_reversion_rate"] = rate
            row[f"h{horizon}_reversion_lift"] = _safe_ratio(rate, baseline_reversion[horizon])
            row[f"h{horizon}_median_signed_reversion"] = median(signed_moves) if signed_moves else None
        rows.append(row)
    return {
        "baseline_reversion": baseline_reversion,
        "abs_thresholds": rows,
    }


def _study_v(records: list[RowRecord], horizons: tuple[int, ...]) -> dict[str, object]:
    low_cut: dict[int, float | None] = {}
    high_cut: dict[int, float | None] = {}
    eligible_by_horizon: dict[int, list[int]] = defaultdict(list)
    baseline_low: dict[int, float | None] = {}
    for horizon in horizons:
        sigmas: list[float] = []
        for idx, record in enumerate(records):
            if not record.ready_core or record.metrics_v is None:
                continue
            sigma = _future_sigma(records, idx, horizon)
            if sigma is None:
                continue
            eligible_by_horizon[horizon].append(idx)
            sigmas.append(sigma)
        low_cut[horizon] = _quantile(sigmas, 0.35)
        high_cut[horizon] = _quantile(sigmas, 0.65)
        cut = low_cut[horizon]
        baseline_low[horizon] = _mean([1.0 if cut is not None and sigma <= cut else 0.0 for sigma in sigmas])

    rows: list[dict[str, object]] = []
    for threshold in POS_GRIDS["v"]:
        row: dict[str, object] = {"threshold": threshold}
        for horizon in horizons:
            support = 0
            hits: list[float] = []
            sigmas: list[float] = []
            cut = low_cut[horizon]
            for idx in eligible_by_horizon[horizon]:
                record = records[idx]
                v_value = record.metrics_v or 0.0
                sigma = _future_sigma(records, idx, horizon)
                if sigma is None or cut is None:
                    continue
                if v_value <= threshold:
                    support += 1
                    sigmas.append(sigma)
                    hits.append(1.0 if sigma <= cut else 0.0)
            rate = _mean(hits)
            row[f"h{horizon}_support"] = support
            row[f"h{horizon}_low_vol_rate"] = rate
            row[f"h{horizon}_low_vol_lift"] = _safe_ratio(rate, baseline_low[horizon])
            row[f"h{horizon}_median_future_sigma"] = median(sigmas) if sigmas else None
        rows.append(row)
    return {
        "future_sigma_low_cut": low_cut,
        "future_sigma_high_cut": high_cut,
        "baseline_low_vol": baseline_low,
        "thresholds": rows,
    }


def _build_summary(
    *,
    config_path: Path,
    backfill_paths: list[Path],
    bars_by_tf: dict[DistTimeframe, list[OhlcBar]],
    rows_by_tf: dict[DistTimeframe, list[RowRecord]],
    studies: dict[DistTimeframe, dict[str, object]],
) -> str:
    lines: list[str] = []
    lines.append("== dist-state metric calibration ==")
    lines.append(f"config={config_path}")
    lines.append(f"backfill_files={len(backfill_paths)}")
    for path in backfill_paths[:3]:
        lines.append(f"- sample_file={path}")
    if len(backfill_paths) > 3:
        lines.append(f"- ... ({len(backfill_paths) - 3} more)")
    lines.append("")
    for tf in ("3m", "15m", "1h", "4h"):
        typed_tf = tf  # keep output ordering simple
        tf_rows = rows_by_tf[typed_tf]  # type: ignore[index]
        lines.append(f"[{tf}] bars={len(bars_by_tf[typed_tf])} rows={len(tf_rows)} ready_core={sum(1 for r in tf_rows if r.ready_core)}")  # type: ignore[index]
        for metric_key, getter in (
            ("V", lambda record: record.metrics_v),
            ("S", lambda record: record.metrics_s),
            ("A", lambda record: record.metrics_a),
            ("T", lambda record: record.metrics_t),
        ):
            summary = _distribution_summary(tf_rows, getter)
            lines.append(
                f"- {metric_key}: n={summary['n']} min={summary['min']} p10={summary['p10']} "
                f"p25={summary['p25']} p50={summary['p50']} p75={summary['p75']} "
                f"p90={summary['p90']} max={summary['max']}"
            )
        study = studies[typed_tf]  # type: ignore[index]
        a_first = study["A"]["positive_thresholds"][:4]  # type: ignore[index]
        t_first = study["T"]["positive_thresholds"][:4]  # type: ignore[index]
        s_first = study["S"]["abs_thresholds"][:4]  # type: ignore[index]
        v_first = study["V"]["thresholds"][:4]  # type: ignore[index]
        lines.append("- A positive candidates:")
        for row in a_first:
            lines.append(f"  {row}")
        lines.append("- T positive candidates:")
        for row in t_first:
            lines.append(f"  {row}")
        lines.append("- S abs candidates:")
        for row in s_first:
            lines.append(f"  {row}")
        lines.append("- V low candidates:")
        for row in v_first:
            lines.append(f"  {row}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate dist-state V/S/A/T metrics from January BTC perp trade backfill.")
    parser.add_argument("--config", default="config/app_btc.toml", help="Config file used for dist-state math.")
    parser.add_argument("--data-dir", default="logs/backfill", help="Directory containing Binance backfill JSONL.gz files.")
    parser.add_argument("--symbol", default="BTC", help="Base symbol to calibrate (default: BTC).")
    parser.add_argument("--month-prefix", default="202601", help="Backfill month prefix in YYYYMM (default: 202601).")
    parser.add_argument(
        "--out-dir",
        default="docs/diagnostics",
        help="Output directory for calibration artifacts (default: docs/diagnostics).",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    backfill_paths = _iter_backfill_paths(data_dir, symbol=args.symbol.upper(), month_prefix=args.month_prefix)
    if not backfill_paths:
        raise SystemExit(f"No backfill files found for {args.symbol} {args.month_prefix} in {data_dir}")

    bars_3m = _build_3m_bars(backfill_paths)
    bars_by_tf: dict[DistTimeframe, list[OhlcBar]] = {
        "3m": bars_3m,
        "15m": _aggregate_bars(bars_3m, "15m"),
        "1h": _aggregate_bars(bars_3m, "1h"),
        "4h": _aggregate_bars(bars_3m, "4h"),
    }
    rows_by_tf = _replay_metric_rows(config_path, bars_by_tf)

    studies: dict[DistTimeframe, dict[str, object]] = {}
    for tf, rows in rows_by_tf.items():
        horizons = DEFAULT_HORIZONS[tf]
        studies[tf] = {
            "A": _study_a(rows, horizons),
            "T": _study_t(rows, horizons),
            "S": _study_s(rows, horizons),
            "V": _study_v(rows, horizons),
        }

    stem = f"dist_state_metric_calibration_{args.symbol.lower()}_{args.month_prefix}"
    json_path = out_dir / f"{stem}.json"
    txt_path = out_dir / f"{stem}.txt"
    payload = {
        "config_path": str(config_path),
        "data_dir": str(data_dir),
        "symbol": args.symbol.upper(),
        "month_prefix": args.month_prefix,
        "backfill_files": [str(path) for path in backfill_paths],
        "bar_counts": {tf: len(bars) for tf, bars in bars_by_tf.items()},
        "row_counts": {tf: len(rows) for tf, rows in rows_by_tf.items()},
        "studies": studies,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(
        _build_summary(
            config_path=config_path,
            backfill_paths=backfill_paths,
            bars_by_tf=bars_by_tf,
            rows_by_tf=rows_by_tf,
            studies=studies,
        ),
        encoding="utf-8",
    )
    print(f"Wrote calibration JSON to {json_path}")
    print(f"Wrote calibration summary to {txt_path}")


if __name__ == "__main__":
    main()
