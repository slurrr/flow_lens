from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Iterable

from flow_lens.config import load_app_config
from flow_lens.dist_state.engine import DistStateConfig, DistStateEngine
from flow_lens.dist_state.models import DistKlineCloseEvent, DistOiSamplerSnapshot, DistTimeframe
from flow_lens.main import DistStateDiagnosticLogger


def _open_jsonl(path: Path) -> Iterable[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield from handle
    else:
        with path.open("r", encoding="utf-8") as handle:
            yield from handle


def _as_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _as_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _build_engine_config(config_path: Path) -> DistStateConfig:
    app = load_app_config(config_path)
    dist = app.dist_state
    return DistStateConfig(
        enabled=dist.enabled,
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
        tokens_enabled=dist.tokens_enabled,
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
    )


def _parse_timeframe(value: Any) -> DistTimeframe | None:
    if value not in {"3m", "15m", "1h", "4h"}:
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay dist-state close events without running the full lens replay pipeline."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to dist_state_diagnostics-*.jsonl(.gz) file captured from live runs.",
    )
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="Config file used for replay run (default: config/app.toml).",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/replay_dist",
        help="Output directory for dist replay diagnostics (default: logs/replay_dist).",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    engine_config = _build_engine_config(config_path)
    if not engine_config.enabled:
        raise SystemExit("runtime.dist_state.enabled=false in selected config; enable it for dist replay.")

    engine = DistStateEngine(engine_config)
    engine.warmup()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "dist_state_replay.jsonl"
    logger = DistStateDiagnosticLogger(
        path=out_path,
        config={
            "replay_source": str(Path(args.input)),
            "config_path": str(config_path),
            "dist_state_symbol": engine_config.symbol,
            "dist_state_source_id": engine_config.source_id,
            "dist_state_timeframes": list(engine_config.timeframes),
        },
    )

    processed = 0
    skipped = 0
    for raw_line in _open_jsonl(Path(args.input)):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(payload, dict):
            skipped += 1
            continue
        if payload.get("event_type") != "dist_state_close":
            continue
        tf = _parse_timeframe(payload.get("tf"))
        if tf is None:
            skipped += 1
            continue
        kline_open_ms = _as_int(payload, "event_kline_open_ms")
        kline_close_ms = _as_int(payload, "kline_close_ms")
        open_price = _as_float(payload, "event_open")
        high_price = _as_float(payload, "event_high")
        low_price = _as_float(payload, "event_low")
        close_price = _as_float(payload, "event_close")
        if (
            kline_open_ms is None
            or kline_close_ms is None
            or open_price is None
            or high_price is None
            or low_price is None
            or close_price is None
        ):
            skipped += 1
            continue

        oi_snapshot: DistOiSamplerSnapshot | None = None
        oi_value = _as_float(payload, "oi_sample_oi")
        oi_recv_ms = _as_int(payload, "oi_sample_recv_ms")
        oi_seq = _as_int(payload, "oi_sample_seq")
        if oi_value is not None:
            oi_snapshot = DistOiSamplerSnapshot(
                oi=oi_value,
                venue_time_ms=_as_int(payload, "oi_sample_venue_time_ms"),
                ts_recv_ms=oi_recv_ms if oi_recv_ms is not None else kline_close_ms,
                sample_seq=oi_seq if oi_seq is not None else processed + 1,
            )

        event = DistKlineCloseEvent(
            ts_recv_ms=_as_int(payload, "event_ts_recv_ms") or kline_close_ms,
            symbol=str(payload.get("symbol", engine_config.symbol)).upper(),
            source_id=str(payload.get("source_id", engine_config.source_id)),
            tf=tf,
            kline_open_ms=kline_open_ms,
            kline_close_ms=kline_close_ms,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            sampler_snapshot=oi_snapshot,
            verify_snapshot=None,
        )
        _, debug = engine.on_kline_close_with_diagnostics(event)
        logger.log_close(event, debug)
        processed += 1

    print(
        f"dist replay complete: processed={processed} skipped={skipped} "
        f"input={args.input} output_prefix={out_path}"
    )


if __name__ == "__main__":
    main()
