# FL-0045 – Adaptive window length from TBT

## Decision

Window length Δ adapts per symbol based on the minimum time-between-trades (TBT) across its tracked pairs. The effective window is:

`Δ = max(default_window, min_TBT * multiplier)`

The multiplier is configurable (runtime setting).

## Rationale

Low-liquidity symbols require longer windows to avoid noisy or empty-state readings, while high-liquidity symbols can remain responsive. Using min TBT ensures the dominant trading pair drives cadence.

## Status
Accepted
