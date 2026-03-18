from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DispersionMetric = Literal["hill", "entropy"]
PersistenceInput = Literal["y_raw", "y_gated", "y"]


@dataclass(frozen=True)
class TimeDomain:
    update_window_seconds: float = 2.0


@dataclass(frozen=True)
class EffortFloor:
    rolling_window_ticks: int = 60
    multiplier_alpha: float = 0.2


@dataclass(frozen=True)
class Smoothing:
    dominance_alpha: float = 0.15
    effectiveness_alpha: float = 0.15


@dataclass(frozen=True)
class EffectivenessScaling:
    tanh_k: float = 500.0


@dataclass(frozen=True)
class InputNormalization:
    scale_window_seconds: float = 600.0


@dataclass(frozen=True)
class Persistence:
    enabled: bool = True
    input_source: PersistenceInput = "y_gated"
    input_deadband: float = 0.0
    neutral_dir_abs_flash: float = 0.05
    neutral_dir_abs_persist: float = 0.05
    tau_eff_active: float = 18.0
    tau_dir_active: float = 12.0
    pivot_active_abs: float = 0.10
    pivot_confirm_s: float = 6.0
    pivot_neutralize_tau: float = 3.0
    pivot_neutral_zone_abs: float = 0.08
    rebuild_confirm_s: float = 4.0
    pivot_cooldown_s: float = 10.0
    pivot_max_s: float = 18.0
    max_delta_s_eff_per_second: float = 0.4
    tau_dir_pivot: float = 4.0
    dormant_quiet_abs: float = 0.04
    dormant_active_abs: float = 0.08
    dormant_quiet_s: float = 20.0
    tau_dormant: float = 45.0
    dormant_effort_norm_threshold: float = 0.35


@dataclass(frozen=True)
class EffectivenessDeadband:
    disp_scale_multiplier: float = 0.25


@dataclass(frozen=True)
class DispScaleConfig:
    percentile: float = 0.75
    min_samples: int = 20
    floor_percentile: float = 0.1


@dataclass(frozen=True)
class EffortScaleConfig:
    percentile: float = 0.5
    min_samples: int = 20


@dataclass(frozen=True)
class SizeScaleConfig:
    percentile: float = 0.5


@dataclass(frozen=True)
class HaloDynamics:
    growth_rate: float = 0.10
    decay_rate: float = 0.5


@dataclass(frozen=True)
class Binning:
    dot_size_thresholds: tuple[float, float] = (0.35, 0.70)
    halo_thresholds: tuple[float, float] = (0.33, 0.66)
    hysteresis_band: float = 0.05


@dataclass(frozen=True)
class ControlBaseline:
    enabled: bool = True
    target_window_s: float = 1800.0
    target_update_s: float = 10.0
    breakout_band: float = 0.06
    confirm_s: float = 30.0
    exit_band_frac: float = 0.50
    peg_half_life_s: float = 7200.0
    reanchor_half_life_s: float = 180.0
    peg_deadband: float = 0.015
    max_window_samples: int | None = None
    center_suppress_band: float = 0.02
    line_hide_warmup_s: float = 120.0
    midnight_tick_enabled: bool = True
    midnight_tick_min_samples: int = 60
    midnight_tick_min_elapsed_s: float = 600.0


@dataclass(frozen=True)
class Defaults:
    time_domain: TimeDomain = TimeDomain()
    effort_floor: EffortFloor = EffortFloor()
    dispersion_metric: DispersionMetric = "hill"
    smoothing: Smoothing = Smoothing()
    effectiveness_scaling: EffectivenessScaling = EffectivenessScaling()
    input_normalization: InputNormalization = InputNormalization()
    persistence: Persistence = Persistence()
    effectiveness_deadband: EffectivenessDeadband = EffectivenessDeadband()
    disp_scale: DispScaleConfig = DispScaleConfig()
    effort_scale: EffortScaleConfig = EffortScaleConfig()
    size_scale: SizeScaleConfig = SizeScaleConfig()
    halo_dynamics: HaloDynamics = HaloDynamics()
    binning: Binning = Binning()
    control_baseline: ControlBaseline = ControlBaseline()
