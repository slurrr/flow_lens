# FL-0024 – Cross-Symbol Stability Principle

## Decision

All normalized variables (X, Y, S_raw, Halo_raw) must be dimensionless and bounded within [−1, +1] or [0, 1] prior to smoothing and binning.

## Rationale

This guarantees that visual states have consistent meaning across symbols, volatility regimes, and adapter implementations. Prevents hidden symbol-specific scaling.

## Status

Accepted (Foundational Invariant)
