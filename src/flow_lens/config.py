from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, cast


@dataclass(frozen=True)
class AdapterConfig:
    type: str
    symbols: list[str]


AggressorMode = Literal["native", "inferred", "none"]
QuoteMode = Literal["usd_like", "converted", "foreign"]
MarketTypeForX = Literal["spot", "perp"]


@dataclass(frozen=True)
class SourceCapabilities:
    has_size: bool
    has_aggressor: bool
    aggressor_mode: AggressorMode
    quote_mode: QuoteMode


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    venue: str
    instrument_class: str
    market_type_for_x: MarketTypeForX
    price_eligible: bool
    price_priority: int
    capabilities: SourceCapabilities


@dataclass(frozen=True)
class DistStateRuntimeConfig:
    enabled: bool
    symbol: str
    source_id: str
    timeframes: tuple[Literal["3m", "15m", "1h", "4h"], ...]
    warmup_kline_bars: int
    warmup_oi_hist_points: int
    ready_core_min_bars: int
    ready_p_min_deltas: int
    p_availability_mode: Literal["strict", "continuous"]
    oi_poll_interval_ms: int
    oi_tolerance_ms: int
    oi_time_missing_policy: Literal["reject"]
    oi_verify_enabled: bool
    oi_verify_timeframes: tuple[Literal["3m", "15m", "1h", "4h"], ...]
    oi_verify_timeout_ms: int
    oi_verify_max_rate_per_min: int
    oi_quality_window_ms: int
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
    narrative_driver_tf: Literal["3m", "15m", "1h", "4h"]
    narrative_linger_reminder_closes: int
    narrative_max_chars: int
    narrative_secondary_min_ratio: float
    narrative_dir_ratio_min: float


@dataclass(frozen=True)
class AppConfig:
    adapters: Mapping[str, AdapterConfig]
    sources: Mapping[str, SourceConfig]
    tbt_window_multiplier: float
    update_window_seconds: float
    price_selector_policy: Literal["priority_sticky"]
    price_selector_stale_failover_ms: int
    price_selector_recovery_confirm_cycles: int
    price_selector_switch_cooldown_cycles: int
    effort_floor_multiplier: float
    effort_floor_ticks: int
    smoothing_dominance_alpha: float
    smoothing_effectiveness_alpha: float
    dispersion_metric: Literal["hill", "entropy"]
    halo_growth_rate: float
    halo_decay_rate: float
    binning_dot_size_thresholds: tuple[float, float]
    binning_halo_thresholds: tuple[float, float]
    binning_hysteresis_band: float
    tanh_k: float
    scale_window_seconds: float
    persist_enabled: bool
    persist_input: Literal["y_raw", "y_gated", "y"]
    persist_input_deadband: float
    persist_neutral_dir_abs_flash: float
    persist_neutral_dir_abs_persist: float
    persist_tau_eff_active: float
    persist_tau_dir_active: float
    persist_pivot_active_abs: float
    persist_pivot_confirm_s: float
    persist_pivot_neutralize_tau: float
    persist_pivot_neutral_zone_abs: float
    persist_rebuild_confirm_s: float
    persist_pivot_cooldown_s: float
    persist_pivot_max_s: float
    persist_max_delta_s_eff_per_second: float
    persist_tau_dir_pivot: float
    persist_dormant_quiet_abs: float
    persist_dormant_active_abs: float
    persist_dormant_quiet_s: float
    persist_tau_dormant: float
    persist_dormant_effort_norm_threshold: float
    disp_scale_multiplier: float
    disp_scale_percentile: float
    disp_scale_min_samples: int
    disp_scale_floor_percentile: float
    effort_scale_percentile: float
    effort_scale_min_samples: int
    size_scale_percentile: float
    control_baseline_enabled: bool
    control_baseline_target_window_s: float
    control_baseline_target_update_s: float
    control_baseline_breakout_band: float
    control_baseline_confirm_s: float
    control_baseline_exit_band_frac: float
    control_baseline_peg_half_life_s: float
    control_baseline_reanchor_half_life_s: float
    control_baseline_peg_deadband: float
    control_baseline_max_window_samples: int | None
    control_baseline_center_suppress_band: float
    control_baseline_line_hide_warmup_s: float
    control_baseline_midnight_tick_enabled: bool
    control_baseline_midnight_tick_min_samples: int
    control_baseline_midnight_tick_min_elapsed_s: float
    hygiene_enabled: bool
    hygiene_max_excess_wire_lag_ms: int
    hygiene_hard_max_wire_lag_ms: int
    hygiene_wire_lag_baseline_window_s: int
    hygiene_wire_lag_baseline_sample_interval_ms: int
    hygiene_wire_lag_baseline_min_samples: int
    hygiene_wire_lag_baseline_max_samples: int
    hygiene_dedupe_ttl_s: int
    hygiene_log_interval_s: int
    hygiene_future_venue_ts_grace_ms: int
    hygiene_connect_gate_s: int
    hygiene_connect_gate_max_excess_wire_lag_ms: int
    hygiene_connect_gate_hard_max_wire_lag_ms: int
    hygiene_connect_gate_rearm_after_s: int
    tui_min_width: int
    tui_min_height: int
    tui_max_width: int
    tui_max_height: int
    tui_dot_radii: tuple[int, int, int]
    tui_halo_radii: tuple[int, int, int]
    tui_frame_enabled: bool
    tui_frame_inset_px: int
    tui_frame_band_inner: float
    tui_frame_band_outer: float
    tui_show_dev_panel: bool
    dist_state: DistStateRuntimeConfig


