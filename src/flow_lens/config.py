from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, cast


@dataclass(frozen=True)
class AdapterConfig:
    type: str
    symbols: list[str]


@dataclass(frozen=True)
class AppConfig:
    adapters: Mapping[str, AdapterConfig]
    tbt_window_multiplier: float
    update_window_seconds: float
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
    disp_scale_multiplier: float
    disp_scale_percentile: float
    disp_scale_min_samples: int
    effort_scale_percentile: float
    effort_scale_min_samples: int


def load_app_config(path: Path | str = Path("config/app.toml")) -> AppConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    adapters_section = data.get("adapters", {})
    if not isinstance(adapters_section, dict) or not adapters_section:
        raise ValueError("app.toml must define adapters.")
    runtime_section = data.get("runtime", {})
    if not isinstance(runtime_section, dict):
        raise ValueError("runtime config must be a table.")
    tbt_window_multiplier = runtime_section.get("tbt_window_multiplier", 4.0)
    update_window_seconds = runtime_section.get("update_window_seconds", 2.0)
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
    disp_scale_multiplier = runtime_section.get("disp_scale_multiplier", 0.25)
    disp_scale_percentile = runtime_section.get("disp_scale_percentile", 0.75)
    disp_scale_min_samples = runtime_section.get("disp_scale_min_samples", 20)
    effort_scale_percentile = runtime_section.get("effort_scale_percentile", 0.5)
    effort_scale_min_samples = runtime_section.get("effort_scale_min_samples", 20)
    if not isinstance(tbt_window_multiplier, (int, float)):
        raise ValueError("runtime.tbt_window_multiplier must be a number.")
    if not isinstance(update_window_seconds, (int, float)):
        raise ValueError("runtime.update_window_seconds must be a number.")
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
    if not isinstance(disp_scale_multiplier, (int, float)):
        raise ValueError("runtime.disp_scale_multiplier must be a number.")
    if not isinstance(disp_scale_percentile, (int, float)):
        raise ValueError("runtime.disp_scale_percentile must be a number.")
    if not isinstance(disp_scale_min_samples, int):
        raise ValueError("runtime.disp_scale_min_samples must be an integer.")
    if disp_scale_percentile <= 0 or disp_scale_percentile >= 1:
        raise ValueError("runtime.disp_scale_percentile must be between 0 and 1.")
    if disp_scale_min_samples <= 0:
        raise ValueError("runtime.disp_scale_min_samples must be > 0.")
    if not isinstance(effort_scale_percentile, (int, float)):
        raise ValueError("runtime.effort_scale_percentile must be a number.")
    if not isinstance(effort_scale_min_samples, int):
        raise ValueError("runtime.effort_scale_min_samples must be an integer.")
    if effort_scale_percentile <= 0 or effort_scale_percentile >= 1:
        raise ValueError("runtime.effort_scale_percentile must be between 0 and 1.")
    if effort_scale_min_samples <= 0:
        raise ValueError("runtime.effort_scale_min_samples must be > 0.")

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

    return AppConfig(
        adapters=adapters,
        tbt_window_multiplier=float(tbt_window_multiplier),
        update_window_seconds=float(update_window_seconds),
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
        disp_scale_multiplier=float(disp_scale_multiplier),
        disp_scale_percentile=float(disp_scale_percentile),
        disp_scale_min_samples=int(disp_scale_min_samples),
        effort_scale_percentile=float(effort_scale_percentile),
        effort_scale_min_samples=int(effort_scale_min_samples),
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
