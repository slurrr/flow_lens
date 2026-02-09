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
