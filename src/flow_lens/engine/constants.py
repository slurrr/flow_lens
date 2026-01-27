from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DispersionMetric = Literal["hill", "entropy"]


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
    tanh_k: float = 1.0


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
    halo_dynamics: HaloDynamics = HaloDynamics()
    binning: Binning = Binning()
