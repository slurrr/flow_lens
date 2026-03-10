from __future__ import annotations

import argparse
import asyncio
import curses
import faulthandler
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from flow_lens.adapters import (
    AdapterEvent,
    AdapterStats,
    AdapterStatus,
    BaseAdapter,
    BinancePerpWSAdapter,
    BinanceSpotWSAdapter,
    BybitPerpWSAdapter,
    BybitSpotWSAdapter,
    CoinbaseSpotWSAdapter,
)
from flow_lens.config import AppConfig, load_app_config
from flow_lens.dist_state.engine import DistStateConfig, DistStateEngine
from flow_lens.dist_state.feed import BinancePerpDistFeed, DistFeedConfig
from flow_lens.dist_state.models import DistKlineCloseEvent, DistPanelSnapshot
from flow_lens.engine.buffer import (
    PriceSourceMeta,
    PriceSwitchEvent,
    PriorityStickySelector,
    RollingEventBuffer,
)
from flow_lens.engine.constants import (
    Binning,
    ControlBaseline,
    Defaults,
    DispScaleConfig,
    EffectivenessDeadband,
    EffectivenessScaling,
    EffortFloor,
    EffortScaleConfig,
    HaloDynamics,
    InputNormalization,
    Persistence,
    SizeScaleConfig,
    Smoothing,
    TimeDomain,
)
from flow_lens.engine.control_baseline import DynamicControlBaseline
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.ingest.hygiene import HygieneConfig, HygieneIngestor, HygieneMetricsEvent
from flow_lens.models.event import Event
from flow_lens.symbols import (
    BinanceSymbolResolver,
    QuotePair,
    SymbolMaps,
    SymbolResolution,
    build_symbol_maps,
    log_resolution,
)
from flow_lens.tui.input import InputState
from flow_lens.tui.metrics import LiveMetrics
from flow_lens.tui.renderer import Renderer, RendererConfig


@dataclass
class RuntimeState:
    loops: dict[str, EngineLoop]
    last_state: dict[str, StateSnapshot | None]
    pending: dict[str, list[Event]]
    symbol_maps: SymbolMaps
    last_event_ms: dict[str, int | None]
    source_meta: dict[str, PriceSourceMeta]
    source_allowlists: dict[str, set[str] | None]
    filter_reset_events: list["FilterContextResetEvent"]
    coinbase_base_to_actual: dict[str, list[str]]
    bybit_spot_base_to_actual: dict[str, list[str]]
    bybit_perp_base_to_actual: dict[str, list[str]]
    hygiene: HygieneIngestor


@dataclass(frozen=True)
class FilterContextResetEvent:
    symbol: str
    old_mask: tuple[str, ...]
    new_mask: tuple[str, ...]
    reset_applied: tuple[str, ...]


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Flow Lens TUI")
    parser.add_argument(
        "--dia",
        action="store_true",
        help="Enable diagnostics logging to JSONL.",
    )
    parser.add_argument(
        "--dist-dia",
        action="store_true",
        help="Enable dist-state diagnostics logging to JSONL.",
    )
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="Path to app config (default: config/app.toml).",
    )
    parser.add_argument(
        "--fault",
        action="store_true",
        help="Enable faulthandler dumps for fatal crashes.",
    )
    args = parser.parse_args()
    if args.fault:
        _enable_fault_handler()
    curses.wrapper(
        partial(
            _run,
            diagnostics_enabled=args.dia,
            dist_diagnostics_enabled=args.dist_dia,
            config_path=args.config,
        )
    )