def load_app_config(path: Path | str = Path("config/app.toml")) -> AppConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    adapters_section = data.get("adapters", {})
    if not isinstance(adapters_section, dict) or not adapters_section:
        raise ValueError("app.toml must define adapters.")
    sources_section = data.get("sources", {})
    if not isinstance(sources_section, dict) or not sources_section:
        raise ValueError("app.toml must define sources.")
    runtime_section = data.get("runtime", {})
    if not isinstance(runtime_section, dict):
        raise ValueError("runtime config must be a table.")
    tbt_window_multiplier = runtime_section.get("tbt_window_multiplier", 4.0)
    update_window_seconds = runtime_section.get("update_window_seconds", 2.0)
    price_selector_policy = runtime_section.get("price_selector_policy", "priority_sticky")
    price_selector_stale_failover_ms = runtime_section.get("price_selector_stale_failover_ms", 6000)
    price_selector_recovery_confirm_cycles = runtime_section.get(
        "price_selector_recovery_confirm_cycles", 2
    )
    price_selector_switch_cooldown_cycles = runtime_section.get(
        "price_selector_switch_cooldown_cycles", 1
    )
    effort_floor_multiplier = runtime_section.get("effort_floor_multiplier", 0.2)
    effort_floor_ticks = runtime_section.get("effort_floor_ticks", 60)
    smoothing_dominance_alpha = runtime_section.get("smoothing_dominance_alpha", 0.15)
    smoothing_effectiveness_alpha = runtime_section.get("smoothing_effectiveness_alpha", 0.15)
    dispersion_metric = runtime_section.get("dispersion_metric", "hill")
    halo_growth_rate = runtime_section.get("halo_growth_rate", 0.10)
    halo_decay_rate = runtime_section.get("halo_decay_rate", 0.5)
    binning_dot_size_thresholds = runtime_section.get(
        "binning_dot_size_thresholds", (0.35, 0.70)
    )
    binning_halo_thresholds = runtime_section.get(
        "binning_halo_thresholds", (0.33, 0.66)
    )
    binning_hysteresis_band = runtime_section.get("binning_hysteresis_band", 0.05)
    tanh_k = runtime_section.get("tanh_k", 500.0)
    scale_window_seconds = runtime_section.get("scale_window_seconds", 600.0)
    persist_enabled = runtime_section.get("persist_enabled", True)
    persist_input = runtime_section.get("persist_input", "y_gated")
    persist_input_deadband = runtime_section.get("persist_input_deadband", 0.0)
    persist_neutral_dir_abs_flash = runtime_section.get("persist_neutral_dir_abs_flash", 0.05)
    persist_neutral_dir_abs_persist = runtime_section.get("persist_neutral_dir_abs_persist", 0.05)
    persist_tau_eff_active = runtime_section.get("persist_tau_eff_active", 18.0)
    persist_tau_dir_active = runtime_section.get("persist_tau_dir_active", 12.0)
    persist_pivot_active_abs = runtime_section.get("persist_pivot_active_abs", 0.10)
    persist_pivot_confirm_s = runtime_section.get("persist_pivot_confirm_s", 6.0)
    persist_pivot_neutralize_tau = runtime_section.get("persist_pivot_neutralize_tau", 3.0)
    persist_pivot_neutral_zone_abs = runtime_section.get("persist_pivot_neutral_zone_abs", 0.08)
    persist_rebuild_confirm_s = runtime_section.get("persist_rebuild_confirm_s", 4.0)
    persist_pivot_cooldown_s = runtime_section.get("persist_pivot_cooldown_s", 10.0)
    persist_pivot_max_s = runtime_section.get("persist_pivot_max_s", 18.0)
    persist_max_delta_s_eff_per_second = runtime_section.get(
        "persist_max_delta_s_eff_per_second", 0.4
    )
    persist_tau_dir_pivot = runtime_section.get("persist_tau_dir_pivot", 4.0)
    persist_dormant_quiet_abs = runtime_section.get("persist_dormant_quiet_abs", 0.04)
    persist_dormant_active_abs = runtime_section.get("persist_dormant_active_abs", 0.08)
    persist_dormant_quiet_s = runtime_section.get("persist_dormant_quiet_s", 20.0)
    persist_tau_dormant = runtime_section.get("persist_tau_dormant", 45.0)
    persist_dormant_effort_norm_threshold = runtime_section.get(
        "persist_dormant_effort_norm_threshold", 0.35
    )
    disp_scale_multiplier = runtime_section.get("disp_scale_multiplier", 0.25)
    disp_scale_percentile = runtime_section.get("disp_scale_percentile", 0.75)
    disp_scale_min_samples = runtime_section.get("disp_scale_min_samples", 20)
    disp_scale_floor_percentile = runtime_section.get("disp_scale_floor_percentile", 0.1)
    effort_scale_percentile = runtime_section.get("effort_scale_percentile", 0.5)
    effort_scale_min_samples = runtime_section.get("effort_scale_min_samples", 20)
    size_scale_percentile = runtime_section.get("size_scale_percentile", effort_scale_percentile)
    control_baseline_enabled = runtime_section.get("control_baseline_enabled", True)
    control_baseline_target_window_s = runtime_section.get(
        "control_baseline_target_window_s", 1800.0
    )
    control_baseline_target_update_s = runtime_section.get(
        "control_baseline_target_update_s", 10.0
    )
    control_baseline_breakout_band = runtime_section.get("control_baseline_breakout_band", 0.06)
    control_baseline_confirm_s = runtime_section.get("control_baseline_confirm_s", 30.0)
    control_baseline_exit_band_frac = runtime_section.get(
        "control_baseline_exit_band_frac", 0.50
    )
    control_baseline_peg_half_life_s = runtime_section.get(
        "control_baseline_peg_half_life_s", 7200.0
    )
    control_baseline_reanchor_half_life_s = runtime_section.get(
        "control_baseline_reanchor_half_life_s", 180.0
    )
    control_baseline_peg_deadband = runtime_section.get("control_baseline_peg_deadband", 0.015)
    control_baseline_max_window_samples = runtime_section.get(
        "control_baseline_max_window_samples", None
    )
    control_baseline_center_suppress_band = runtime_section.get(
        "control_baseline_center_suppress_band", 0.02
    )
    control_baseline_line_hide_warmup_s = runtime_section.get(
        "control_baseline_line_hide_warmup_s", 120.0
    )
    control_baseline_midnight_tick_enabled = runtime_section.get(
        "control_baseline_midnight_tick_enabled", True
    )
    control_baseline_midnight_tick_min_samples = runtime_section.get(
        "control_baseline_midnight_tick_min_samples", 60
    )
    control_baseline_midnight_tick_min_elapsed_s = runtime_section.get(
        "control_baseline_midnight_tick_min_elapsed_s", 600.0
    )
    hygiene_section = runtime_section.get("hygiene", {})
    if not isinstance(hygiene_section, dict):
        raise ValueError("runtime.hygiene must be a table.")
    hygiene_enabled = hygiene_section.get("enabled", True)
    hygiene_max_excess_wire_lag_ms = hygiene_section.get(
        "max_excess_wire_lag_ms",
        hygiene_section.get("max_wire_lag_ms", 2000),
    )
    hygiene_hard_max_wire_lag_ms = hygiene_section.get("hard_max_wire_lag_ms", 30000)
    hygiene_wire_lag_baseline_window_s = hygiene_section.get("wire_lag_baseline_window_s", 300)
    hygiene_wire_lag_baseline_sample_interval_ms = hygiene_section.get(
        "wire_lag_baseline_sample_interval_ms", 200
    )
    hygiene_wire_lag_baseline_min_samples = hygiene_section.get(
        "wire_lag_baseline_min_samples", 30
    )
    hygiene_wire_lag_baseline_max_samples = hygiene_section.get(
        "wire_lag_baseline_max_samples", 2000
    )
    hygiene_dedupe_ttl_s = hygiene_section.get("dedupe_ttl_s", 30)
    hygiene_log_interval_s = hygiene_section.get("log_interval_s", 10)
    hygiene_future_venue_ts_grace_ms = hygiene_section.get("future_venue_ts_grace_ms", 250)
    hygiene_connect_gate_s = hygiene_section.get("connect_gate_s", 0)
    hygiene_connect_gate_max_excess_wire_lag_ms = hygiene_section.get(
        "connect_gate_max_excess_wire_lag_ms",
        hygiene_section.get("connect_gate_max_wire_lag_ms", 500),
    )
    hygiene_connect_gate_hard_max_wire_lag_ms = hygiene_section.get(
        "connect_gate_hard_max_wire_lag_ms", 5000
    )
    hygiene_connect_gate_rearm_after_s = hygiene_section.get("connect_gate_rearm_after_s", 60)
    tui_min_width = runtime_section.get("tui_min_width", 41)
    tui_min_height = runtime_section.get("tui_min_height", 17)
    tui_max_width = runtime_section.get("tui_max_width", 81)
    tui_max_height = runtime_section.get("tui_max_height", 33)
    tui_dot_radii = runtime_section.get("tui_dot_radii", (1, 2, 4))
    tui_halo_radii = runtime_section.get("tui_halo_radii", (0, 6, 9))
    tui_frame_enabled = runtime_section.get("tui_frame_enabled", True)
    tui_frame_inset_px = runtime_section.get("tui_frame_inset_px", 1)
    tui_frame_band_inner = runtime_section.get("tui_frame_band_inner", 0.995)
    tui_frame_band_outer = runtime_section.get("tui_frame_band_outer", 1.005)
    tui_show_dev_panel = runtime_section.get("tui_show_dev_panel", True)
    dist_state_section = runtime_section.get("dist_state", {})
    if not isinstance(dist_state_section, dict):
        raise ValueError("runtime.dist_state must be a table.")
    dist_state_enabled = dist_state_section.get("enabled", False)
    dist_state_symbol = dist_state_section.get("symbol", "BTC")
    dist_state_source_id = dist_state_section.get("source_id", "binance_perp")
    dist_state_timeframes = dist_state_section.get("timeframes", ["3m", "15m", "1h", "4h"])
    dist_state_warmup_kline_bars = dist_state_section.get("warmup_kline_bars", 200)
    dist_state_warmup_oi_hist_points = dist_state_section.get("warmup_oi_hist_points", 200)
    dist_state_ready_core_min_bars = dist_state_section.get("ready_core_min_bars", 30)
    dist_state_ready_p_min_deltas = dist_state_section.get("ready_p_min_deltas", 10)
    dist_state_p_availability_mode = dist_state_section.get("p_availability_mode", "strict")
    dist_state_oi_poll_interval_ms = dist_state_section.get("oi_poll_interval_ms", 1000)
    dist_state_oi_tolerance_ms = dist_state_section.get("oi_tolerance_ms", 7000)
    dist_state_oi_time_missing_policy = dist_state_section.get("oi_time_missing_policy", "reject")
    dist_state_oi_verify_enabled = dist_state_section.get("oi_verify_enabled", True)
    dist_state_oi_verify_timeframes = dist_state_section.get(
        "oi_verify_timeframes", ["3m", "15m", "1h", "4h"]
    )
    dist_state_oi_verify_timeout_ms = dist_state_section.get("oi_verify_timeout_ms", 1200)
    dist_state_oi_verify_max_rate_per_min = dist_state_section.get(
        "oi_verify_max_rate_per_min", 24
    )
    dist_state_oi_quality_window_ms = dist_state_section.get("oi_quality_window_ms", 15_000)
    dist_state_oi_seed_points = dist_state_section.get(
        "oi_seed_points",
        dist_state_warmup_oi_hist_points,
    )
    dist_state_oi_seed_min_points = dist_state_section.get("oi_seed_min_points", 30)
    dist_state_v_scale_window_bars = dist_state_section.get("v_scale_window_bars", 200)
    dist_state_v_scale_percentile = dist_state_section.get("v_scale_percentile", 0.80)
    dist_state_v_scale_min_samples = dist_state_section.get("v_scale_min_samples", 30)
    dist_state_hl_vol_bars = dist_state_section.get("hl_vol_bars", 20.0)
    dist_state_hl_stretch_bars = dist_state_section.get("hl_stretch_bars", 20.0)
    dist_state_hl_oi_bars = dist_state_section.get("hl_oi_bars", 20.0)
    dist_state_hl_atr_short_bars = dist_state_section.get("hl_atr_short_bars", 10.0)
    dist_state_hl_atr_long_bars = dist_state_section.get("hl_atr_long_bars", 40.0)
    dist_state_hl_a_bars = dist_state_section.get("hl_a_bars", 20.0)
    dist_state_k_s = dist_state_section.get("k_s", 0.6)
    dist_state_k_p = dist_state_section.get("k_p", 0.6)
    dist_state_k_t = dist_state_section.get("k_t", 1.0)
    dist_state_tokens_enabled = dist_state_section.get("tokens_enabled", False)
    dist_state_tokens_fail_fast_unknown = dist_state_section.get("tokens_fail_fast_unknown", False)
    dist_state_s_dir_deadband = dist_state_section.get("s_dir_deadband", 0.10)
    dist_state_s_ext_enter = dist_state_section.get("s_ext_enter", 0.60)
    dist_state_s_ext_exit = dist_state_section.get("s_ext_exit", 0.45)
    dist_state_s_revert_min_stretch = dist_state_section.get("s_revert_min_stretch", 0.20)
    dist_state_t_exp_enter = dist_state_section.get("t_exp_enter", 0.40)
    dist_state_t_exp_exit = dist_state_section.get("t_exp_exit", 0.25)
    dist_state_t_comp_enter = dist_state_section.get("t_comp_enter", -0.40)
    dist_state_t_comp_exit = dist_state_section.get("t_comp_exit", -0.25)
    dist_state_a_cont_enter = dist_state_section.get("a_cont_enter", 0.35)
    dist_state_a_cont_exit = dist_state_section.get("a_cont_exit", 0.20)
    dist_state_a_revert_enter = dist_state_section.get("a_revert_enter", -0.35)
    dist_state_a_revert_exit = dist_state_section.get("a_revert_exit", -0.20)
    dist_state_v_low_threshold = dist_state_section.get("v_low_threshold", 0.25)
    dist_state_t_rise_threshold = dist_state_section.get("t_rise_threshold", 0.05)
    dist_state_s_neut_max = dist_state_section.get("s_neut_max", 0.12)
    dist_state_a_neut_max = dist_state_section.get("a_neut_max", 0.12)
    dist_state_t_neut_max = dist_state_section.get("t_neut_max", 0.12)
    dist_state_v_neut_min = dist_state_section.get("v_neut_min", 0.30)
    dist_state_v_neut_max = dist_state_section.get("v_neut_max", 0.70)
    dist_state_t_exp_plus = dist_state_section.get("t_exp_plus", 0.60)
    dist_state_t_exp_plus_plus = dist_state_section.get("t_exp_plus_plus", 0.80)
    dist_state_t_comp_plus = dist_state_section.get("t_comp_plus", -0.60)
    dist_state_t_comp_plus_plus = dist_state_section.get("t_comp_plus_plus", -0.80)
    dist_state_a_cont_plus = dist_state_section.get("a_cont_plus", 0.55)
    dist_state_a_cont_plus_plus = dist_state_section.get("a_cont_plus_plus", 0.75)
    dist_state_a_revert_plus = dist_state_section.get("a_revert_plus", -0.55)
    dist_state_a_revert_plus_plus = dist_state_section.get("a_revert_plus_plus", -0.75)
    dist_state_s_exh_plus = dist_state_section.get("s_exh_plus", 0.70)
    dist_state_s_exh_plus_plus = dist_state_section.get("s_exh_plus_plus", 0.85)
    dist_state_p_confirm_threshold = dist_state_section.get("p_confirm_threshold", 0.25)
    dist_state_token_min_hold_bars_3m = dist_state_section.get("token_min_hold_bars_3m", 2)
    dist_state_token_min_hold_bars_15m = dist_state_section.get("token_min_hold_bars_15m", 2)
    dist_state_token_min_hold_bars_1h = dist_state_section.get("token_min_hold_bars_1h", 1)
    dist_state_token_min_hold_bars_4h = dist_state_section.get("token_min_hold_bars_4h", 1)
    dist_state_narrative_enabled = dist_state_section.get("narrative_enabled", False)
    dist_state_narrative_driver_tf = dist_state_section.get("narrative_driver_tf", "15m")
    dist_state_narrative_linger_reminder_closes = dist_state_section.get(
        "narrative_linger_reminder_closes", 0
    )
    dist_state_narrative_max_chars = dist_state_section.get("narrative_max_chars", 72)
    dist_state_narrative_secondary_min_ratio = dist_state_section.get(
        "narrative_secondary_min_ratio", 0.50
    )
    dist_state_narrative_dir_ratio_min = dist_state_section.get("narrative_dir_ratio_min", 0.20)
    if not isinstance(tbt_window_multiplier, (int, float)):
        raise ValueError("runtime.tbt_window_multiplier must be a number.")
    if not isinstance(update_window_seconds, (int, float)):
        raise ValueError("runtime.update_window_seconds must be a number.")
    if not isinstance(price_selector_policy, str):
        raise ValueError("runtime.price_selector_policy must be a string.")
    if price_selector_policy != "priority_sticky":
        raise ValueError("runtime.price_selector_policy must be 'priority_sticky'.")
    if not isinstance(price_selector_stale_failover_ms, int):
        raise ValueError("runtime.price_selector_stale_failover_ms must be an integer.")
    if not isinstance(price_selector_recovery_confirm_cycles, int):
        raise ValueError("runtime.price_selector_recovery_confirm_cycles must be an integer.")
    if not isinstance(price_selector_switch_cooldown_cycles, int):
        raise ValueError("runtime.price_selector_switch_cooldown_cycles must be an integer.")
    if price_selector_stale_failover_ms <= 0:
        raise ValueError("runtime.price_selector_stale_failover_ms must be > 0.")
    if price_selector_recovery_confirm_cycles <= 0:
        raise ValueError("runtime.price_selector_recovery_confirm_cycles must be > 0.")
    if price_selector_switch_cooldown_cycles < 0:
        raise ValueError("runtime.price_selector_switch_cooldown_cycles must be >= 0.")
    if not isinstance(effort_floor_multiplier, (int, float)):
        raise ValueError("runtime.effort_floor_multiplier must be a number.")
    if not isinstance(effort_floor_ticks, int):
        raise ValueError("runtime.effort_floor_ticks must be an integer.")
    if not isinstance(smoothing_dominance_alpha, (int, float)):
        raise ValueError("runtime.smoothing_dominance_alpha must be a number.")
    if not isinstance(smoothing_effectiveness_alpha, (int, float)):
        raise ValueError("runtime.smoothing_effectiveness_alpha must be a number.")
    if not isinstance(dispersion_metric, str):
        raise ValueError("runtime.dispersion_metric must be a string.")
    if not isinstance(halo_growth_rate, (int, float)):
        raise ValueError("runtime.halo_growth_rate must be a number.")
    if not isinstance(halo_decay_rate, (int, float)):
        raise ValueError("runtime.halo_decay_rate must be a number.")
    if not isinstance(binning_hysteresis_band, (int, float)):
        raise ValueError("runtime.binning_hysteresis_band must be a number.")
    if not isinstance(tanh_k, (int, float)):
        raise ValueError("runtime.tanh_k must be a number.")
    if not isinstance(scale_window_seconds, (int, float)):
        raise ValueError("runtime.scale_window_seconds must be a number.")
    if not isinstance(persist_enabled, bool):
        raise ValueError("runtime.persist_enabled must be a boolean.")
    if not isinstance(persist_input, str):
        raise ValueError("runtime.persist_input must be a string.")
    if persist_input not in {"y_raw", "y_gated", "y"}:
        raise ValueError("runtime.persist_input must be one of: y_raw, y_gated, y.")
    if not isinstance(persist_input_deadband, (int, float)):
        raise ValueError("runtime.persist_input_deadband must be a number.")
    if float(persist_input_deadband) < 0:
        raise ValueError("runtime.persist_input_deadband must be >= 0.")
    if not isinstance(persist_neutral_dir_abs_flash, (int, float)):
        raise ValueError("runtime.persist_neutral_dir_abs_flash must be a number.")
    if not isinstance(persist_neutral_dir_abs_persist, (int, float)):
        raise ValueError("runtime.persist_neutral_dir_abs_persist must be a number.")
    if not isinstance(persist_tau_eff_active, (int, float)):
        raise ValueError("runtime.persist_tau_eff_active must be a number.")
    if not isinstance(persist_tau_dir_active, (int, float)):
        raise ValueError("runtime.persist_tau_dir_active must be a number.")
    if not isinstance(persist_pivot_active_abs, (int, float)):
        raise ValueError("runtime.persist_pivot_active_abs must be a number.")
    if not isinstance(persist_pivot_confirm_s, (int, float)):
        raise ValueError("runtime.persist_pivot_confirm_s must be a number.")
    if not isinstance(persist_pivot_neutralize_tau, (int, float)):
        raise ValueError("runtime.persist_pivot_neutralize_tau must be a number.")
    if not isinstance(persist_pivot_neutral_zone_abs, (int, float)):
        raise ValueError("runtime.persist_pivot_neutral_zone_abs must be a number.")
    if not isinstance(persist_rebuild_confirm_s, (int, float)):
        raise ValueError("runtime.persist_rebuild_confirm_s must be a number.")
    if not isinstance(persist_pivot_cooldown_s, (int, float)):
        raise ValueError("runtime.persist_pivot_cooldown_s must be a number.")
    if not isinstance(persist_pivot_max_s, (int, float)):
        raise ValueError("runtime.persist_pivot_max_s must be a number.")
    if not isinstance(persist_max_delta_s_eff_per_second, (int, float)):
        raise ValueError("runtime.persist_max_delta_s_eff_per_second must be a number.")
    if not isinstance(persist_tau_dir_pivot, (int, float)):
        raise ValueError("runtime.persist_tau_dir_pivot must be a number.")
    if not isinstance(persist_dormant_quiet_abs, (int, float)):
        raise ValueError("runtime.persist_dormant_quiet_abs must be a number.")
    if not isinstance(persist_dormant_active_abs, (int, float)):
        raise ValueError("runtime.persist_dormant_active_abs must be a number.")
    if not isinstance(persist_dormant_quiet_s, (int, float)):
        raise ValueError("runtime.persist_dormant_quiet_s must be a number.")
    if not isinstance(persist_tau_dormant, (int, float)):
        raise ValueError("runtime.persist_tau_dormant must be a number.")
    if not isinstance(persist_dormant_effort_norm_threshold, (int, float)):
        raise ValueError("runtime.persist_dormant_effort_norm_threshold must be a number.")
    if float(persist_neutral_dir_abs_flash) < 0 or float(persist_neutral_dir_abs_flash) >= 1:
        raise ValueError("runtime.persist_neutral_dir_abs_flash must be between 0 and 1.")
    if (
        float(persist_neutral_dir_abs_persist) < 0
        or float(persist_neutral_dir_abs_persist) >= 1
    ):
        raise ValueError("runtime.persist_neutral_dir_abs_persist must be between 0 and 1.")
    if float(persist_tau_eff_active) <= 0:
        raise ValueError("runtime.persist_tau_eff_active must be > 0.")
    if float(persist_tau_dir_active) <= 0:
        raise ValueError("runtime.persist_tau_dir_active must be > 0.")
    if float(persist_pivot_active_abs) < 0 or float(persist_pivot_active_abs) > 1:
        raise ValueError("runtime.persist_pivot_active_abs must be between 0 and 1.")
    if float(persist_pivot_confirm_s) <= 0:
        raise ValueError("runtime.persist_pivot_confirm_s must be > 0.")
    if float(persist_pivot_neutralize_tau) <= 0:
        raise ValueError("runtime.persist_pivot_neutralize_tau must be > 0.")
    if float(persist_pivot_neutral_zone_abs) < 0 or float(persist_pivot_neutral_zone_abs) > 1:
        raise ValueError("runtime.persist_pivot_neutral_zone_abs must be between 0 and 1.")
    if float(persist_rebuild_confirm_s) <= 0:
        raise ValueError("runtime.persist_rebuild_confirm_s must be > 0.")
    if float(persist_pivot_cooldown_s) < 0:
        raise ValueError("runtime.persist_pivot_cooldown_s must be >= 0.")
    if float(persist_pivot_max_s) <= 0:
        raise ValueError("runtime.persist_pivot_max_s must be > 0.")
    if float(persist_max_delta_s_eff_per_second) < 0:
        raise ValueError("runtime.persist_max_delta_s_eff_per_second must be >= 0.")
    if float(persist_tau_dir_pivot) <= 0:
        raise ValueError("runtime.persist_tau_dir_pivot must be > 0.")
    if float(persist_dormant_quiet_abs) < 0 or float(persist_dormant_quiet_abs) > 1:
        raise ValueError("runtime.persist_dormant_quiet_abs must be between 0 and 1.")
    if float(persist_dormant_active_abs) < 0 or float(persist_dormant_active_abs) > 1:
        raise ValueError("runtime.persist_dormant_active_abs must be between 0 and 1.")
    if float(persist_dormant_active_abs) < float(persist_dormant_quiet_abs):
        raise ValueError(
            "runtime.persist_dormant_active_abs must be >= runtime.persist_dormant_quiet_abs."
        )
    if float(persist_dormant_quiet_s) <= 0:
        raise ValueError("runtime.persist_dormant_quiet_s must be > 0.")
    if float(persist_tau_dormant) <= 0:
        raise ValueError("runtime.persist_tau_dormant must be > 0.")
    if float(persist_dormant_effort_norm_threshold) < 0:
        raise ValueError("runtime.persist_dormant_effort_norm_threshold must be >= 0.")
    if float(persist_pivot_max_s) < float(persist_pivot_confirm_s):
        raise ValueError("runtime.persist_pivot_max_s must be >= runtime.persist_pivot_confirm_s.")
    if not isinstance(disp_scale_multiplier, (int, float)):
        raise ValueError("runtime.disp_scale_multiplier must be a number.")
    if not isinstance(disp_scale_percentile, (int, float)):
        raise ValueError("runtime.disp_scale_percentile must be a number.")
    if not isinstance(disp_scale_min_samples, int):
        raise ValueError("runtime.disp_scale_min_samples must be an integer.")
    if not isinstance(disp_scale_floor_percentile, (int, float)):
        raise ValueError("runtime.disp_scale_floor_percentile must be a number.")
    if disp_scale_percentile <= 0 or disp_scale_percentile >= 1:
        raise ValueError("runtime.disp_scale_percentile must be between 0 and 1.")
    if disp_scale_floor_percentile < 0 or disp_scale_floor_percentile >= 1:
        raise ValueError("runtime.disp_scale_floor_percentile must be between 0 and 1.")
    if disp_scale_min_samples <= 0:
        raise ValueError("runtime.disp_scale_min_samples must be > 0.")
    if not isinstance(effort_scale_percentile, (int, float)):
        raise ValueError("runtime.effort_scale_percentile must be a number.")
    if not isinstance(effort_scale_min_samples, int):
        raise ValueError("runtime.effort_scale_min_samples must be an integer.")
    if not isinstance(size_scale_percentile, (int, float)):
        raise ValueError("runtime.size_scale_percentile must be a number.")
    if effort_scale_percentile <= 0 or effort_scale_percentile >= 1:
        raise ValueError("runtime.effort_scale_percentile must be between 0 and 1.")
    if size_scale_percentile <= 0 or size_scale_percentile >= 1:
        raise ValueError("runtime.size_scale_percentile must be between 0 and 1.")
    if effort_scale_min_samples <= 0:
        raise ValueError("runtime.effort_scale_min_samples must be > 0.")
    if not isinstance(control_baseline_enabled, bool):
        raise ValueError("runtime.control_baseline_enabled must be a boolean.")
    if not isinstance(control_baseline_target_window_s, (int, float)):
        raise ValueError("runtime.control_baseline_target_window_s must be a number.")
    if float(control_baseline_target_window_s) <= 0:
        raise ValueError("runtime.control_baseline_target_window_s must be > 0.")
    if not isinstance(control_baseline_target_update_s, (int, float)):
        raise ValueError("runtime.control_baseline_target_update_s must be a number.")
    if float(control_baseline_target_update_s) <= 0:
        raise ValueError("runtime.control_baseline_target_update_s must be > 0.")
    if float(control_baseline_target_update_s) > float(control_baseline_target_window_s):
        raise ValueError(
            "runtime.control_baseline_target_update_s must be <= runtime.control_baseline_target_window_s."
        )
    if not isinstance(control_baseline_breakout_band, (int, float)):
        raise ValueError("runtime.control_baseline_breakout_band must be a number.")
    if float(control_baseline_breakout_band) < 0 or float(control_baseline_breakout_band) > 1:
        raise ValueError("runtime.control_baseline_breakout_band must be between 0 and 1.")
    if not isinstance(control_baseline_confirm_s, (int, float)):
        raise ValueError("runtime.control_baseline_confirm_s must be a number.")
    if float(control_baseline_confirm_s) <= 0:
        raise ValueError("runtime.control_baseline_confirm_s must be > 0.")
    if not isinstance(control_baseline_exit_band_frac, (int, float)):
        raise ValueError("runtime.control_baseline_exit_band_frac must be a number.")
    if float(control_baseline_exit_band_frac) <= 0 or float(control_baseline_exit_band_frac) >= 1:
        raise ValueError("runtime.control_baseline_exit_band_frac must be between 0 and 1.")
    if not isinstance(control_baseline_peg_half_life_s, (int, float)):
        raise ValueError("runtime.control_baseline_peg_half_life_s must be a number.")
    if float(control_baseline_peg_half_life_s) <= 0:
        raise ValueError("runtime.control_baseline_peg_half_life_s must be > 0.")
    if not isinstance(control_baseline_reanchor_half_life_s, (int, float)):
        raise ValueError("runtime.control_baseline_reanchor_half_life_s must be a number.")
    if float(control_baseline_reanchor_half_life_s) <= 0:
        raise ValueError("runtime.control_baseline_reanchor_half_life_s must be > 0.")
    if float(control_baseline_reanchor_half_life_s) > float(control_baseline_peg_half_life_s):
        raise ValueError(
            "runtime.control_baseline_reanchor_half_life_s must be <= runtime.control_baseline_peg_half_life_s."
        )
    if not isinstance(control_baseline_peg_deadband, (int, float)):
        raise ValueError("runtime.control_baseline_peg_deadband must be a number.")
    if float(control_baseline_peg_deadband) < 0 or float(control_baseline_peg_deadband) > 1:
        raise ValueError("runtime.control_baseline_peg_deadband must be between 0 and 1.")
    if control_baseline_max_window_samples is not None and (
        not isinstance(control_baseline_max_window_samples, int)
        or control_baseline_max_window_samples <= 0
    ):
        raise ValueError(
            "runtime.control_baseline_max_window_samples must be a positive integer or omitted."
        )
    if not isinstance(control_baseline_center_suppress_band, (int, float)):
        raise ValueError("runtime.control_baseline_center_suppress_band must be a number.")
    if (
        float(control_baseline_center_suppress_band) < 0
        or float(control_baseline_center_suppress_band) > 1
    ):
        raise ValueError("runtime.control_baseline_center_suppress_band must be between 0 and 1.")
    if not isinstance(control_baseline_line_hide_warmup_s, (int, float)):
        raise ValueError("runtime.control_baseline_line_hide_warmup_s must be a number.")
    if float(control_baseline_line_hide_warmup_s) < 0:
        raise ValueError("runtime.control_baseline_line_hide_warmup_s must be >= 0.")
    if not isinstance(control_baseline_midnight_tick_enabled, bool):
        raise ValueError("runtime.control_baseline_midnight_tick_enabled must be a boolean.")
    if not isinstance(control_baseline_midnight_tick_min_samples, int):
        raise ValueError("runtime.control_baseline_midnight_tick_min_samples must be an integer.")
    if control_baseline_midnight_tick_min_samples <= 0:
        raise ValueError("runtime.control_baseline_midnight_tick_min_samples must be > 0.")
    if not isinstance(control_baseline_midnight_tick_min_elapsed_s, (int, float)):
        raise ValueError("runtime.control_baseline_midnight_tick_min_elapsed_s must be a number.")
    if float(control_baseline_midnight_tick_min_elapsed_s) < 0:
        raise ValueError("runtime.control_baseline_midnight_tick_min_elapsed_s must be >= 0.")
    if not isinstance(hygiene_enabled, bool):
        raise ValueError("runtime.hygiene.enabled must be a boolean.")
    if not isinstance(hygiene_max_excess_wire_lag_ms, int):
        raise ValueError("runtime.hygiene.max_excess_wire_lag_ms must be an integer.")
    if hygiene_max_excess_wire_lag_ms <= 0:
        raise ValueError("runtime.hygiene.max_excess_wire_lag_ms must be > 0.")
    if not isinstance(hygiene_hard_max_wire_lag_ms, int):
        raise ValueError("runtime.hygiene.hard_max_wire_lag_ms must be an integer.")
    if hygiene_hard_max_wire_lag_ms <= 0:
        raise ValueError("runtime.hygiene.hard_max_wire_lag_ms must be > 0.")
    if not isinstance(hygiene_wire_lag_baseline_window_s, int):
        raise ValueError("runtime.hygiene.wire_lag_baseline_window_s must be an integer.")
    if hygiene_wire_lag_baseline_window_s <= 0:
        raise ValueError("runtime.hygiene.wire_lag_baseline_window_s must be > 0.")
    if not isinstance(hygiene_wire_lag_baseline_sample_interval_ms, int):
        raise ValueError(
            "runtime.hygiene.wire_lag_baseline_sample_interval_ms must be an integer."
        )
    if hygiene_wire_lag_baseline_sample_interval_ms <= 0:
        raise ValueError("runtime.hygiene.wire_lag_baseline_sample_interval_ms must be > 0.")
    if not isinstance(hygiene_wire_lag_baseline_min_samples, int):
        raise ValueError("runtime.hygiene.wire_lag_baseline_min_samples must be an integer.")
    if hygiene_wire_lag_baseline_min_samples <= 0:
        raise ValueError("runtime.hygiene.wire_lag_baseline_min_samples must be > 0.")
    if not isinstance(hygiene_wire_lag_baseline_max_samples, int):
        raise ValueError("runtime.hygiene.wire_lag_baseline_max_samples must be an integer.")
    if hygiene_wire_lag_baseline_max_samples <= 0:
        raise ValueError("runtime.hygiene.wire_lag_baseline_max_samples must be > 0.")
    if not isinstance(hygiene_dedupe_ttl_s, int):
        raise ValueError("runtime.hygiene.dedupe_ttl_s must be an integer.")
    if hygiene_dedupe_ttl_s <= 0:
        raise ValueError("runtime.hygiene.dedupe_ttl_s must be > 0.")
    if not isinstance(hygiene_log_interval_s, int):
        raise ValueError("runtime.hygiene.log_interval_s must be an integer.")
    if hygiene_log_interval_s <= 0:
        raise ValueError("runtime.hygiene.log_interval_s must be > 0.")
    if not isinstance(hygiene_future_venue_ts_grace_ms, int):
        raise ValueError("runtime.hygiene.future_venue_ts_grace_ms must be an integer.")
    if hygiene_future_venue_ts_grace_ms < 0:
        raise ValueError("runtime.hygiene.future_venue_ts_grace_ms must be >= 0.")
    if not isinstance(hygiene_connect_gate_s, int):
        raise ValueError("runtime.hygiene.connect_gate_s must be an integer.")
    if hygiene_connect_gate_s < 0:
        raise ValueError("runtime.hygiene.connect_gate_s must be >= 0.")
    if not isinstance(hygiene_connect_gate_max_excess_wire_lag_ms, int):
        raise ValueError(
            "runtime.hygiene.connect_gate_max_excess_wire_lag_ms must be an integer."
        )
    if hygiene_connect_gate_max_excess_wire_lag_ms <= 0:
        raise ValueError("runtime.hygiene.connect_gate_max_excess_wire_lag_ms must be > 0.")
    if not isinstance(hygiene_connect_gate_hard_max_wire_lag_ms, int):
        raise ValueError("runtime.hygiene.connect_gate_hard_max_wire_lag_ms must be an integer.")
    if hygiene_connect_gate_hard_max_wire_lag_ms <= 0:
        raise ValueError("runtime.hygiene.connect_gate_hard_max_wire_lag_ms must be > 0.")
    if not isinstance(hygiene_connect_gate_rearm_after_s, int):
        raise ValueError("runtime.hygiene.connect_gate_rearm_after_s must be an integer.")
    if hygiene_connect_gate_rearm_after_s < 0:
        raise ValueError("runtime.hygiene.connect_gate_rearm_after_s must be >= 0.")
    if not isinstance(tui_min_width, int):
        raise ValueError("runtime.tui_min_width must be an integer.")
    if not isinstance(tui_min_height, int):
        raise ValueError("runtime.tui_min_height must be an integer.")
    if not isinstance(tui_max_width, int):
        raise ValueError("runtime.tui_max_width must be an integer.")
    if not isinstance(tui_max_height, int):
        raise ValueError("runtime.tui_max_height must be an integer.")
    if tui_min_width < 15 or tui_min_height < 9:
        raise ValueError("runtime tui minimum dimensions are too small.")
    if tui_max_width < tui_min_width or tui_max_height < tui_min_height:
        raise ValueError("runtime tui max dimensions must be >= min dimensions.")
    dot_radii = _parse_int_triplet(tui_dot_radii, "runtime.tui_dot_radii")
    halo_radii = _parse_int_triplet(tui_halo_radii, "runtime.tui_halo_radii")
    if not isinstance(tui_frame_enabled, bool):
        raise ValueError("runtime.tui_frame_enabled must be a boolean.")
    if not isinstance(tui_frame_inset_px, int):
        raise ValueError("runtime.tui_frame_inset_px must be an integer.")
    if tui_frame_inset_px < 0:
        raise ValueError("runtime.tui_frame_inset_px must be >= 0.")
    if not isinstance(tui_frame_band_inner, (int, float)):
        raise ValueError("runtime.tui_frame_band_inner must be a number.")
    if not isinstance(tui_frame_band_outer, (int, float)):
        raise ValueError("runtime.tui_frame_band_outer must be a number.")
    if float(tui_frame_band_inner) <= 0:
        raise ValueError("runtime.tui_frame_band_inner must be > 0.")
    if float(tui_frame_band_outer) <= float(tui_frame_band_inner):
        raise ValueError("runtime.tui_frame_band_outer must be > runtime.tui_frame_band_inner.")
    if not isinstance(tui_show_dev_panel, bool):
        raise ValueError("runtime.tui_show_dev_panel must be a boolean.")
    if not isinstance(dist_state_enabled, bool):
        raise ValueError("runtime.dist_state.enabled must be a boolean.")
    if not isinstance(dist_state_symbol, str) or not dist_state_symbol.strip():
        raise ValueError("runtime.dist_state.symbol must be a non-empty string.")
    if not isinstance(dist_state_source_id, str) or not dist_state_source_id.strip():
        raise ValueError("runtime.dist_state.source_id must be a non-empty string.")
    if not isinstance(dist_state_timeframes, list) or not dist_state_timeframes:
        raise ValueError("runtime.dist_state.timeframes must be a non-empty list.")
    allowed_timeframes = {"3m", "15m", "1h", "4h"}
    parsed_timeframes: list[Literal["3m", "15m", "1h", "4h"]] = []
    for timeframe in dist_state_timeframes:
        if not isinstance(timeframe, str) or timeframe not in allowed_timeframes:
            raise ValueError("runtime.dist_state.timeframes must only include 3m, 15m, 1h, 4h.")
        parsed_timeframes.append(cast(Literal["3m", "15m", "1h", "4h"], timeframe))
    if len(set(parsed_timeframes)) != len(parsed_timeframes):
        raise ValueError("runtime.dist_state.timeframes must not contain duplicates.")
    if dist_state_enabled and "3m" not in parsed_timeframes:
        raise ValueError("runtime.dist_state.timeframes must include 3m when dist_state is enabled.")
    if not isinstance(dist_state_warmup_kline_bars, int) or dist_state_warmup_kline_bars <= 0:
        raise ValueError("runtime.dist_state.warmup_kline_bars must be > 0.")
    if (
        not isinstance(dist_state_warmup_oi_hist_points, int)
        or dist_state_warmup_oi_hist_points <= 0
    ):
        raise ValueError("runtime.dist_state.warmup_oi_hist_points must be > 0.")
    if not isinstance(dist_state_ready_core_min_bars, int) or dist_state_ready_core_min_bars <= 0:
        raise ValueError("runtime.dist_state.ready_core_min_bars must be > 0.")
    if not isinstance(dist_state_ready_p_min_deltas, int) or dist_state_ready_p_min_deltas <= 0:
        raise ValueError("runtime.dist_state.ready_p_min_deltas must be > 0.")
    if dist_state_p_availability_mode not in {"strict", "continuous"}:
        raise ValueError("runtime.dist_state.p_availability_mode must be strict or continuous.")
    if not isinstance(dist_state_oi_poll_interval_ms, int) or dist_state_oi_poll_interval_ms <= 0:
        raise ValueError("runtime.dist_state.oi_poll_interval_ms must be > 0.")
    if not isinstance(dist_state_oi_tolerance_ms, int) or dist_state_oi_tolerance_ms <= 0:
        raise ValueError("runtime.dist_state.oi_tolerance_ms must be > 0.")
    if dist_state_oi_time_missing_policy not in {"reject"}:
        raise ValueError("runtime.dist_state.oi_time_missing_policy must be reject.")
    if not isinstance(dist_state_oi_verify_enabled, bool):
        raise ValueError("runtime.dist_state.oi_verify_enabled must be a boolean.")
    if (
        not isinstance(dist_state_oi_verify_timeframes, list)
        or not dist_state_oi_verify_timeframes
    ):
        raise ValueError("runtime.dist_state.oi_verify_timeframes must be a non-empty list.")
    parsed_verify_timeframes: list[Literal["3m", "15m", "1h", "4h"]] = []
    for timeframe in dist_state_oi_verify_timeframes:
        if not isinstance(timeframe, str) or timeframe not in allowed_timeframes:
            raise ValueError(
                "runtime.dist_state.oi_verify_timeframes must only include 3m, 15m, 1h, 4h."
            )
        parsed_verify_timeframes.append(cast(Literal["3m", "15m", "1h", "4h"], timeframe))
    if len(set(parsed_verify_timeframes)) != len(parsed_verify_timeframes):
        raise ValueError("runtime.dist_state.oi_verify_timeframes must not contain duplicates.")
    if not isinstance(dist_state_oi_verify_timeout_ms, int) or dist_state_oi_verify_timeout_ms <= 0:
        raise ValueError("runtime.dist_state.oi_verify_timeout_ms must be > 0.")
    if (
        not isinstance(dist_state_oi_verify_max_rate_per_min, int)
        or dist_state_oi_verify_max_rate_per_min <= 0
    ):
        raise ValueError("runtime.dist_state.oi_verify_max_rate_per_min must be > 0.")
    if not isinstance(dist_state_oi_quality_window_ms, int) or dist_state_oi_quality_window_ms <= 0:
        raise ValueError("runtime.dist_state.oi_quality_window_ms must be > 0.")
    if not isinstance(dist_state_oi_seed_points, int) or dist_state_oi_seed_points <= 0:
        raise ValueError("runtime.dist_state.oi_seed_points must be > 0.")
    if not isinstance(dist_state_oi_seed_min_points, int) or dist_state_oi_seed_min_points <= 0:
        raise ValueError("runtime.dist_state.oi_seed_min_points must be > 0.")
    if (
        not isinstance(dist_state_v_scale_window_bars, int)
        or dist_state_v_scale_window_bars <= 0
    ):
        raise ValueError("runtime.dist_state.v_scale_window_bars must be > 0.")
    if not isinstance(dist_state_v_scale_percentile, (int, float)):
        raise ValueError("runtime.dist_state.v_scale_percentile must be a number.")
    if not (0.0 < float(dist_state_v_scale_percentile) < 1.0):
        raise ValueError("runtime.dist_state.v_scale_percentile must be between 0 and 1.")
    if not isinstance(dist_state_v_scale_min_samples, int) or dist_state_v_scale_min_samples <= 0:
        raise ValueError("runtime.dist_state.v_scale_min_samples must be > 0.")
    for field_name, value in (
        ("hl_vol_bars", dist_state_hl_vol_bars),
        ("hl_stretch_bars", dist_state_hl_stretch_bars),
        ("hl_oi_bars", dist_state_hl_oi_bars),
        ("hl_atr_short_bars", dist_state_hl_atr_short_bars),
        ("hl_atr_long_bars", dist_state_hl_atr_long_bars),
        ("hl_a_bars", dist_state_hl_a_bars),
        ("k_s", dist_state_k_s),
        ("k_p", dist_state_k_p),
        ("k_t", dist_state_k_t),
    ):
        if not isinstance(value, (int, float)) or float(value) <= 0:
            raise ValueError(f"runtime.dist_state.{field_name} must be > 0.")
    if dist_state_oi_seed_min_points > dist_state_oi_seed_points:
        raise ValueError(
            "runtime.dist_state.oi_seed_min_points must be <= runtime.dist_state.oi_seed_points."
        )
    if not isinstance(dist_state_tokens_enabled, bool):
        raise ValueError("runtime.dist_state.tokens_enabled must be a boolean.")
    if not isinstance(dist_state_tokens_fail_fast_unknown, bool):
        raise ValueError("runtime.dist_state.tokens_fail_fast_unknown must be a boolean.")
    for field_name, value, lo, hi in (
        ("s_dir_deadband", dist_state_s_dir_deadband, 0.0, 1.0),
        ("s_ext_enter", dist_state_s_ext_enter, 0.0, 1.0),
        ("s_ext_exit", dist_state_s_ext_exit, 0.0, 1.0),
        ("s_revert_min_stretch", dist_state_s_revert_min_stretch, 0.0, 1.0),
        ("t_exp_enter", dist_state_t_exp_enter, -1.0, 1.0),
        ("t_exp_exit", dist_state_t_exp_exit, -1.0, 1.0),
        ("t_comp_enter", dist_state_t_comp_enter, -1.0, 1.0),
        ("t_comp_exit", dist_state_t_comp_exit, -1.0, 1.0),
        ("a_cont_enter", dist_state_a_cont_enter, -1.0, 1.0),
        ("a_cont_exit", dist_state_a_cont_exit, -1.0, 1.0),
        ("a_revert_enter", dist_state_a_revert_enter, -1.0, 1.0),
        ("a_revert_exit", dist_state_a_revert_exit, -1.0, 1.0),
        ("v_low_threshold", dist_state_v_low_threshold, 0.0, 1.0),
        ("t_rise_threshold", dist_state_t_rise_threshold, 0.0, 1.0),
        ("s_neut_max", dist_state_s_neut_max, 0.0, 1.0),
        ("a_neut_max", dist_state_a_neut_max, 0.0, 1.0),
        ("t_neut_max", dist_state_t_neut_max, 0.0, 1.0),
        ("v_neut_min", dist_state_v_neut_min, 0.0, 1.0),
        ("v_neut_max", dist_state_v_neut_max, 0.0, 1.0),
        ("t_exp_plus", dist_state_t_exp_plus, -1.0, 1.0),
        ("t_exp_plus_plus", dist_state_t_exp_plus_plus, -1.0, 1.0),
        ("t_comp_plus", dist_state_t_comp_plus, -1.0, 1.0),
        ("t_comp_plus_plus", dist_state_t_comp_plus_plus, -1.0, 1.0),
        ("a_cont_plus", dist_state_a_cont_plus, -1.0, 1.0),
        ("a_cont_plus_plus", dist_state_a_cont_plus_plus, -1.0, 1.0),
        ("a_revert_plus", dist_state_a_revert_plus, -1.0, 1.0),
        ("a_revert_plus_plus", dist_state_a_revert_plus_plus, -1.0, 1.0),
        ("s_exh_plus", dist_state_s_exh_plus, 0.0, 1.0),
        ("s_exh_plus_plus", dist_state_s_exh_plus_plus, 0.0, 1.0),
        ("p_confirm_threshold", dist_state_p_confirm_threshold, 0.0, 1.0),
    ):
        if not isinstance(value, (int, float)):
            raise ValueError(f"runtime.dist_state.{field_name} must be a number.")
        fv = float(value)
        if fv != fv:
            raise ValueError(f"runtime.dist_state.{field_name} must not be NaN.")
        if not (lo <= fv <= hi):
            raise ValueError(f"runtime.dist_state.{field_name} must be in [{lo}, {hi}].")
    for field_name, value in (
        ("token_min_hold_bars_3m", dist_state_token_min_hold_bars_3m),
        ("token_min_hold_bars_15m", dist_state_token_min_hold_bars_15m),
        ("token_min_hold_bars_1h", dist_state_token_min_hold_bars_1h),
        ("token_min_hold_bars_4h", dist_state_token_min_hold_bars_4h),
    ):
        if not isinstance(value, int):
            raise ValueError(f"runtime.dist_state.{field_name} must be an integer.")
        if value < 0:
            raise ValueError(f"runtime.dist_state.{field_name} must be >= 0.")
    if not (float(dist_state_t_exp_enter) > float(dist_state_t_exp_exit)):
        raise ValueError("runtime.dist_state.t_exp_enter must be > t_exp_exit.")
    if not (float(dist_state_t_comp_enter) < float(dist_state_t_comp_exit)):
        raise ValueError("runtime.dist_state.t_comp_enter must be < t_comp_exit.")
    if not (float(dist_state_a_cont_enter) > float(dist_state_a_cont_exit)):
        raise ValueError("runtime.dist_state.a_cont_enter must be > a_cont_exit.")
    if not (float(dist_state_a_revert_enter) < float(dist_state_a_revert_exit)):
        raise ValueError("runtime.dist_state.a_revert_enter must be < a_revert_exit.")
    if not (float(dist_state_s_ext_enter) > float(dist_state_s_ext_exit)):
        raise ValueError("runtime.dist_state.s_ext_enter must be > s_ext_exit.")
    if not (float(dist_state_t_exp_plus_plus) >= float(dist_state_t_exp_plus)):
        raise ValueError("runtime.dist_state.t_exp_plus_plus must be >= t_exp_plus.")
    if not (float(dist_state_t_comp_plus_plus) <= float(dist_state_t_comp_plus)):
        raise ValueError("runtime.dist_state.t_comp_plus_plus must be <= t_comp_plus.")
    if not (float(dist_state_a_cont_plus_plus) >= float(dist_state_a_cont_plus)):
        raise ValueError("runtime.dist_state.a_cont_plus_plus must be >= a_cont_plus.")
    if not (float(dist_state_a_revert_plus_plus) <= float(dist_state_a_revert_plus)):
        raise ValueError("runtime.dist_state.a_revert_plus_plus must be <= a_revert_plus.")
    if not (float(dist_state_s_exh_plus_plus) >= float(dist_state_s_exh_plus)):
        raise ValueError("runtime.dist_state.s_exh_plus_plus must be >= s_exh_plus.")
    if not (float(dist_state_v_neut_min) <= float(dist_state_v_neut_max)):
        raise ValueError("runtime.dist_state.v_neut_min must be <= v_neut_max.")
    if not isinstance(dist_state_narrative_enabled, bool):
        raise ValueError("runtime.dist_state.narrative_enabled must be a boolean.")
    if (
        not isinstance(dist_state_narrative_driver_tf, str)
        or dist_state_narrative_driver_tf not in allowed_timeframes
    ):
        raise ValueError("runtime.dist_state.narrative_driver_tf must be one of 3m, 15m, 1h, 4h.")
    if dist_state_narrative_driver_tf not in parsed_timeframes:
        raise ValueError(
            "runtime.dist_state.narrative_driver_tf must be one of runtime.dist_state.timeframes."
        )
    if (
        not isinstance(dist_state_narrative_linger_reminder_closes, int)
        or dist_state_narrative_linger_reminder_closes < 0
    ):
        raise ValueError("runtime.dist_state.narrative_linger_reminder_closes must be >= 0.")
    if not isinstance(dist_state_narrative_max_chars, int) or dist_state_narrative_max_chars < 16:
        raise ValueError("runtime.dist_state.narrative_max_chars must be >= 16.")
    if not isinstance(dist_state_narrative_secondary_min_ratio, (int, float)):
        raise ValueError("runtime.dist_state.narrative_secondary_min_ratio must be a number.")
    if not isinstance(dist_state_narrative_dir_ratio_min, (int, float)):
        raise ValueError("runtime.dist_state.narrative_dir_ratio_min must be a number.")
    if not (0.0 <= float(dist_state_narrative_secondary_min_ratio) <= 1.0):
        raise ValueError("runtime.dist_state.narrative_secondary_min_ratio must be in [0, 1].")
    if not (0.0 <= float(dist_state_narrative_dir_ratio_min) <= 1.0):
        raise ValueError("runtime.dist_state.narrative_dir_ratio_min must be in [0, 1].")

    dot_thresholds = _parse_thresholds(
        binning_dot_size_thresholds, "runtime.binning_dot_size_thresholds"
    )
    halo_thresholds = _parse_thresholds(
        binning_halo_thresholds, "runtime.binning_halo_thresholds"
    )
    if dispersion_metric not in {"hill", "entropy"}:
        raise ValueError("runtime.dispersion_metric must be 'hill' or 'entropy'.")
    dispersion_metric_literal = cast(Literal["hill", "entropy"], dispersion_metric)

    adapters: dict[str, AdapterConfig] = {}
    for name, adapter in adapters_section.items():
        if not isinstance(adapter, dict):
            raise ValueError(f"Adapter {name} config must be a table.")
        adapter_type = adapter.get("type")
        symbols = adapter.get("symbols")
        if not isinstance(adapter_type, str):
            raise ValueError(f"Adapter {name} is missing a type.")
        if not isinstance(symbols, list) or not symbols:
            raise ValueError(f"Adapter {name} is missing symbols.")

        normalized_symbols = [normalize_symbol(str(s)) for s in symbols]
        adapters[name] = AdapterConfig(
            type=adapter_type,
            symbols=normalized_symbols,
        )
    sources = _parse_sources(sources_section)
    _validate_source_registry(sources=sources, adapters=adapters)

    return AppConfig(
        adapters=adapters,
        sources=sources,
        tbt_window_multiplier=float(tbt_window_multiplier),
        update_window_seconds=float(update_window_seconds),
        price_selector_policy=cast(Literal["priority_sticky"], price_selector_policy),
        price_selector_stale_failover_ms=int(price_selector_stale_failover_ms),
        price_selector_recovery_confirm_cycles=int(price_selector_recovery_confirm_cycles),
        price_selector_switch_cooldown_cycles=int(price_selector_switch_cooldown_cycles),
        effort_floor_multiplier=float(effort_floor_multiplier),
        effort_floor_ticks=int(effort_floor_ticks),
        smoothing_dominance_alpha=float(smoothing_dominance_alpha),
        smoothing_effectiveness_alpha=float(smoothing_effectiveness_alpha),
        dispersion_metric=dispersion_metric_literal,
        halo_growth_rate=float(halo_growth_rate),
        halo_decay_rate=float(halo_decay_rate),
        binning_dot_size_thresholds=dot_thresholds,
        binning_halo_thresholds=halo_thresholds,
        binning_hysteresis_band=float(binning_hysteresis_band),
        tanh_k=float(tanh_k),
        scale_window_seconds=float(scale_window_seconds),
        persist_enabled=bool(persist_enabled),
        persist_input=cast(Literal["y_raw", "y_gated", "y"], persist_input),
        persist_input_deadband=float(persist_input_deadband),
        persist_neutral_dir_abs_flash=float(persist_neutral_dir_abs_flash),
        persist_neutral_dir_abs_persist=float(persist_neutral_dir_abs_persist),
        persist_tau_eff_active=float(persist_tau_eff_active),
        persist_tau_dir_active=float(persist_tau_dir_active),
        persist_pivot_active_abs=float(persist_pivot_active_abs),
        persist_pivot_confirm_s=float(persist_pivot_confirm_s),
        persist_pivot_neutralize_tau=float(persist_pivot_neutralize_tau),
        persist_pivot_neutral_zone_abs=float(persist_pivot_neutral_zone_abs),
        persist_rebuild_confirm_s=float(persist_rebuild_confirm_s),
        persist_pivot_cooldown_s=float(persist_pivot_cooldown_s),
        persist_pivot_max_s=float(persist_pivot_max_s),
        persist_max_delta_s_eff_per_second=float(persist_max_delta_s_eff_per_second),
        persist_tau_dir_pivot=float(persist_tau_dir_pivot),
        persist_dormant_quiet_abs=float(persist_dormant_quiet_abs),
        persist_dormant_active_abs=float(persist_dormant_active_abs),
        persist_dormant_quiet_s=float(persist_dormant_quiet_s),
        persist_tau_dormant=float(persist_tau_dormant),
        persist_dormant_effort_norm_threshold=float(persist_dormant_effort_norm_threshold),
        disp_scale_multiplier=float(disp_scale_multiplier),
        disp_scale_percentile=float(disp_scale_percentile),
        disp_scale_min_samples=int(disp_scale_min_samples),
        disp_scale_floor_percentile=float(disp_scale_floor_percentile),
        effort_scale_percentile=float(effort_scale_percentile),
        effort_scale_min_samples=int(effort_scale_min_samples),
        size_scale_percentile=float(size_scale_percentile),
        control_baseline_enabled=bool(control_baseline_enabled),
        control_baseline_target_window_s=float(control_baseline_target_window_s),
        control_baseline_target_update_s=float(control_baseline_target_update_s),
        control_baseline_breakout_band=float(control_baseline_breakout_band),
        control_baseline_confirm_s=float(control_baseline_confirm_s),
        control_baseline_exit_band_frac=float(control_baseline_exit_band_frac),
        control_baseline_peg_half_life_s=float(control_baseline_peg_half_life_s),
        control_baseline_reanchor_half_life_s=float(control_baseline_reanchor_half_life_s),
        control_baseline_peg_deadband=float(control_baseline_peg_deadband),
        control_baseline_max_window_samples=(
            int(control_baseline_max_window_samples)
            if control_baseline_max_window_samples is not None
            else None
        ),
        control_baseline_center_suppress_band=float(control_baseline_center_suppress_band),
        control_baseline_line_hide_warmup_s=float(control_baseline_line_hide_warmup_s),
        control_baseline_midnight_tick_enabled=bool(control_baseline_midnight_tick_enabled),
        control_baseline_midnight_tick_min_samples=int(control_baseline_midnight_tick_min_samples),
        control_baseline_midnight_tick_min_elapsed_s=float(
            control_baseline_midnight_tick_min_elapsed_s
        ),
        hygiene_enabled=bool(hygiene_enabled),
        hygiene_max_excess_wire_lag_ms=int(hygiene_max_excess_wire_lag_ms),
        hygiene_hard_max_wire_lag_ms=int(hygiene_hard_max_wire_lag_ms),
        hygiene_wire_lag_baseline_window_s=int(hygiene_wire_lag_baseline_window_s),
        hygiene_wire_lag_baseline_sample_interval_ms=int(
            hygiene_wire_lag_baseline_sample_interval_ms
        ),
        hygiene_wire_lag_baseline_min_samples=int(hygiene_wire_lag_baseline_min_samples),
        hygiene_wire_lag_baseline_max_samples=int(hygiene_wire_lag_baseline_max_samples),
        hygiene_dedupe_ttl_s=int(hygiene_dedupe_ttl_s),
        hygiene_log_interval_s=int(hygiene_log_interval_s),
        hygiene_future_venue_ts_grace_ms=int(hygiene_future_venue_ts_grace_ms),
        hygiene_connect_gate_s=int(hygiene_connect_gate_s),
        hygiene_connect_gate_max_excess_wire_lag_ms=int(
            hygiene_connect_gate_max_excess_wire_lag_ms
        ),
        hygiene_connect_gate_hard_max_wire_lag_ms=int(
            hygiene_connect_gate_hard_max_wire_lag_ms
        ),
        hygiene_connect_gate_rearm_after_s=int(hygiene_connect_gate_rearm_after_s),
        tui_min_width=int(tui_min_width),
        tui_min_height=int(tui_min_height),
        tui_max_width=int(tui_max_width),
        tui_max_height=int(tui_max_height),
        tui_dot_radii=dot_radii,
        tui_halo_radii=halo_radii,
        tui_frame_enabled=bool(tui_frame_enabled),
        tui_frame_inset_px=int(tui_frame_inset_px),
        tui_frame_band_inner=float(tui_frame_band_inner),
        tui_frame_band_outer=float(tui_frame_band_outer),
        tui_show_dev_panel=bool(tui_show_dev_panel),
        dist_state=DistStateRuntimeConfig(
            enabled=bool(dist_state_enabled),
            symbol=normalize_symbol(dist_state_symbol),
            source_id=dist_state_source_id.strip(),
            timeframes=tuple(parsed_timeframes),
            warmup_kline_bars=int(dist_state_warmup_kline_bars),
            warmup_oi_hist_points=int(dist_state_warmup_oi_hist_points),
            ready_core_min_bars=int(dist_state_ready_core_min_bars),
            ready_p_min_deltas=int(dist_state_ready_p_min_deltas),
            p_availability_mode=cast(Literal["strict", "continuous"], dist_state_p_availability_mode),
            oi_poll_interval_ms=int(dist_state_oi_poll_interval_ms),
            oi_tolerance_ms=int(dist_state_oi_tolerance_ms),
            oi_time_missing_policy=cast(
                Literal["reject"],
                dist_state_oi_time_missing_policy,
            ),
            oi_verify_enabled=bool(dist_state_oi_verify_enabled),
            oi_verify_timeframes=tuple(parsed_verify_timeframes),
            oi_verify_timeout_ms=int(dist_state_oi_verify_timeout_ms),
            oi_verify_max_rate_per_min=int(dist_state_oi_verify_max_rate_per_min),
            oi_quality_window_ms=int(dist_state_oi_quality_window_ms),
            oi_seed_points=int(dist_state_oi_seed_points),
            oi_seed_min_points=int(dist_state_oi_seed_min_points),
            v_scale_window_bars=int(dist_state_v_scale_window_bars),
            v_scale_percentile=float(dist_state_v_scale_percentile),
            v_scale_min_samples=int(dist_state_v_scale_min_samples),
            hl_vol_bars=float(dist_state_hl_vol_bars),
            hl_stretch_bars=float(dist_state_hl_stretch_bars),
            hl_oi_bars=float(dist_state_hl_oi_bars),
            hl_atr_short_bars=float(dist_state_hl_atr_short_bars),
            hl_atr_long_bars=float(dist_state_hl_atr_long_bars),
            hl_a_bars=float(dist_state_hl_a_bars),
            k_s=float(dist_state_k_s),
            k_p=float(dist_state_k_p),
            k_t=float(dist_state_k_t),
            tokens_enabled=bool(dist_state_tokens_enabled),
            tokens_fail_fast_unknown=bool(dist_state_tokens_fail_fast_unknown),
            s_dir_deadband=float(dist_state_s_dir_deadband),
            s_ext_enter=float(dist_state_s_ext_enter),
            s_ext_exit=float(dist_state_s_ext_exit),
            s_revert_min_stretch=float(dist_state_s_revert_min_stretch),
            t_exp_enter=float(dist_state_t_exp_enter),
            t_exp_exit=float(dist_state_t_exp_exit),
            t_comp_enter=float(dist_state_t_comp_enter),
            t_comp_exit=float(dist_state_t_comp_exit),
            a_cont_enter=float(dist_state_a_cont_enter),
            a_cont_exit=float(dist_state_a_cont_exit),
            a_revert_enter=float(dist_state_a_revert_enter),
            a_revert_exit=float(dist_state_a_revert_exit),
            v_low_threshold=float(dist_state_v_low_threshold),
            t_rise_threshold=float(dist_state_t_rise_threshold),
            s_neut_max=float(dist_state_s_neut_max),
            a_neut_max=float(dist_state_a_neut_max),
            t_neut_max=float(dist_state_t_neut_max),
            v_neut_min=float(dist_state_v_neut_min),
            v_neut_max=float(dist_state_v_neut_max),
            t_exp_plus=float(dist_state_t_exp_plus),
            t_exp_plus_plus=float(dist_state_t_exp_plus_plus),
            t_comp_plus=float(dist_state_t_comp_plus),
            t_comp_plus_plus=float(dist_state_t_comp_plus_plus),
            a_cont_plus=float(dist_state_a_cont_plus),
            a_cont_plus_plus=float(dist_state_a_cont_plus_plus),
            a_revert_plus=float(dist_state_a_revert_plus),
            a_revert_plus_plus=float(dist_state_a_revert_plus_plus),
            s_exh_plus=float(dist_state_s_exh_plus),
            s_exh_plus_plus=float(dist_state_s_exh_plus_plus),
            p_confirm_threshold=float(dist_state_p_confirm_threshold),
            token_min_hold_bars_3m=int(dist_state_token_min_hold_bars_3m),
            token_min_hold_bars_15m=int(dist_state_token_min_hold_bars_15m),
            token_min_hold_bars_1h=int(dist_state_token_min_hold_bars_1h),
            token_min_hold_bars_4h=int(dist_state_token_min_hold_bars_4h),
            narrative_enabled=bool(dist_state_narrative_enabled),
            narrative_driver_tf=cast(
                Literal["3m", "15m", "1h", "4h"], dist_state_narrative_driver_tf
            ),
            narrative_linger_reminder_closes=int(dist_state_narrative_linger_reminder_closes),
            narrative_max_chars=int(dist_state_narrative_max_chars),
            narrative_secondary_min_ratio=float(dist_state_narrative_secondary_min_ratio),
            narrative_dir_ratio_min=float(dist_state_narrative_dir_ratio_min),
        ),
    )


