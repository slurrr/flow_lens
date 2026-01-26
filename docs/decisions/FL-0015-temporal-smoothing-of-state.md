# FL-0015 – Temporal Smoothing of State (X, Y)

## Decision

State variables X (dominance) and Y (effectiveness) are updated using low-pass smoothing:

Xₜ = Xₜ₋₁ + aₓ · (X_raw − Xₜ₋₁)  
Yₜ = Yₜ₋₁ + aᵧ · (Y_raw − Yₜ₋₁)

Default smoothing coefficients:
- aₓ = 0.15
- aᵧ = 0.15

## Rationale

Raw flow measurements are noisy at the update cadence. Light smoothing stabilizes visual state without introducing lag large enough to hide regime shifts. This preserves structural truth while preventing thrashing.

## Status

Accepted (Invariant)
