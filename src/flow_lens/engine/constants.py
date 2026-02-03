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
    gain_per_second: float = 0.5
    input_deadband: float = 0.0


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
class HaloDynamics:
    growth_rate: float = 0.10
    decay_rate: float = 0.5


@dataclass(frozen=True)
class Binning:
    dot_size_thresholds: tuple[float, float] = (0.35, 0.70)
    halo_thresholds: tuple[float, float] = (0.33, 0.66)
    hysteresis_band: float = 0.05


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
    halo_dynamics: HaloDynamics = HaloDynamics()
    binning: Binning = Binning()