def _parse_sources(section: dict[object, object]) -> dict[str, SourceConfig]:
    sources: dict[str, SourceConfig] = {}
    for source_id_raw, source_cfg in section.items():
        if not isinstance(source_id_raw, str):
            raise ValueError("sources keys must be strings.")
        source_id = source_id_raw.strip()
        if not source_id:
            raise ValueError("sources keys must be non-empty strings.")
        if not isinstance(source_cfg, dict):
            raise ValueError(f"Source {source_id} config must be a table.")

        venue = source_cfg.get("venue")
        instrument_class = source_cfg.get("instrument_class")
        market_type_for_x = source_cfg.get("market_type_for_x")
        price_eligible = source_cfg.get("price_eligible")
        price_priority = source_cfg.get("price_priority")
        has_size = source_cfg.get("has_size")
        has_aggressor = source_cfg.get("has_aggressor")
        aggressor_mode = source_cfg.get("aggressor_mode")
        quote_mode = source_cfg.get("quote_mode")

        if not isinstance(venue, str) or not venue.strip():
            raise ValueError(f"Source {source_id} must define non-empty venue.")
        if not isinstance(instrument_class, str) or not instrument_class.strip():
            raise ValueError(f"Source {source_id} must define non-empty instrument_class.")
        if market_type_for_x not in {"spot", "perp"}:
            raise ValueError(f"Source {source_id} market_type_for_x must be 'spot' or 'perp'.")
        if not isinstance(price_eligible, bool):
            raise ValueError(f"Source {source_id} price_eligible must be a boolean.")
        if not isinstance(price_priority, int):
            raise ValueError(f"Source {source_id} price_priority must be an integer.")
        if not isinstance(has_size, bool):
            raise ValueError(f"Source {source_id} has_size must be a boolean.")
        if not isinstance(has_aggressor, bool):
            raise ValueError(f"Source {source_id} has_aggressor must be a boolean.")
        if aggressor_mode not in {"native", "inferred", "none"}:
            raise ValueError(
                f"Source {source_id} aggressor_mode must be one of: native, inferred, none."
            )
        if quote_mode not in {"usd_like", "converted", "foreign"}:
            raise ValueError(
                f"Source {source_id} quote_mode must be one of: usd_like, converted, foreign."
            )

        sources[source_id] = SourceConfig(
            source_id=source_id,
            venue=venue.strip(),
            instrument_class=instrument_class.strip(),
            market_type_for_x=cast(MarketTypeForX, market_type_for_x),
            price_eligible=price_eligible,
            price_priority=price_priority,
            capabilities=SourceCapabilities(
                has_size=has_size,
                has_aggressor=has_aggressor,
                aggressor_mode=cast(AggressorMode, aggressor_mode),
                quote_mode=cast(QuoteMode, quote_mode),
            ),
        )
    return sources


