from __future__ import annotations

from typing import Mapping

from flow_lens.engine.constants import Defaults

EPSILON = 1e-12


def hill_number_dispersion(per_source: Mapping[str, float]) -> float:
    total = sum(per_source.values())
    if total <= 0.0:
        return 0.0

    weights = [value / total for value in per_source.values() if value > 0.0]
    k = len(weights)
    if k <= 1:
        return 0.0

    sum_w2 = sum(weight * weight for weight in weights)
    if sum_w2 <= 0.0:
        return 0.0

    h = 1.0 / (sum_w2 + EPSILON)
    return _clamp((h - 1.0) / (k - 1.0), 0.0, 1.0)


def update_halo(
    halo_prev: float,
    halo_raw: float,
    defaults: Defaults,
) -> float:
    if halo_raw > halo_prev:
        rate = defaults.halo_dynamics.growth_rate
        updated = halo_prev + rate * (halo_raw - halo_prev)
        return _clamp(updated, 0.0, 1.0)

    rate = defaults.halo_dynamics.decay_rate
    updated = halo_prev + rate * (halo_raw - halo_prev)
    return _clamp(updated, 0.0, 1.0)


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