def _run(
    stdscr: "curses.window",
    *,
    diagnostics_enabled: bool,
    dist_diagnostics_enabled: bool,
    config_path: str,
) -> None:
    stdscr.nodelay(True)
    stdscr.keypad(True)
    curses.curs_set(0)

    config = load_app_config(config_path)
    update_interval_s = config.update_window_seconds
    window_ms = int(config.update_window_seconds * 1000)
    defaults = Defaults(
        time_domain=TimeDomain(update_window_seconds=config.update_window_seconds),
        effort_floor=EffortFloor(
            rolling_window_ticks=config.effort_floor_ticks,
            multiplier_alpha=config.effort_floor_multiplier,
        ),
        dispersion_metric=config.dispersion_metric,
        smoothing=Smoothing(
            dominance_alpha=config.smoothing_dominance_alpha,
            effectiveness_alpha=config.smoothing_effectiveness_alpha,
        ),
        effectiveness_scaling=EffectivenessScaling(tanh_k=config.tanh_k),
        input_normalization=InputNormalization(
            scale_window_seconds=config.scale_window_seconds,
        ),
        persistence=Persistence(
            enabled=config.persist_enabled,
            input_source=config.persist_input,
            input_deadband=config.persist_input_deadband,
            neutral_dir_abs_flash=config.persist_neutral_dir_abs_flash,
            neutral_dir_abs_persist=config.persist_neutral_dir_abs_persist,
            tau_eff_active=config.persist_tau_eff_active,
            tau_dir_active=config.persist_tau_dir_active,
            pivot_active_abs=config.persist_pivot_active_abs,
            pivot_confirm_s=config.persist_pivot_confirm_s,
            pivot_neutralize_tau=config.persist_pivot_neutralize_tau,
            pivot_neutral_zone_abs=config.persist_pivot_neutral_zone_abs,
            rebuild_confirm_s=config.persist_rebuild_confirm_s,
            pivot_cooldown_s=config.persist_pivot_cooldown_s,
            pivot_max_s=config.persist_pivot_max_s,
            max_delta_s_eff_per_second=config.persist_max_delta_s_eff_per_second,
            tau_dir_pivot=config.persist_tau_dir_pivot,
            dormant_quiet_abs=config.persist_dormant_quiet_abs,
            dormant_active_abs=config.persist_dormant_active_abs,
            dormant_quiet_s=config.persist_dormant_quiet_s,
            tau_dormant=config.persist_tau_dormant,
            dormant_effort_norm_threshold=config.persist_dormant_effort_norm_threshold,
        ),
        effectiveness_deadband=EffectivenessDeadband(
            disp_scale_multiplier=config.disp_scale_multiplier,
        ),
        disp_scale=DispScaleConfig(
            percentile=config.disp_scale_percentile,
            min_samples=config.disp_scale_min_samples,
            floor_percentile=config.disp_scale_floor_percentile,
        ),
        effort_scale=EffortScaleConfig(
            percentile=config.effort_scale_percentile,
            min_samples=config.effort_scale_min_samples,
        ),
        size_scale=SizeScaleConfig(
            percentile=config.size_scale_percentile,
        ),
        halo_dynamics=HaloDynamics(
            growth_rate=config.halo_growth_rate,
            decay_rate=config.halo_decay_rate,
        ),
        binning=Binning(
            dot_size_thresholds=config.binning_dot_size_thresholds,
            halo_thresholds=config.binning_halo_thresholds,
            hysteresis_band=config.binning_hysteresis_band,
        ),
        control_baseline=ControlBaseline(
            enabled=config.control_baseline_enabled,
            target_window_s=config.control_baseline_target_window_s,
            target_update_s=config.control_baseline_target_update_s,
            breakout_band=config.control_baseline_breakout_band,
            confirm_s=config.control_baseline_confirm_s,
            exit_band_frac=config.control_baseline_exit_band_frac,
            peg_half_life_s=config.control_baseline_peg_half_life_s,
            reanchor_half_life_s=config.control_baseline_reanchor_half_life_s,
            peg_deadband=config.control_baseline_peg_deadband,
            max_window_samples=config.control_baseline_max_window_samples,
            center_suppress_band=config.control_baseline_center_suppress_band,
            line_hide_warmup_s=config.control_baseline_line_hide_warmup_s,
            midnight_tick_enabled=config.control_baseline_midnight_tick_enabled,
            midnight_tick_min_samples=config.control_baseline_midnight_tick_min_samples,
            midnight_tick_min_elapsed_s=config.control_baseline_midnight_tick_min_elapsed_s,
        ),
    )
    logging.info("Runtime config: %s", _runtime_config_map(config))
    _log_source_registry(config)
    base_symbols = _collect_symbols(config)

    binance_spot_enabled = "binance_spot" in config.adapters
    binance_perp_enabled = "binance_perp" in config.adapters
    resolver = BinanceSymbolResolver()
    if binance_spot_enabled:
        spot_resolution = resolver.resolve_spot(config.adapters["binance_spot"].symbols)
        log_resolution("Spot", spot_resolution)
    else:
        spot_resolution = _empty_resolution()
    if binance_perp_enabled:
        perp_resolution = resolver.resolve_perp(config.adapters["binance_perp"].symbols)
        log_resolution("Perp", perp_resolution)
    else:
        perp_resolution = _empty_resolution()

    symbol_maps = build_symbol_maps(spot_resolution, perp_resolution)
    source_meta = _build_price_source_meta(config)
    queue_events: queue.Queue[AdapterEvent] = queue.Queue()
    supervisor = AdapterSupervisor(queue_events)
    coinbase_product_map = _coinbase_product_map(
        config.adapters["coinbase_spot"].symbols
        if "coinbase_spot" in config.adapters
        else []
    )
    bybit_spot_actuals, bybit_spot_symbol_to_base, bybit_spot_base_to_actual = (
        _bybit_symbol_maps(
            config.adapters["bybit_spot"].symbols
            if "bybit_spot" in config.adapters
            else []
        )
    )
    bybit_perp_actuals, bybit_perp_symbol_to_base, bybit_perp_base_to_actual = (
        _bybit_symbol_maps(
            config.adapters["bybit_perp"].symbols
            if "bybit_perp" in config.adapters
            else []
        )
    )
    supervisor.start(
        binance_spot_enabled=binance_spot_enabled,
        binance_perp_enabled=binance_perp_enabled,
        spot_symbols=_flatten(symbol_maps.spot_base_to_actual),
        spot_symbol_to_base=symbol_maps.spot_actual_to_base,
        spot_quotes=symbol_maps.spot_actual_to_quote,
        quote_pairs=symbol_maps.quote_pairs,
        quote_rates=symbol_maps.quote_rates,
        perp_symbols=_flatten(symbol_maps.perp_base_to_actual),
        perp_symbol_to_base=symbol_maps.perp_actual_to_base,
        coinbase_symbols=sorted(coinbase_product_map.values()),
        bybit_spot_symbols=bybit_spot_actuals,
        bybit_spot_symbol_to_base=bybit_spot_symbol_to_base,
        bybit_perp_symbols=bybit_perp_actuals,
        bybit_perp_symbol_to_base=bybit_perp_symbol_to_base,
    )

    runtime = _init_runtime(
        base_symbols,
        window_ms,
        symbol_maps,
        source_meta,
        defaults,
        selector_stale_failover_ms=config.price_selector_stale_failover_ms,
        selector_recovery_confirm_cycles=config.price_selector_recovery_confirm_cycles,
        selector_switch_cooldown_cycles=config.price_selector_switch_cooldown_cycles,
        hygiene_config=HygieneConfig(
            enabled=config.hygiene_enabled,
            max_excess_wire_lag_ms=config.hygiene_max_excess_wire_lag_ms,
            hard_max_wire_lag_ms=config.hygiene_hard_max_wire_lag_ms,
            wire_lag_baseline_window_s=config.hygiene_wire_lag_baseline_window_s,
            wire_lag_baseline_sample_interval_ms=(
                config.hygiene_wire_lag_baseline_sample_interval_ms
            ),
            wire_lag_baseline_min_samples=config.hygiene_wire_lag_baseline_min_samples,
            wire_lag_baseline_max_samples=config.hygiene_wire_lag_baseline_max_samples,
            dedupe_ttl_s=config.hygiene_dedupe_ttl_s,
            log_interval_s=config.hygiene_log_interval_s,
            future_venue_ts_grace_ms=config.hygiene_future_venue_ts_grace_ms,
            connect_gate_s=config.hygiene_connect_gate_s,
            connect_gate_max_excess_wire_lag_ms=config.hygiene_connect_gate_max_excess_wire_lag_ms,
            connect_gate_hard_max_wire_lag_ms=config.hygiene_connect_gate_hard_max_wire_lag_ms,
            connect_gate_rearm_after_s=config.hygiene_connect_gate_rearm_after_s,
        ),
        coinbase_base_to_actual=_coinbase_base_to_actual(coinbase_product_map),
        bybit_spot_base_to_actual=bybit_spot_base_to_actual,
        bybit_perp_base_to_actual=bybit_perp_base_to_actual,
    )
    input_state = InputState(symbols=base_symbols)
    renderer = Renderer(
        RendererConfig(
            min_width=config.tui_min_width,
            min_height=config.tui_min_height,
            max_width=config.tui_max_width,
            max_height=config.tui_max_height,
            dot_radii=config.tui_dot_radii,
            halo_radii=config.tui_halo_radii,
            frame_enabled=config.tui_frame_enabled,
            frame_inset_px=config.tui_frame_inset_px,
            frame_band_inner=config.tui_frame_band_inner,
            frame_band_outer=config.tui_frame_band_outer,
            show_dev_panel=config.tui_show_dev_panel,
            dist_narrative_max_chars=config.dist_state.narrative_max_chars,
            control_baseline_center_suppress_band=config.control_baseline_center_suppress_band,
            axis_flash_duration_s=config.update_window_seconds,
            axis_flash_cooldown_s=config.update_window_seconds,
        )
    )
    live_metrics = LiveMetrics()
    dist_engine: DistStateEngine | None = None
    dist_feed: BinancePerpDistFeed | None = None
    dist_snapshot: DistPanelSnapshot | None = None
    if config.dist_state.enabled:
        try:
            dist_engine = DistStateEngine(
                DistStateConfig(
                    enabled=config.dist_state.enabled,
                    symbol=config.dist_state.symbol,
                    source_id=config.dist_state.source_id,
                    timeframes=config.dist_state.timeframes,
                    warmup_kline_bars=config.dist_state.warmup_kline_bars,
                    warmup_oi_hist_points=config.dist_state.warmup_oi_hist_points,
                    ready_core_min_bars=config.dist_state.ready_core_min_bars,
                    ready_p_min_deltas=config.dist_state.ready_p_min_deltas,
                    p_availability_mode=config.dist_state.p_availability_mode,
                    oi_tolerance_ms=config.dist_state.oi_tolerance_ms,
                    oi_time_missing_policy=config.dist_state.oi_time_missing_policy,
                    oi_seed_points=config.dist_state.oi_seed_points,
                    oi_seed_min_points=config.dist_state.oi_seed_min_points,
                    v_scale_window_bars=config.dist_state.v_scale_window_bars,
                    v_scale_percentile=config.dist_state.v_scale_percentile,
                    v_scale_min_samples=config.dist_state.v_scale_min_samples,
                    hl_vol_bars=config.dist_state.hl_vol_bars,
                    hl_stretch_bars=config.dist_state.hl_stretch_bars,
                    hl_oi_bars=config.dist_state.hl_oi_bars,
                    hl_atr_short_bars=config.dist_state.hl_atr_short_bars,
                    hl_atr_long_bars=config.dist_state.hl_atr_long_bars,
                    hl_a_bars=config.dist_state.hl_a_bars,
                    k_s=config.dist_state.k_s,
                    k_p=config.dist_state.k_p,
                    k_t=config.dist_state.k_t,
                    tokens_enabled=config.dist_state.tokens_enabled,
                    tokens_fail_fast_unknown=config.dist_state.tokens_fail_fast_unknown,
                    s_dir_deadband=config.dist_state.s_dir_deadband,
                    s_ext_enter=config.dist_state.s_ext_enter,
                    s_ext_exit=config.dist_state.s_ext_exit,
                    s_revert_min_stretch=config.dist_state.s_revert_min_stretch,
                    t_exp_enter=config.dist_state.t_exp_enter,
                    t_exp_exit=config.dist_state.t_exp_exit,
                    t_comp_enter=config.dist_state.t_comp_enter,
                    t_comp_exit=config.dist_state.t_comp_exit,
                    a_cont_enter=config.dist_state.a_cont_enter,
                    a_cont_exit=config.dist_state.a_cont_exit,
                    a_revert_enter=config.dist_state.a_revert_enter,
                    a_revert_exit=config.dist_state.a_revert_exit,
                    v_low_threshold=config.dist_state.v_low_threshold,
                    t_rise_threshold=config.dist_state.t_rise_threshold,
                    s_neut_max=config.dist_state.s_neut_max,
                    a_neut_max=config.dist_state.a_neut_max,
                    t_neut_max=config.dist_state.t_neut_max,
                    v_neut_min=config.dist_state.v_neut_min,
                    v_neut_max=config.dist_state.v_neut_max,
                    t_exp_plus=config.dist_state.t_exp_plus,
                    t_exp_plus_plus=config.dist_state.t_exp_plus_plus,
                    t_comp_plus=config.dist_state.t_comp_plus,
                    t_comp_plus_plus=config.dist_state.t_comp_plus_plus,
                    a_cont_plus=config.dist_state.a_cont_plus,
                    a_cont_plus_plus=config.dist_state.a_cont_plus_plus,
                    a_revert_plus=config.dist_state.a_revert_plus,
                    a_revert_plus_plus=config.dist_state.a_revert_plus_plus,
                    s_exh_plus=config.dist_state.s_exh_plus,
                    s_exh_plus_plus=config.dist_state.s_exh_plus_plus,
                    p_confirm_threshold=config.dist_state.p_confirm_threshold,
                    token_min_hold_bars_3m=config.dist_state.token_min_hold_bars_3m,
                    token_min_hold_bars_15m=config.dist_state.token_min_hold_bars_15m,
                    token_min_hold_bars_1h=config.dist_state.token_min_hold_bars_1h,
                    token_min_hold_bars_4h=config.dist_state.token_min_hold_bars_4h,
                    narrative_enabled=config.dist_state.narrative_enabled,
                    narrative_driver_tf=config.dist_state.narrative_driver_tf,
                    narrative_linger_reminder_closes=(
                        config.dist_state.narrative_linger_reminder_closes
                    ),
                    narrative_max_chars=config.dist_state.narrative_max_chars,
                    narrative_secondary_min_ratio=config.dist_state.narrative_secondary_min_ratio,
                    narrative_dir_ratio_min=config.dist_state.narrative_dir_ratio_min,
                )
            )
            dist_engine.warmup()
            dist_snapshot = dist_engine.snapshot()
            dist_feed = BinancePerpDistFeed(
                DistFeedConfig(
                    symbol=config.dist_state.symbol,
                    source_id=config.dist_state.source_id,
                    timeframes=config.dist_state.timeframes,
                    oi_poll_interval_ms=config.dist_state.oi_poll_interval_ms,
                    oi_verify_enabled=config.dist_state.oi_verify_enabled,
                    oi_verify_timeframes=config.dist_state.oi_verify_timeframes,
                    oi_verify_timeout_ms=config.dist_state.oi_verify_timeout_ms,
                    oi_verify_max_rate_per_min=config.dist_state.oi_verify_max_rate_per_min,
                )
            )
            dist_feed.start()
        except Exception:
            logging.exception("Failed to initialize dist-state layer; disabling panel.")
            dist_engine = None
            dist_feed = None
            dist_snapshot = None
    diagnostics: DiagnosticLogger | None = None
    if diagnostics_enabled:
        diagnostics = DiagnosticLogger(
            path=Path("logs/flow_lens_diagnostics.jsonl"),
            symbols={"ASTER", "XPL", "SHIB", "BTC", "ETH", "SOL"},
            tanh_k=config.tanh_k,
            config=_runtime_config_map(config),
        )
    dist_diagnostics: DistStateDiagnosticLogger | None = None
    if dist_diagnostics_enabled:
        if config.dist_state.enabled:
            dist_diagnostics = DistStateDiagnosticLogger(
                path=Path("docs/diagnostics/dist_state_diagnostics.jsonl"),
                config={
                    "dist_state_enabled": config.dist_state.enabled,
                    "dist_state_symbol": config.dist_state.symbol,
                    "dist_state_source_id": config.dist_state.source_id,
                    "dist_state_timeframes": list(config.dist_state.timeframes),
                    "dist_state_p_availability_mode": config.dist_state.p_availability_mode,
                    "dist_state_oi_poll_interval_ms": config.dist_state.oi_poll_interval_ms,
                    "dist_state_oi_tolerance_ms": config.dist_state.oi_tolerance_ms,
                    "dist_state_oi_time_missing_policy": config.dist_state.oi_time_missing_policy,
                    "dist_state_oi_verify_enabled": config.dist_state.oi_verify_enabled,
                    "dist_state_oi_verify_timeframes": list(config.dist_state.oi_verify_timeframes),
                    "dist_state_oi_verify_timeout_ms": config.dist_state.oi_verify_timeout_ms,
                    "dist_state_oi_verify_max_rate_per_min": (
                        config.dist_state.oi_verify_max_rate_per_min
                    ),
                    "dist_state_oi_quality_window_ms": config.dist_state.oi_quality_window_ms,
                    "dist_state_tokens_enabled": config.dist_state.tokens_enabled,
                    "dist_state_tokens_fail_fast_unknown": (
                        config.dist_state.tokens_fail_fast_unknown
                    ),
                    "dist_state_s_dir_deadband": config.dist_state.s_dir_deadband,
                    "dist_state_s_ext_enter": config.dist_state.s_ext_enter,
                    "dist_state_s_ext_exit": config.dist_state.s_ext_exit,
                    "dist_state_s_revert_min_stretch": config.dist_state.s_revert_min_stretch,
                    "dist_state_t_exp_enter": config.dist_state.t_exp_enter,
                    "dist_state_t_exp_exit": config.dist_state.t_exp_exit,
                    "dist_state_t_comp_enter": config.dist_state.t_comp_enter,
                    "dist_state_t_comp_exit": config.dist_state.t_comp_exit,
                    "dist_state_a_cont_enter": config.dist_state.a_cont_enter,
                    "dist_state_a_cont_exit": config.dist_state.a_cont_exit,
                    "dist_state_a_revert_enter": config.dist_state.a_revert_enter,
                    "dist_state_a_revert_exit": config.dist_state.a_revert_exit,
                    "dist_state_v_low_threshold": config.dist_state.v_low_threshold,
                    "dist_state_t_rise_threshold": config.dist_state.t_rise_threshold,
                    "dist_state_s_neut_max": config.dist_state.s_neut_max,
                    "dist_state_a_neut_max": config.dist_state.a_neut_max,
                    "dist_state_t_neut_max": config.dist_state.t_neut_max,
                    "dist_state_v_neut_min": config.dist_state.v_neut_min,
                    "dist_state_v_neut_max": config.dist_state.v_neut_max,
                    "dist_state_t_exp_plus": config.dist_state.t_exp_plus,
                    "dist_state_t_exp_plus_plus": config.dist_state.t_exp_plus_plus,
                    "dist_state_t_comp_plus": config.dist_state.t_comp_plus,
                    "dist_state_t_comp_plus_plus": config.dist_state.t_comp_plus_plus,
                    "dist_state_a_cont_plus": config.dist_state.a_cont_plus,
                    "dist_state_a_cont_plus_plus": config.dist_state.a_cont_plus_plus,
                    "dist_state_a_revert_plus": config.dist_state.a_revert_plus,
                    "dist_state_a_revert_plus_plus": config.dist_state.a_revert_plus_plus,
                    "dist_state_s_exh_plus": config.dist_state.s_exh_plus,
                    "dist_state_s_exh_plus_plus": config.dist_state.s_exh_plus_plus,
                    "dist_state_p_confirm_threshold": config.dist_state.p_confirm_threshold,
                    "dist_state_token_min_hold_bars_3m": (
                        config.dist_state.token_min_hold_bars_3m
                    ),
                    "dist_state_token_min_hold_bars_15m": (
                        config.dist_state.token_min_hold_bars_15m
                    ),
                    "dist_state_token_min_hold_bars_1h": (
                        config.dist_state.token_min_hold_bars_1h
                    ),
                    "dist_state_token_min_hold_bars_4h": (
                        config.dist_state.token_min_hold_bars_4h
                    ),
                    "dist_state_narrative_enabled": config.dist_state.narrative_enabled,
                    "dist_state_narrative_driver_tf": config.dist_state.narrative_driver_tf,
                    "dist_state_narrative_linger_reminder_closes": (
                        config.dist_state.narrative_linger_reminder_closes
                    ),
                    "dist_state_narrative_max_chars": config.dist_state.narrative_max_chars,
                    "dist_state_narrative_secondary_min_ratio": (
                        config.dist_state.narrative_secondary_min_ratio
                    ),
                    "dist_state_narrative_dir_ratio_min": config.dist_state.narrative_dir_ratio_min,
                },
            )
        else:
            logging.warning("--dist-dia enabled but runtime.dist_state.enabled=false; no dist diagnostics emitted.")

    last_update = time.monotonic()
    last_frame = last_update
    start_time = time.monotonic()
    reported_missing = False
    reported_still_missing = False
    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        if key != -1:
            input_state.handle_key(key)

        _drain_events(queue_events, runtime)
        if dist_feed is not None and dist_engine is not None:
            for dist_event in dist_feed.drain():
                dist_snapshot, dist_debug = dist_engine.on_kline_close_with_diagnostics(dist_event)
                if dist_diagnostics is not None:
                    dist_diagnostics.log_close(dist_event, dist_debug)

        now = time.monotonic()
        if now - last_update >= update_interval_s:
            last_update = now
            now_ms = int(time.time_ns() // 1_000_000)
            tbt_cutoffs, tbt_windows = _build_tbt_settings(
                runtime,
                supervisor,
                window_ms,
                config.tbt_window_multiplier,
            )
            _update_state(
                runtime,
                now_ms,
                tbt_cutoffs,
                tbt_windows,
                diagnostics,
                live_metrics,
            )

        if not reported_missing and now - start_time > 30:
            _report_missing(runtime, supervisor, prefix="No events yet")
            reported_missing = True
        if not reported_still_missing and now - start_time > 300:
            _report_missing(runtime, supervisor, prefix="Missing")
            reported_still_missing = True

        if now - last_frame >= 1 / 30.0:
            last_frame = now
            symbol = input_state.symbol
            now_ms = int(time.time_ns() // 1_000_000)
            status_spot = _combine_status(
                [
                    _adapter_status(
                        symbol,
                        now_ms,
                        supervisor.spot,
                        runtime.symbol_maps.spot_base_to_actual,
                    ),
                    _adapter_status(
                        symbol,
                        now_ms,
                        supervisor.coinbase,
                        runtime.coinbase_base_to_actual,
                    ),
                    _adapter_status(
                        symbol,
                        now_ms,
                        supervisor.bybit_spot,
                        runtime.bybit_spot_base_to_actual,
                    ),
                ]
            )
            status_perp = _combine_status(
                [
                    _adapter_status(
                        symbol,
                        now_ms,
                        supervisor.perp,
                        runtime.symbol_maps.perp_base_to_actual,
                    ),
                    _adapter_status(
                        symbol,
                        now_ms,
                        supervisor.bybit_perp,
                        runtime.bybit_perp_base_to_actual,
                    ),
                ]
            )
            spot_stats = None
            perp_stats = None
            spot_actuals = runtime.symbol_maps.spot_base_to_actual.get(symbol, [])
            perp_actuals = runtime.symbol_maps.perp_base_to_actual.get(symbol, [])
            coinbase_actuals = runtime.coinbase_base_to_actual.get(symbol, [])
            bybit_spot_actuals = runtime.bybit_spot_base_to_actual.get(symbol, [])
            bybit_perp_actuals = runtime.bybit_perp_base_to_actual.get(symbol, [])
            if supervisor.spot is not None:
                spot_stats = supervisor.spot.stats_for(now_ms, symbols=spot_actuals)
            coinbase_stats = None
            if supervisor.coinbase is not None:
                coinbase_stats = supervisor.coinbase.stats_for(now_ms, symbols=coinbase_actuals)
            bybit_spot_stats = None
            if supervisor.bybit_spot is not None:
                bybit_spot_stats = supervisor.bybit_spot.stats_for(
                    now_ms, symbols=bybit_spot_actuals
                )
            spot_stats = _combine_adapter_stats([spot_stats, coinbase_stats, bybit_spot_stats])
            if supervisor.perp is not None:
                perp_stats = supervisor.perp.stats_for(now_ms, symbols=perp_actuals)
            bybit_perp_stats = None
            if supervisor.bybit_perp is not None:
                bybit_perp_stats = supervisor.bybit_perp.stats_for(
                    now_ms, symbols=bybit_perp_actuals
                )
            perp_stats = _combine_adapter_stats([perp_stats, bybit_perp_stats])
            metrics_snapshot = live_metrics.snapshot(symbol)
            renderer.draw(
                stdscr,
                symbol,
                runtime.last_state.get(symbol),
                dist_snapshot=dist_snapshot,
                status_spot=status_spot,
                status_perp=status_perp,
                spot_stats=spot_stats,
                perp_stats=perp_stats,
                metrics=metrics_snapshot,
                search_mode=input_state.search_mode,
                search_buffer=input_state.search_buffer,
            )

        time.sleep(0.001)


def _collect_symbols(config: AppConfig) -> list[str]:
    symbols: set[str] = set()
    for adapter in config.adapters.values():
        symbols.update(adapter.symbols)
    return sorted(symbols)


def _build_price_source_meta(config: AppConfig) -> dict[str, PriceSourceMeta]:
    return {
        source_id: PriceSourceMeta(
            source_id=source_id,
            market_type_for_x=source.market_type_for_x,
            price_eligible=source.price_eligible,
            price_priority=source.price_priority,
        )
        for source_id, source in config.sources.items()
    }


def _coinbase_product_map(symbols: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for symbol in symbols:
        product = symbol.strip().upper()
        if "-" not in product:
            product = f"{product}-USD"
        base = product.split("-")[0]
        mapping[base] = product
    return mapping


def _coinbase_base_to_actual(mapping: dict[str, str]) -> dict[str, list[str]]:
    return {base: [product] for base, product in mapping.items()}


def _empty_resolution() -> SymbolResolution:
    return SymbolResolution(resolved={}, meta={}, missing=[], quote_pairs={})


def _bybit_symbol_maps(
    symbols: list[str],
) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    actuals: list[str] = []
    symbol_to_base: dict[str, str] = {}
    base_to_actual: dict[str, list[str]] = {}
    for symbol in symbols:
        candidate = symbol.strip().upper()
        if not candidate:
            continue
        if candidate.endswith("USDT") and len(candidate) > 4:
            actual = candidate
            base = candidate[:-4]
        else:
            base = candidate
            actual = f"{candidate}USDT"
        actuals.append(actual)
        symbol_to_base[actual] = base
        base_to_actual.setdefault(base, []).append(actual)
    actuals.sort()
    return actuals, symbol_to_base, base_to_actual


def _log_source_registry(config: AppConfig) -> None:
    for source_id, source in sorted(config.sources.items()):
        logging.info(
            "Source registry %s: venue=%s class=%s market=%s price_eligible=%s "
            "priority=%s aggressor_mode=%s quote_mode=%s",
            source_id,
            source.venue,
            source.instrument_class,
            source.market_type_for_x,
            source.price_eligible,
            source.price_priority,
            source.capabilities.aggressor_mode,
            source.capabilities.quote_mode,
        )


class AdapterSupervisor:
    def __init__(self, queue_events: queue.Queue[AdapterEvent]) -> None:
        self._queue_events = queue_events
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.spot: BinanceSpotWSAdapter | None = None
        self.perp: BinancePerpWSAdapter | None = None
        self.coinbase: CoinbaseSpotWSAdapter | None = None
        self.bybit_spot: BybitSpotWSAdapter | None = None
        self.bybit_perp: BybitPerpWSAdapter | None = None

    def start(
        self,
        *,
        binance_spot_enabled: bool,
        binance_perp_enabled: bool,
        spot_symbols: list[str],
        spot_symbol_to_base: dict[str, str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
        perp_symbol_to_base: dict[str, str],
        coinbase_symbols: list[str],
        bybit_spot_symbols: list[str],
        bybit_spot_symbol_to_base: dict[str, str],
        bybit_perp_symbols: list[str],
        bybit_perp_symbol_to_base: dict[str, str],
    ) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)
        self.update_symbols(
            binance_spot_enabled=binance_spot_enabled,
            binance_perp_enabled=binance_perp_enabled,
            spot_symbols=spot_symbols,
            spot_symbol_to_base=spot_symbol_to_base,
            spot_quotes=spot_quotes,
            quote_pairs=quote_pairs,
            quote_rates=quote_rates,
            perp_symbols=perp_symbols,
            perp_symbol_to_base=perp_symbol_to_base,
            coinbase_symbols=coinbase_symbols,
            bybit_spot_symbols=bybit_spot_symbols,
            bybit_spot_symbol_to_base=bybit_spot_symbol_to_base,
            bybit_perp_symbols=bybit_perp_symbols,
            bybit_perp_symbol_to_base=bybit_perp_symbol_to_base,
        )

    def update_symbols(
        self,
        *,
        binance_spot_enabled: bool,
        binance_perp_enabled: bool,
        spot_symbols: list[str],
        spot_symbol_to_base: dict[str, str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
        perp_symbol_to_base: dict[str, str],
        coinbase_symbols: list[str],
        bybit_spot_symbols: list[str],
        bybit_spot_symbol_to_base: dict[str, str],
        bybit_perp_symbols: list[str],
        bybit_perp_symbol_to_base: dict[str, str],
    ) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._restart(
                binance_spot_enabled=binance_spot_enabled,
                binance_perp_enabled=binance_perp_enabled,
                spot_symbols=spot_symbols,
                spot_symbol_to_base=spot_symbol_to_base,
                spot_quotes=spot_quotes,
                quote_pairs=quote_pairs,
                quote_rates=quote_rates,
                perp_symbols=perp_symbols,
                perp_symbol_to_base=perp_symbol_to_base,
                coinbase_symbols=coinbase_symbols,
                bybit_spot_symbols=bybit_spot_symbols,
                bybit_spot_symbol_to_base=bybit_spot_symbol_to_base,
                bybit_perp_symbols=bybit_perp_symbols,
                bybit_perp_symbol_to_base=bybit_perp_symbol_to_base,
            ),
            self._loop,
        )

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _restart(
        self,
        *,
        binance_spot_enabled: bool,
        binance_perp_enabled: bool,
        spot_symbols: list[str],
        spot_symbol_to_base: dict[str, str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
        perp_symbol_to_base: dict[str, str],
        coinbase_symbols: list[str],
        bybit_spot_symbols: list[str],
        bybit_spot_symbol_to_base: dict[str, str],
        bybit_perp_symbols: list[str],
        bybit_perp_symbol_to_base: dict[str, str],
    ) -> None:
        await self._cancel_tasks()
        spot = (
            BinanceSpotWSAdapter(
                symbols=spot_symbols,
                symbol_to_base=spot_symbol_to_base,
                symbol_quotes=spot_quotes,
                quote_pairs=quote_pairs,
                quote_rates=quote_rates,
            )
            if binance_spot_enabled
            else None
        )
        perp = (
            BinancePerpWSAdapter(
                symbols=perp_symbols,
                symbol_to_base=perp_symbol_to_base,
            )
            if binance_perp_enabled
            else None
        )
        coinbase = CoinbaseSpotWSAdapter(symbols=coinbase_symbols) if coinbase_symbols else None
        bybit_spot = (
            BybitSpotWSAdapter(
                symbols=bybit_spot_symbols,
                symbol_to_base=bybit_spot_symbol_to_base,
            )
            if bybit_spot_symbols
            else None
        )
        bybit_perp = (
            BybitPerpWSAdapter(
                symbols=bybit_perp_symbols,
                symbol_to_base=bybit_perp_symbol_to_base,
            )
            if bybit_perp_symbols
            else None
        )
        with self._lock:
            self.spot = spot
            self.perp = perp
            self.coinbase = coinbase
            self.bybit_spot = bybit_spot
            self.bybit_perp = bybit_perp
        self._tasks = []
        if spot is not None:
            self._tasks.append(asyncio.create_task(self._consume(spot)))
        if perp is not None:
            self._tasks.append(asyncio.create_task(self._consume(perp)))
        if coinbase is not None:
            self._tasks.append(asyncio.create_task(self._consume(coinbase)))
        if bybit_spot is not None:
            self._tasks.append(asyncio.create_task(self._consume(bybit_spot)))
        if bybit_perp is not None:
            self._tasks.append(asyncio.create_task(self._consume(bybit_perp)))

    async def _cancel_tasks(self) -> None:
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _consume(self, adapter) -> None:
        try:
            async for item in adapter.stream():
                self._queue_events.put(item)
        except asyncio.CancelledError:
            adapter._mark_disconnected()
            raise


def _init_runtime(
    symbols: list[str],
    window_ms: int,
    symbol_maps: SymbolMaps,
    source_meta: dict[str, PriceSourceMeta],
    defaults: Defaults,
    selector_stale_failover_ms: int,
    selector_recovery_confirm_cycles: int,
    selector_switch_cooldown_cycles: int,
    hygiene_config: HygieneConfig,
    coinbase_base_to_actual: dict[str, list[str]],
    bybit_spot_base_to_actual: dict[str, list[str]],
    bybit_perp_base_to_actual: dict[str, list[str]],
) -> RuntimeState:
    loops: dict[str, EngineLoop] = {}
    last_state: dict[str, StateSnapshot | None] = {}
    pending: dict[str, list[Event]] = {symbol: [] for symbol in symbols}
    last_event_ms: dict[str, int | None] = {symbol: None for symbol in symbols}
    source_allowlists: dict[str, set[str] | None] = {symbol: None for symbol in symbols}

    for symbol in symbols:
        buffer = RollingEventBuffer(
            window_delta_ms=window_ms,
            source_meta=source_meta,
            price_selector=PriorityStickySelector(
                stale_failover_ms=selector_stale_failover_ms,
                recovery_confirm_cycles=selector_recovery_confirm_cycles,
                switch_cooldown_cycles=selector_switch_cooldown_cycles,
            ),
        )
        engine = StateEngine(defaults)
        loops[symbol] = EngineLoop(
            symbol=symbol,
            buffer=buffer,
            engine=engine,
            control_baseline=DynamicControlBaseline(defaults.control_baseline),
        )
        last_state[symbol] = None

    return RuntimeState(
        loops=loops,
        last_state=last_state,
        pending=pending,
        symbol_maps=symbol_maps,
        last_event_ms=last_event_ms,
        source_meta=source_meta,
        source_allowlists=source_allowlists,
        filter_reset_events=[],
        coinbase_base_to_actual=coinbase_base_to_actual,
        bybit_spot_base_to_actual=bybit_spot_base_to_actual,
        bybit_perp_base_to_actual=bybit_perp_base_to_actual,
        hygiene=HygieneIngestor(hygiene_config),
    )


def _drain_events(queue_events: queue.Queue[AdapterEvent], runtime: RuntimeState) -> None:
    while True:
        try:
            item = queue_events.get_nowait()
        except queue.Empty:
            break
        base_symbol = item.base_symbol.upper() if item.base_symbol is not None else None
        if base_symbol is None:
            base_symbol = _legacy_map_to_base(item, runtime.symbol_maps)
        if base_symbol is None or base_symbol not in runtime.pending:
            continue
        event = runtime.hygiene.process(item, base_symbol=base_symbol)
        if event is None:
            continue
        runtime.pending[base_symbol].append(event)
        runtime.last_event_ms[base_symbol] = event.timestamp


def _apply_source_filter(
    runtime: RuntimeState,
    *,
    symbol: str,
    source_allowlist: set[str] | None,
) -> None:
    old_allowlist = runtime.source_allowlists.get(symbol)
    new_allowlist = set(source_allowlist) if source_allowlist is not None else None
    old_key = (
        ("__ALL__",)
        if old_allowlist is None
        else tuple(sorted(old_allowlist))
    )
    new_key = (
        ("__ALL__",)
        if new_allowlist is None
        else tuple(sorted(new_allowlist))
    )
    if old_key == new_key:
        return
    loop = runtime.loops[symbol]
    loop.source_allowlist = new_allowlist
    loop.buffer.reset_context()
    loop.engine.reset_context()
    loop.control_baseline.reset_context()
    runtime.pending[symbol] = []
    runtime.last_state[symbol] = None
    runtime.last_event_ms[symbol] = None
    runtime.source_allowlists[symbol] = new_allowlist
    runtime.filter_reset_events.append(
        FilterContextResetEvent(
            symbol=symbol,
            old_mask=old_key,
            new_mask=new_key,
            reset_applied=(
                "rolling_buffer",
                "normalization_scales",
                "smoothing_state",
                "persistence_state",
                "price_selector_state",
                "visual_bins",
                "lean_state",
            ),
        )
    )


def _update_state(
    runtime: RuntimeState,
    now_ms: int,
    tbt_cutoffs: dict[str, int],
    tbt_windows: dict[str, int],
    diagnostics: "DiagnosticLogger | None",
    live_metrics: LiveMetrics | None,
) -> None:
    for symbol, loop in runtime.loops.items():
        events = runtime.pending[symbol]
        runtime.pending[symbol] = []
        window_override = tbt_windows.get(symbol)
        if events:
            state = loop.step(events, now_ms, window_override_ms=window_override)
            switch_events = loop.buffer.pop_price_switch_events()
            runtime.last_state[symbol] = state
            if live_metrics is not None and state is not None:
                live_metrics.update(symbol, state, now_ms)
            if diagnostics is not None:
                diagnostics.log(
                    symbol,
                    runtime.last_state[symbol],
                    now_ms,
                    loop.buffer,
                    switch_events=switch_events,
                )
            if state is None and loop.buffer.active_price_source_id is None:
                logging.warning(
                    "Price series unavailable for %s (no eligible source).",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.log_price_series_unavailable(symbol, now_ms, loop.buffer)
            continue
        last_event_ms = runtime.last_event_ms.get(symbol)
        if last_event_ms is None:
            continue
        cutoff_ms = tbt_cutoffs.get(symbol)
        if cutoff_ms is None:
            continue
        if now_ms - last_event_ms <= cutoff_ms:
            state = loop.step(events, now_ms, window_override_ms=window_override)
            switch_events = loop.buffer.pop_price_switch_events()
            runtime.last_state[symbol] = state
            if live_metrics is not None and state is not None:
                live_metrics.update(symbol, state, now_ms)
            if diagnostics is not None:
                diagnostics.log(
                    symbol,
                    runtime.last_state[symbol],
                    now_ms,
                    loop.buffer,
                    switch_events=switch_events,
                )
            if state is None and loop.buffer.active_price_source_id is None:
                logging.warning(
                    "Price series unavailable for %s (no eligible source).",
                    symbol,
                )
                if diagnostics is not None:
                    diagnostics.log_price_series_unavailable(symbol, now_ms, loop.buffer)
    if runtime.filter_reset_events and diagnostics is not None:
        diagnostics.log_filter_resets(now_ms, runtime.filter_reset_events)
    if runtime.filter_reset_events:
        runtime.filter_reset_events.clear()
    if diagnostics is not None:
        for metrics in runtime.hygiene.flush_due(now_ms):
            diagnostics.log_hygiene_metrics(now_ms, metrics)


def _adapter_status(
    symbol: str,
    now_ms: int,
    adapter: BaseAdapter | None,
    mapping: dict[str, list[str]],
) -> AdapterStatus:
    if adapter is None:
        return AdapterStatus.DISCONNECTED
    actuals = mapping.get(symbol)
    if not actuals:
        return AdapterStatus.DISCONNECTED
    if adapter.status(actuals[0], now_ms) == AdapterStatus.DISCONNECTED:
        return AdapterStatus.DISCONNECTED
    statuses = [adapter.status(actual, now_ms) for actual in actuals]
    if any(status == AdapterStatus.CONNECTED for status in statuses):
        return AdapterStatus.CONNECTED
    return AdapterStatus.STALE


def _combine_status(statuses: list[AdapterStatus]) -> AdapterStatus:
    if not statuses:
        return AdapterStatus.DISCONNECTED
    if any(status == AdapterStatus.CONNECTED for status in statuses):
        return AdapterStatus.CONNECTED
    if any(status == AdapterStatus.STALE for status in statuses):
        return AdapterStatus.STALE
    return AdapterStatus.DISCONNECTED


def _combine_adapter_stats(stats_list: list[AdapterStats | None]) -> AdapterStats | None:
    combined = [stats for stats in stats_list if stats is not None]
    if not combined:
        return None
    message_count = sum(stats.message_count for stats in combined)
    dropped_count = sum(stats.dropped_count for stats in combined)
    reconnect_count = sum(stats.reconnect_count for stats in combined)
    active_pairs = sum(stats.active_pairs for stats in combined)
    total_pairs = sum(stats.total_pairs for stats in combined)
    tbt_values = [stats.tbt_ms for stats in combined if stats.tbt_ms is not None]
    tbt_ms = min(tbt_values) if tbt_values else None
    return AdapterStats(
        message_count=message_count,
        dropped_count=dropped_count,
        reconnect_count=reconnect_count,
        active_pairs=active_pairs,
        total_pairs=total_pairs,
        tbt_ms=tbt_ms,
    )


def _configure_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "flow_lens.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )


_FAULT_LOG: TextIO | None = None


def _enable_fault_handler() -> None:
    global _FAULT_LOG
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fault_path = log_dir / f"fault-{time.strftime('%Y%m%d-%H%M%S')}.log"
    _FAULT_LOG = fault_path.open("w", encoding="utf-8")
    faulthandler.enable(file=_FAULT_LOG, all_threads=True)
    logging.info("Fault handler enabled (faulthandler -> %s).", fault_path)


class DiagnosticLogger:
    def __init__(
        self,
        *,
        path: Path,
        symbols: set[str],
        tanh_k: float,
        config: dict[str, object],
        max_lines: int = 20_000,
    ) -> None:
        self._base_path = path
        self._symbols = {symbol.upper() for symbol in symbols}
        self._tanh_k = tanh_k
        self._config = config
        self._max_lines = max_lines
        self._line_count = 0
        self._part = 0
        self._run_id = time.strftime("%Y%m%d-%H%M%S")
        self._base_path.parent.mkdir(exist_ok=True)
        self._file = self._open_new_file()
        self._write_config_meta(self._config)
        self._write_inference_diagnostics_defaults(self._config)

    def _open_new_file(self) -> TextIO:
        suffix = f"-{self._run_id}-p{self._part:02d}.jsonl"
        filename = self._base_path.with_name(self._base_path.stem + suffix)
        self._part += 1
        self._line_count = 0
        return filename.open("w", encoding="utf-8")

    def _write_config_meta(self, config: dict[str, object]) -> None:
        meta = {"_meta": {"type": "config", "config": config}}
        self._file.write(json.dumps(meta, separators=(",", ":")) + "\n")
        self._file.flush()

    def _write_inference_diagnostics_defaults(self, config: dict[str, object]) -> None:
        sources = config.get("source_registry")
        if not isinstance(sources, dict):
            return
        for source_id, details in sources.items():
            if not isinstance(source_id, str) or not isinstance(details, dict):
                continue
            aggressor_mode = details.get("aggressor_mode")
            record = {
                "event_type": "inference_diagnostics",
                "source_id": source_id,
                "aggressor_mode": aggressor_mode,
                "inferred_with_bbo_rate": 0.0,
                "inferred_mid_fallback_rate": 0.0,
                "inferred_tick_rule_fallback_rate": 0.0,
                "unknown_side_rate": 0.0,
                "bbo_age_ms_p50": 0.0,
                "bbo_age_ms_p95": 0.0,
            }
            self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._line_count += 1
        self._file.flush()

    def _rotate_if_needed(self) -> None:
        if self._line_count < self._max_lines:
            return
        self._file.close()
        self._file = self._open_new_file()
        # Re-emit runtime config for every rotated file to keep reports self-contained.
        self._write_config_meta(self._config)
        self._write_inference_diagnostics_defaults(self._config)

    def log(
        self,
        symbol: str,
        state: StateSnapshot | None,
        now_ms: int,
        buffer: RollingEventBuffer,
        *,
        switch_events: tuple[PriceSwitchEvent, ...] = (),
    ) -> None:
        symbol_upper = symbol.upper()
        if symbol_upper not in self._symbols:
            return
        if state is None:
            for switch in switch_events:
                switch_record = {
                    "event_type": "price_source_switch",
                    "ts_wall_ms": int(time.time() * 1000),
                    "now_ms": now_ms,
                    "symbol": symbol_upper,
                    "from_source_id": switch.from_source_id,
                    "to_source_id": switch.to_source_id,
                    "reason": switch.reason,
                    "staleness_from_ms": switch.staleness_from_ms,
                    "staleness_to_ms": switch.staleness_to_ms,
                    "priority_from": switch.priority_from,
                    "priority_to": switch.priority_to,
                    "selector_policy": switch.selector_policy,
                }
                self._file.write(json.dumps(switch_record, separators=(",", ":")) + "\n")
                self._line_count += 1
            self._file.flush()
            self._rotate_if_needed()
            return
        record = {
            "ts_wall_ms": int(time.time() * 1000),
            "now_ms": now_ms,
            "symbol": symbol_upper,
            "window_ms": buffer.window_delta_ms,
            "window_seconds": state.window_seconds,
            "buffer_event_count": buffer.size,
            "tanh_k": self._tanh_k,
            "active_price_source_id": state.active_price_source_id,
            "selector_policy": state.selector_policy,
            "price_series_side": state.price_series_side,
            "price_series_used": state.price_series_used,
            "spot_fresh": state.spot_fresh,
            "perp_fresh": state.perp_fresh,
            "last_spot_event_ts": state.last_spot_event_ts,
            "last_perp_event_ts": state.last_perp_event_ts,
            "spot_event_count_window": state.spot_event_count_window,
            "perp_event_count_window": state.perp_event_count_window,
            "price_start": state.price_start,
            "price_end": state.price_end,
            "log_return": state.log_return,
            "delta_price": state.price_end - state.price_start,
            "disp_rate": state.disp_rate,
            "E_rate": state.effort_rate,
            "disp_scale": state.disp_scale,
            "E_scale": state.effort_scale,
            "disp_deadband_active": state.disp_deadband_active,
            "E_spot": state.e_spot,
            "E_perp": state.e_perp,
            "E_dir": state.e_dir,
            "E_dir_sign": _sign(state.e_dir),
            "E_total": state.total_effort,
            "D": state.dominance,
            "E_spot_share": state.e_spot_share,
            "X_raw": state.x_raw,
            "X": state.x,
            "size_raw": state.size_raw,
            "size_bin": state.size_bin,
            "size_effort_norm": state.size_effort_norm,
            "size_scale": state.size_scale,
            "disp": state.disp,
            "effort_floor": state.effort_floor,
            "effort_median": state.effort_median,
            "effort_norm": state.effort_norm,
            "gate": state.gate,
            "eff_raw": state.eff_raw,
            "Y_raw": state.y_raw,
            "Y_gated": state.y_gated,
            "Y": state.y,
            "persist_raw": state.persist_raw,
            "persist_slope": state.persist_slope,
            "persist_sign": state.persist_sign,
            "persist_dir_raw": state.persist_dir_raw,
            "persist_dir_sign": state.persist_dir_sign,
            "persist_input": state.persist_input,
            "persist_input_value": state.persist_input_value,
            "persist_a_eff": state.persist_a_eff,
            "persist_a_dir": state.persist_a_dir,
            "persist_dt_s": state.persist_dt_s,
            "persist_gain_per_second": state.persist_gain_per_second,
            "persist_input_deadband": state.persist_input_deadband,
            "persist_step_coeff": state.persist_step_coeff,
            "persist_alpha_eff": state.persist_alpha_eff,
            "persist_alpha_dir": state.persist_alpha_dir,
            "persist_tau_eff_s": state.persist_tau_eff_s,
            "persist_tau_dir_s": state.persist_tau_dir_s,
            "persist_update_mode": state.persist_update_mode,
            "persist_activity_flag": state.persist_activity_flag,
            "persist_pivot_confirm_elapsed_s": state.persist_pivot_confirm_elapsed_s,
            "persist_pivot_cooldown_remaining_s": state.persist_pivot_cooldown_remaining_s,
            "persist_last_confirmed_dir_sign": state.persist_last_confirmed_dir_sign,
            "persist_pivot_target_dir_sign": state.persist_pivot_target_dir_sign,
            "persist_neutral_dir_abs_flash": state.persist_neutral_dir_abs_flash,
            "persist_neutral_dir_abs_persist": state.persist_neutral_dir_abs_persist,
            "halo_raw": state.halo_raw,
            "halo": state.halo,
            "halo_bin": state.halo_bin,
            "source_count_active": state.source_count_active,
            "max_source_share": state.max_source_share,
            "top_source_id": state.top_source_id,
            "top_source_effort": state.top_source_effort,
        }
        if state.control_baseline_enabled:
            record.update(
                {
                    "control_baseline_enabled": state.control_baseline_enabled,
                    "control_baseline_initialized": state.control_baseline_initialized,
                    "control_baseline_x": state.control_baseline_x,
                    "control_baseline_target_x": state.control_baseline_target_x,
                    "control_baseline_mode": state.control_baseline_mode,
                    "control_baseline_breakout_age_s": state.control_baseline_breakout_age_s,
                    "control_baseline_delta": state.control_baseline_delta,
                    "control_baseline_visible": state.control_baseline_visible,
                    "control_baseline_midnight_tick_visible": state.control_baseline_midnight_tick_visible,
                    "control_baseline_midnight_tick_locked": state.control_baseline_midnight_tick_locked,
                    "control_baseline_midnight_tick_x": state.control_baseline_midnight_tick_x,
                    "control_baseline_midnight_tick_samples": state.control_baseline_midnight_tick_samples,
                }
            )
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()
        self._line_count += 1
        for switch in switch_events:
            switch_record = {
                "event_type": "price_source_switch",
                "ts_wall_ms": int(time.time() * 1000),
                "now_ms": now_ms,
                "symbol": symbol_upper,
                "from_source_id": switch.from_source_id,
                "to_source_id": switch.to_source_id,
                "reason": switch.reason,
                "staleness_from_ms": switch.staleness_from_ms,
                "staleness_to_ms": switch.staleness_to_ms,
                "priority_from": switch.priority_from,
                "priority_to": switch.priority_to,
                "selector_policy": switch.selector_policy,
            }
            self._file.write(json.dumps(switch_record, separators=(",", ":")) + "\n")
            self._line_count += 1
        self._file.flush()
        self._rotate_if_needed()

    def log_filter_resets(
        self,
        now_ms: int,
        events: list[FilterContextResetEvent],
    ) -> None:
        for event in events:
            record = {
                "event_type": "filter_context_reset",
                "ts_wall_ms": int(time.time() * 1000),
                "now_ms": now_ms,
                "symbol": event.symbol,
                "old_mask": list(event.old_mask),
                "new_mask": list(event.new_mask),
                "reset_applied": list(event.reset_applied),
            }
            self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._line_count += 1
        self._file.flush()
        self._rotate_if_needed()

    def log_price_series_unavailable(
        self,
        symbol: str,
        now_ms: int,
        buffer: RollingEventBuffer,
    ) -> None:
        record = {
            "event_type": "price_series_unavailable",
            "ts_wall_ms": int(time.time() * 1000),
            "now_ms": now_ms,
            "symbol": symbol.upper(),
            "selector_policy": buffer.selector_policy,
            "active_price_source_id": buffer.active_price_source_id,
            "price_series_side": buffer.price_series_side,
            "price_series_used": buffer.price_series_side,
            "reason": "no_eligible_price_source",
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._line_count += 1
        self._file.flush()
        self._rotate_if_needed()

    def log_hygiene_metrics(self, now_ms: int, metrics: HygieneMetricsEvent) -> None:
        if metrics.symbol.upper() not in self._symbols:
            return
        record = {
            "event_type": "hygiene_metrics",
            "ts_wall_ms": int(time.time() * 1000),
            "now_ms": now_ms,
            "symbol": metrics.symbol,
            "source_id": metrics.source_id,
            "interval_start_ms": metrics.interval_start_ms,
            "interval_end_ms": metrics.interval_end_ms,
            "samples_with_venue_ts": metrics.samples_with_venue_ts,
            "wire_lag_ms_p50": metrics.wire_lag_ms_p50,
            "wire_lag_ms_p95": metrics.wire_lag_ms_p95,
            "stale_on_arrival_dropped": metrics.stale_on_arrival_dropped,
            "dedupe_dropped": metrics.dedupe_dropped,
            "venue_ts_missing": metrics.venue_ts_missing,
            "negative_wire_lag": metrics.negative_wire_lag,
            "future_venue_ts": metrics.future_venue_ts,
            "connect_gate_rearm_inactivity": metrics.connect_gate_rearm_inactivity,
            "connect_gate_rearm_stale_burst": metrics.connect_gate_rearm_stale_burst,
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._line_count += 1
        self._file.flush()
        self._rotate_if_needed()


class DistStateDiagnosticLogger:
    _METRIC_KEYS = ("v", "s", "a", "p", "t")

    def __init__(
        self,
        *,
        path: Path,
        config: dict[str, object],
        max_lines: int = 20_000,
    ) -> None:
        self._base_path = path
        self._config = config
        self._max_lines = max_lines
        self._line_count = 0
        self._part = 0
        self._run_id = time.strftime("%Y%m%d-%H%M%S")
        self._base_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._open_new_file()
        self._write_meta()
        self._expected_by_tf: dict[str, int] = {}
        self._captured_by_tf: dict[str, dict[str, int]] = {}

    def _open_new_file(self) -> TextIO:
        suffix = f"-{self._run_id}-p{self._part:02d}.jsonl"
        filename = self._base_path.with_name(self._base_path.stem + suffix)
        self._part += 1
        self._line_count = 0
        return filename.open("w", encoding="utf-8")

    def _write_meta(self) -> None:
        meta = {"_meta": {"type": "dist_state_config", "config": self._config}}
        self._file.write(json.dumps(meta, separators=(",", ":")) + "\n")
        self._file.flush()

    def _rotate_if_needed(self) -> None:
        if self._line_count < self._max_lines:
            return
        self._file.close()
        self._file = self._open_new_file()
        self._write_meta()

    def log_close(self, event: DistKlineCloseEvent, debug: dict[str, object]) -> None:
        tf = str(debug.get("tf", event.tf))
        processed = bool(debug.get("processed", False))
        if processed:
            expected = self._expected_by_tf.get(tf, 0) + 1
            self._expected_by_tf[tf] = expected
            metric_counts = self._captured_by_tf.setdefault(tf, {k: 0 for k in self._METRIC_KEYS})
            for metric in self._METRIC_KEYS:
                value = debug.get(f"metrics_{metric}")
                if value is not None:
                    metric_counts[metric] += 1
        expected = self._expected_by_tf.get(tf, 0)
        metric_counts = self._captured_by_tf.setdefault(tf, {k: 0 for k in self._METRIC_KEYS})
        coverage_pct = {
            f"coverage_{metric}_pct": (
                (float(metric_counts[metric]) / float(expected) * 100.0) if expected > 0 else 0.0
            )
            for metric in self._METRIC_KEYS
        }
        record = {
            "event_type": "dist_state_close",
            "ts_wall_ms": int(time.time() * 1000),
            "symbol": event.symbol.upper(),
            "source_id": event.source_id,
            "tf": tf,
            "kline_close_ms": event.kline_close_ms,
            "event_ts_recv_ms": event.ts_recv_ms,
            "event_kline_open_ms": event.kline_open_ms,
            "event_open": event.open,
            "event_high": event.high,
            "event_low": event.low,
            "event_close": event.close,
            "processed": processed,
            "drop_reason": debug.get("drop_reason"),
            "p_availability_mode": debug.get("p_availability_mode"),
            "selection_source": debug.get("selection_source"),
            "selection_reason": debug.get("selection_reason"),
            "oi_tolerance_ms": debug.get("oi_tolerance_ms"),
            "oi_sample_present": debug.get("oi_sample_present"),
            "oi_sample_venue_time_ms": debug.get("oi_sample_venue_time_ms"),
            "oi_sample_oi": debug.get("oi_sample_oi"),
            "oi_sample_recv_ms": debug.get("oi_sample_recv_ms"),
            "oi_sample_seq": debug.get("oi_sample_seq"),
            "oi_offset_ms": debug.get("oi_offset_ms"),
            "oi_staleness_ms": debug.get("oi_staleness_ms"),
            "sampler_reason": debug.get("sampler_reason"),
            "verify_reason": debug.get("verify_reason"),
            "sampler_offset_ms": debug.get("sampler_offset_ms"),
            "verify_offset_ms": debug.get("verify_offset_ms"),
            "sampler_tolerance_margin_ms": debug.get("sampler_tolerance_margin_ms"),
            "verify_tolerance_margin_ms": debug.get("verify_tolerance_margin_ms"),
            "best_candidate_source": debug.get("best_candidate_source"),
            "best_candidate_abs_offset_ms": debug.get("best_candidate_abs_offset_ms"),
            "best_candidate_tolerance_margin_ms": debug.get("best_candidate_tolerance_margin_ms"),
            "selected_offset_ms": debug.get("selected_offset_ms"),
            "selected_abs_offset_ms": debug.get("selected_abs_offset_ms"),
            "selected_tolerance_margin_ms": debug.get("selected_tolerance_margin_ms"),
            "oi_bootstrap_source": debug.get("oi_bootstrap_source"),
            "oi_bootstrap_age_ms": debug.get("oi_bootstrap_age_ms"),
            "sampler_snapshot_present": event.sampler_snapshot is not None,
            "verify_snapshot_present": event.verify_snapshot is not None,
            "p_available": debug.get("p_available"),
            "p_status": ("computed" if debug.get("p_available") else "missing"),
            "p_missing_reason": debug.get("p_missing_reason"),
            "ready_core": debug.get("ready_core"),
            "ready_p": debug.get("ready_p"),
            "metrics_v": debug.get("metrics_v"),
            "metrics_s": debug.get("metrics_s"),
            "metrics_a": debug.get("metrics_a"),
            "metrics_p": debug.get("metrics_p"),
            "metrics_t": debug.get("metrics_t"),
            "bin_v": debug.get("bin_v"),
            "bin_s": debug.get("bin_s"),
            "bin_a": debug.get("bin_a"),
            "bin_p": debug.get("bin_p"),
            "bin_t": debug.get("bin_t"),
            "token": debug.get("token"),
            "token_strength": debug.get("token_strength"),
            "token_changed": debug.get("token_changed"),
            "token_prev": debug.get("token_prev"),
            "token_prev_strength": debug.get("token_prev_strength"),
            "token_dwell_blocked": debug.get("token_dwell_blocked"),
            "token_override_reason": debug.get("token_override_reason"),
            "token_predicate_hits": debug.get("token_predicate_hits"),
            "token_inputs": debug.get("token_inputs"),
            "narrative_emitted": debug.get("narrative_emitted"),
            "narrative_emission_reason": debug.get("narrative_emission_reason"),
            "narrative_state_id": debug.get("narrative_state_id"),
            "narrative_template_id": debug.get("narrative_template_id"),
            "narrative_params": debug.get("narrative_params"),
            "narrative_as_of_close_ms": debug.get("narrative_as_of_close_ms"),
            "narrative_driver_tf": debug.get("narrative_driver_tf"),
            "narrative_started_close_ms": debug.get("narrative_started_close_ms"),
            "narrative_age_closes": debug.get("narrative_age_closes"),
            "narrative_reason_codes": debug.get("narrative_reason_codes"),
            "narrative_quality_flags": debug.get("narrative_quality_flags"),
            "narrative_text_template": debug.get("narrative_text_template"),
            "expected_closes_tf": expected,
            "captured_v_tf": metric_counts["v"],
            "captured_s_tf": metric_counts["s"],
            "captured_a_tf": metric_counts["a"],
            "captured_p_tf": metric_counts["p"],
            "captured_t_tf": metric_counts["t"],
            **coverage_pct,
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        if debug.get("narrative_emitted"):
            narrative_event = {
                "event_type": "dist_state_narrative",
                "ts_wall_ms": int(time.time() * 1000),
                "symbol": event.symbol.upper(),
                "source_id": event.source_id,
                "driver_close_ms": debug.get("narrative_as_of_close_ms"),
                "driver_tf": debug.get("narrative_driver_tf"),
                "emission_reason": debug.get("narrative_emission_reason"),
                "narrative_state_id": debug.get("narrative_state_id"),
                "narrative_template_id": debug.get("narrative_template_id"),
                "narrative_params": debug.get("narrative_params"),
                "narrative_started_close_ms": debug.get("narrative_started_close_ms"),
                "narrative_age_closes": debug.get("narrative_age_closes"),
                "narrative_reason_codes": debug.get("narrative_reason_codes"),
                "narrative_quality_flags": debug.get("narrative_quality_flags"),
                "narrative_text_template": debug.get("narrative_text_template"),
                "narrative_stack_tokens": debug.get("narrative_stack_tokens"),
            }
            self._file.write(json.dumps(narrative_event, separators=(",", ":")) + "\n")
            self._line_count += 1
        self._file.flush()
        self._line_count += 1
        self._rotate_if_needed()


def _runtime_config_map(config: AppConfig) -> dict[str, object]:
    source_registry = {
        source_id: {
            "venue": source.venue,
            "instrument_class": source.instrument_class,
            "market_type_for_x": source.market_type_for_x,
            "price_eligible": source.price_eligible,
            "price_priority": source.price_priority,
            "has_size": source.capabilities.has_size,
            "has_aggressor": source.capabilities.has_aggressor,
            "aggressor_mode": source.capabilities.aggressor_mode,
            "quote_mode": source.capabilities.quote_mode,
        }
        for source_id, source in config.sources.items()
    }
    return {
        "update_window_seconds": config.update_window_seconds,
        "tbt_window_multiplier": config.tbt_window_multiplier,
        "price_selector_policy": config.price_selector_policy,
        "price_selector_stale_failover_ms": config.price_selector_stale_failover_ms,
        "price_selector_recovery_confirm_cycles": config.price_selector_recovery_confirm_cycles,
        "price_selector_switch_cooldown_cycles": config.price_selector_switch_cooldown_cycles,
        "tanh_k": config.tanh_k,
        "scale_window_seconds": config.scale_window_seconds,
        "persist_enabled": config.persist_enabled,
        "persist_input": config.persist_input,
        "persist_input_deadband": config.persist_input_deadband,
        "persist_neutral_dir_abs_flash": config.persist_neutral_dir_abs_flash,
        "persist_neutral_dir_abs_persist": config.persist_neutral_dir_abs_persist,
        "persist_tau_eff_active": config.persist_tau_eff_active,
        "persist_tau_dir_active": config.persist_tau_dir_active,
        "persist_pivot_active_abs": config.persist_pivot_active_abs,
        "persist_pivot_confirm_s": config.persist_pivot_confirm_s,
        "persist_pivot_neutralize_tau": config.persist_pivot_neutralize_tau,
        "persist_pivot_neutral_zone_abs": config.persist_pivot_neutral_zone_abs,
        "persist_rebuild_confirm_s": config.persist_rebuild_confirm_s,
        "persist_pivot_cooldown_s": config.persist_pivot_cooldown_s,
        "persist_pivot_max_s": config.persist_pivot_max_s,
        "persist_max_delta_s_eff_per_second": config.persist_max_delta_s_eff_per_second,
        "persist_tau_dir_pivot": config.persist_tau_dir_pivot,
        "persist_dormant_quiet_abs": config.persist_dormant_quiet_abs,
        "persist_dormant_active_abs": config.persist_dormant_active_abs,
        "persist_dormant_quiet_s": config.persist_dormant_quiet_s,
        "persist_tau_dormant": config.persist_tau_dormant,
        "persist_dormant_effort_norm_threshold": config.persist_dormant_effort_norm_threshold,
        "disp_scale_multiplier": config.disp_scale_multiplier,
        "disp_scale_percentile": config.disp_scale_percentile,
        "disp_scale_min_samples": config.disp_scale_min_samples,
        "disp_scale_floor_percentile": config.disp_scale_floor_percentile,
        "effort_scale_percentile": config.effort_scale_percentile,
        "effort_scale_min_samples": config.effort_scale_min_samples,
        "size_scale_percentile": config.size_scale_percentile,
        "effort_floor_multiplier": config.effort_floor_multiplier,
        "effort_floor_ticks": config.effort_floor_ticks,
        "smoothing_dominance_alpha": config.smoothing_dominance_alpha,
        "smoothing_effectiveness_alpha": config.smoothing_effectiveness_alpha,
        "dispersion_metric": config.dispersion_metric,
        "halo_growth_rate": config.halo_growth_rate,
        "halo_decay_rate": config.halo_decay_rate,
        "binning_dot_size_thresholds": config.binning_dot_size_thresholds,
        "binning_halo_thresholds": config.binning_halo_thresholds,
        "binning_hysteresis_band": config.binning_hysteresis_band,
        "control_baseline_enabled": config.control_baseline_enabled,
        "control_baseline_target_window_s": config.control_baseline_target_window_s,
        "control_baseline_target_update_s": config.control_baseline_target_update_s,
        "control_baseline_breakout_band": config.control_baseline_breakout_band,
        "control_baseline_confirm_s": config.control_baseline_confirm_s,
        "control_baseline_exit_band_frac": config.control_baseline_exit_band_frac,
        "control_baseline_peg_half_life_s": config.control_baseline_peg_half_life_s,
        "control_baseline_reanchor_half_life_s": config.control_baseline_reanchor_half_life_s,
        "control_baseline_peg_deadband": config.control_baseline_peg_deadband,
        "control_baseline_max_window_samples": config.control_baseline_max_window_samples,
        "control_baseline_center_suppress_band": config.control_baseline_center_suppress_band,
        "control_baseline_line_hide_warmup_s": config.control_baseline_line_hide_warmup_s,
        "control_baseline_midnight_tick_enabled": config.control_baseline_midnight_tick_enabled,
        "control_baseline_midnight_tick_min_samples": config.control_baseline_midnight_tick_min_samples,
        "control_baseline_midnight_tick_min_elapsed_s": config.control_baseline_midnight_tick_min_elapsed_s,
        "hygiene_enabled": config.hygiene_enabled,
        "hygiene_max_excess_wire_lag_ms": config.hygiene_max_excess_wire_lag_ms,
        "hygiene_hard_max_wire_lag_ms": config.hygiene_hard_max_wire_lag_ms,
        "hygiene_wire_lag_baseline_window_s": config.hygiene_wire_lag_baseline_window_s,
        "hygiene_wire_lag_baseline_sample_interval_ms": (
            config.hygiene_wire_lag_baseline_sample_interval_ms
        ),
        "hygiene_wire_lag_baseline_min_samples": config.hygiene_wire_lag_baseline_min_samples,
        "hygiene_wire_lag_baseline_max_samples": config.hygiene_wire_lag_baseline_max_samples,
        "hygiene_dedupe_ttl_s": config.hygiene_dedupe_ttl_s,
        "hygiene_log_interval_s": config.hygiene_log_interval_s,
        "hygiene_future_venue_ts_grace_ms": config.hygiene_future_venue_ts_grace_ms,
        "hygiene_connect_gate_s": config.hygiene_connect_gate_s,
        "hygiene_connect_gate_max_excess_wire_lag_ms": (
            config.hygiene_connect_gate_max_excess_wire_lag_ms
        ),
        "hygiene_connect_gate_hard_max_wire_lag_ms": (
            config.hygiene_connect_gate_hard_max_wire_lag_ms
        ),
        "hygiene_connect_gate_rearm_after_s": config.hygiene_connect_gate_rearm_after_s,
        "tui_min_width": config.tui_min_width,
        "tui_min_height": config.tui_min_height,
        "tui_max_width": config.tui_max_width,
        "tui_max_height": config.tui_max_height,
        "tui_dot_radii": config.tui_dot_radii,
        "tui_halo_radii": config.tui_halo_radii,
        "tui_frame_enabled": config.tui_frame_enabled,
        "tui_frame_inset_px": config.tui_frame_inset_px,
        "tui_frame_band_inner": config.tui_frame_band_inner,
        "tui_frame_band_outer": config.tui_frame_band_outer,
        "tui_show_dev_panel": config.tui_show_dev_panel,
        "dist_state_enabled": config.dist_state.enabled,
        "dist_state_symbol": config.dist_state.symbol,
        "dist_state_source_id": config.dist_state.source_id,
        "dist_state_timeframes": config.dist_state.timeframes,
        "dist_state_warmup_kline_bars": config.dist_state.warmup_kline_bars,
        "dist_state_warmup_oi_hist_points": config.dist_state.warmup_oi_hist_points,
        "dist_state_ready_core_min_bars": config.dist_state.ready_core_min_bars,
        "dist_state_ready_p_min_deltas": config.dist_state.ready_p_min_deltas,
        "dist_state_p_availability_mode": config.dist_state.p_availability_mode,
        "dist_state_oi_poll_interval_ms": config.dist_state.oi_poll_interval_ms,
        "dist_state_oi_tolerance_ms": config.dist_state.oi_tolerance_ms,
        "dist_state_oi_time_missing_policy": config.dist_state.oi_time_missing_policy,
        "dist_state_oi_verify_enabled": config.dist_state.oi_verify_enabled,
        "dist_state_oi_verify_timeframes": config.dist_state.oi_verify_timeframes,
        "dist_state_oi_verify_timeout_ms": config.dist_state.oi_verify_timeout_ms,
        "dist_state_oi_verify_max_rate_per_min": config.dist_state.oi_verify_max_rate_per_min,
        "dist_state_oi_quality_window_ms": config.dist_state.oi_quality_window_ms,
        "dist_state_oi_seed_points": config.dist_state.oi_seed_points,
        "dist_state_oi_seed_min_points": config.dist_state.oi_seed_min_points,
        "dist_state_v_scale_window_bars": config.dist_state.v_scale_window_bars,
        "dist_state_v_scale_percentile": config.dist_state.v_scale_percentile,
        "dist_state_v_scale_min_samples": config.dist_state.v_scale_min_samples,
        "dist_state_hl_vol_bars": config.dist_state.hl_vol_bars,
        "dist_state_hl_stretch_bars": config.dist_state.hl_stretch_bars,
        "dist_state_hl_oi_bars": config.dist_state.hl_oi_bars,
        "dist_state_hl_atr_short_bars": config.dist_state.hl_atr_short_bars,
        "dist_state_hl_atr_long_bars": config.dist_state.hl_atr_long_bars,
        "dist_state_hl_a_bars": config.dist_state.hl_a_bars,
        "dist_state_k_s": config.dist_state.k_s,
        "dist_state_k_p": config.dist_state.k_p,
        "dist_state_k_t": config.dist_state.k_t,
        "dist_state_tokens_enabled": config.dist_state.tokens_enabled,
        "dist_state_tokens_fail_fast_unknown": config.dist_state.tokens_fail_fast_unknown,
        "dist_state_s_dir_deadband": config.dist_state.s_dir_deadband,
        "dist_state_s_ext_enter": config.dist_state.s_ext_enter,
        "dist_state_s_ext_exit": config.dist_state.s_ext_exit,
        "dist_state_s_revert_min_stretch": config.dist_state.s_revert_min_stretch,
        "dist_state_t_exp_enter": config.dist_state.t_exp_enter,
        "dist_state_t_exp_exit": config.dist_state.t_exp_exit,
        "dist_state_t_comp_enter": config.dist_state.t_comp_enter,
        "dist_state_t_comp_exit": config.dist_state.t_comp_exit,
        "dist_state_a_cont_enter": config.dist_state.a_cont_enter,
        "dist_state_a_cont_exit": config.dist_state.a_cont_exit,
        "dist_state_a_revert_enter": config.dist_state.a_revert_enter,
        "dist_state_a_revert_exit": config.dist_state.a_revert_exit,
        "dist_state_v_low_threshold": config.dist_state.v_low_threshold,
        "dist_state_t_rise_threshold": config.dist_state.t_rise_threshold,
        "dist_state_s_neut_max": config.dist_state.s_neut_max,
        "dist_state_a_neut_max": config.dist_state.a_neut_max,
        "dist_state_t_neut_max": config.dist_state.t_neut_max,
        "dist_state_v_neut_min": config.dist_state.v_neut_min,
        "dist_state_v_neut_max": config.dist_state.v_neut_max,
        "dist_state_t_exp_plus": config.dist_state.t_exp_plus,
        "dist_state_t_exp_plus_plus": config.dist_state.t_exp_plus_plus,
        "dist_state_t_comp_plus": config.dist_state.t_comp_plus,
        "dist_state_t_comp_plus_plus": config.dist_state.t_comp_plus_plus,
        "dist_state_a_cont_plus": config.dist_state.a_cont_plus,
        "dist_state_a_cont_plus_plus": config.dist_state.a_cont_plus_plus,
        "dist_state_a_revert_plus": config.dist_state.a_revert_plus,
        "dist_state_a_revert_plus_plus": config.dist_state.a_revert_plus_plus,
        "dist_state_s_exh_plus": config.dist_state.s_exh_plus,
        "dist_state_s_exh_plus_plus": config.dist_state.s_exh_plus_plus,
        "dist_state_p_confirm_threshold": config.dist_state.p_confirm_threshold,
        "dist_state_token_min_hold_bars_3m": config.dist_state.token_min_hold_bars_3m,
        "dist_state_token_min_hold_bars_15m": config.dist_state.token_min_hold_bars_15m,
        "dist_state_token_min_hold_bars_1h": config.dist_state.token_min_hold_bars_1h,
        "dist_state_token_min_hold_bars_4h": config.dist_state.token_min_hold_bars_4h,
        "dist_state_narrative_enabled": config.dist_state.narrative_enabled,
        "dist_state_narrative_driver_tf": config.dist_state.narrative_driver_tf,
        "dist_state_narrative_linger_reminder_closes": (
            config.dist_state.narrative_linger_reminder_closes
        ),
        "dist_state_narrative_max_chars": config.dist_state.narrative_max_chars,
        "dist_state_narrative_secondary_min_ratio": config.dist_state.narrative_secondary_min_ratio,
        "dist_state_narrative_dir_ratio_min": config.dist_state.narrative_dir_ratio_min,
        "source_registry": source_registry,
    }


def _report_missing(
    runtime: RuntimeState, supervisor: AdapterSupervisor, *, prefix: str
) -> None:
    if supervisor.spot is not None:
        _log_missing_adapter(
            prefix,
            "binance_spot",
            supervisor.spot,
            runtime.symbol_maps.spot_actual_to_base,
        )
    if supervisor.perp is not None:
        _log_missing_adapter(
            prefix,
            "binance_perp",
            supervisor.perp,
            runtime.symbol_maps.perp_actual_to_base,
        )
    if supervisor.coinbase is not None:
        coinbase_actual_to_base = _invert_base_to_actual(runtime.coinbase_base_to_actual)
        _log_missing_adapter(
            prefix,
            "coinbase_spot",
            supervisor.coinbase,
            coinbase_actual_to_base,
        )
    if supervisor.bybit_spot is not None:
        bybit_spot_actual_to_base = _invert_base_to_actual(runtime.bybit_spot_base_to_actual)
        _log_missing_adapter(
            prefix,
            "bybit_spot",
            supervisor.bybit_spot,
            bybit_spot_actual_to_base,
        )
    if supervisor.bybit_perp is not None:
        bybit_perp_actual_to_base = _invert_base_to_actual(runtime.bybit_perp_base_to_actual)
        _log_missing_adapter(
            prefix,
            "bybit_perp",
            supervisor.bybit_perp,
            bybit_perp_actual_to_base,
        )


def _invert_base_to_actual(mapping: dict[str, list[str]]) -> dict[str, str]:
    actual_to_base: dict[str, str] = {}
    for base, actuals in mapping.items():
        for actual in actuals:
            actual_to_base[actual] = base
    return actual_to_base


def _log_missing_adapter(
    prefix: str,
    adapter_name: str,
    adapter: BaseAdapter,
    actual_to_base: dict[str, str],
) -> None:
    missing_actuals = list(adapter.missing_symbols())
    if not missing_actuals:
        return
    missing_bases = [
        actual_to_base.get(actual, actual) for actual in missing_actuals
    ]
    logging.warning(
        "%s %s missing: actual=[%s] base=[%s]",
        prefix,
        adapter_name,
        ",".join(missing_actuals),
        ",".join(missing_bases),
    )


def _legacy_map_to_base(item: AdapterEvent, symbol_maps: SymbolMaps) -> str | None:
    if item.event.source_id.startswith("binance_spot"):
        return symbol_maps.spot_actual_to_base.get(item.symbol)
    return symbol_maps.perp_actual_to_base.get(item.symbol)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _build_tbt_settings(
    runtime: RuntimeState,
    supervisor: AdapterSupervisor,
    fallback_ms: int,
    window_multiplier: float,
) -> tuple[dict[str, int], dict[str, int]]:
    cutoffs: dict[str, int] = {}
    windows: dict[str, int] = {}
    for symbol in runtime.loops:
        spot_actuals = runtime.symbol_maps.spot_base_to_actual.get(symbol, [])
        perp_actuals = runtime.symbol_maps.perp_base_to_actual.get(symbol, [])
        coinbase_actuals = runtime.coinbase_base_to_actual.get(symbol, [])
        bybit_spot_actuals = runtime.bybit_spot_base_to_actual.get(symbol, [])
        bybit_perp_actuals = runtime.bybit_perp_base_to_actual.get(symbol, [])
        tbt_values: list[float] = []
        if supervisor.spot is not None:
            spot_tbt = supervisor.spot.tbt_min(spot_actuals)
            if spot_tbt is not None:
                tbt_values.append(spot_tbt)
        if supervisor.perp is not None:
            perp_tbt = supervisor.perp.tbt_min(perp_actuals)
            if perp_tbt is not None:
                tbt_values.append(perp_tbt)
        if supervisor.coinbase is not None and coinbase_actuals:
            coinbase_tbt = supervisor.coinbase.tbt_min(coinbase_actuals)
            if coinbase_tbt is not None:
                tbt_values.append(coinbase_tbt)
        if supervisor.bybit_spot is not None and bybit_spot_actuals:
            bybit_spot_tbt = supervisor.bybit_spot.tbt_min(bybit_spot_actuals)
            if bybit_spot_tbt is not None:
                tbt_values.append(bybit_spot_tbt)
        if supervisor.bybit_perp is not None and bybit_perp_actuals:
            bybit_perp_tbt = supervisor.bybit_perp.tbt_min(bybit_perp_actuals)
            if bybit_perp_tbt is not None:
                tbt_values.append(bybit_perp_tbt)
        if tbt_values:
            min_tbt = min(tbt_values)
            cutoffs[symbol] = max(1, int(min_tbt))
            windows[symbol] = max(fallback_ms, int(min_tbt * window_multiplier))
        else:
            cutoffs[symbol] = fallback_ms
            windows[symbol] = fallback_ms
    return cutoffs, windows


def _flatten(mapping: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for symbols in mapping.values():
        flattened.extend(symbols)
    return flattened


if __name__ == "__main__":
    main()