def _validate_source_registry(
    *,
    sources: Mapping[str, SourceConfig],
    adapters: Mapping[str, AdapterConfig],
) -> None:
    if not sources:
        raise ValueError("Source registry is empty.")
    missing = sorted(source_id for source_id in adapters if source_id not in sources)
    if missing:
        raise ValueError(
            "Source registry missing adapter source_ids: " + ",".join(missing)
        )
    for source_id, source in sources.items():
        caps = source.capabilities
        if caps.aggressor_mode == "native" and not caps.has_aggressor:
            raise ValueError(
                f"Source {source_id} has_aggressor must be true when aggressor_mode='native'."
            )
        if caps.aggressor_mode == "none" and caps.has_aggressor:
            raise ValueError(
                f"Source {source_id} has_aggressor must be false when aggressor_mode='none'."
            )
        if caps.aggressor_mode == "inferred" and caps.has_aggressor:
            raise ValueError(
                f"Source {source_id} has_aggressor must be false when aggressor_mode='inferred'."
            )
        if not source.price_eligible and source.price_priority != 0:
            raise ValueError(
                f"Source {source_id} must use price_priority=0 when price_eligible=false."
            )
        if source.price_eligible and source.price_priority < 0:
            raise ValueError(
                f"Source {source_id} price_priority must be >= 0 when price_eligible=true."
            )


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("-", "").replace("_", "").upper()


def _parse_thresholds(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be a list of two numbers.")
    low, high = value
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        raise ValueError(f"{label} must contain numeric values.")
    return float(low), float(high)


def _parse_int_triplet(value: object, label: str) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a list of three integers.")
    v0, v1, v2 = value
    if not isinstance(v0, int) or not isinstance(v1, int) or not isinstance(v2, int):
        raise ValueError(f"{label} values must be integers.")
    if v0 < 0 or v1 < 0 or v2 < 0:
        raise ValueError(f"{label} values must be >= 0.")
    return v0, v1, v2
