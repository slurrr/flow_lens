# FL-0019 – Dominance Normalization (X-Axis)

## Decision

Dominance (X) is computed as normalized net effort:

D = E_spot − E_perp  
X_raw = D / (E_spot + E_perp + ε)

X is then clipped to [-1, +1] before smoothing.

## Rationale

Normalizing by total effort removes scale dependence and ensures X is a relative control measure rather than an absolute volume measure. This allows the lens to compare structural dominance consistently across symbols and regimes.

## Status

Accepted (Invariant)
