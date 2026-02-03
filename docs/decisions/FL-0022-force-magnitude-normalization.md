# FL-0022 – Force Magnitude Normalization (Dot Size)

## Decision

Dot size (force magnitude) is based on normalized dominance:

dom = |D| / (E_spot + E_perp + ε)  
S_raw = sqrt(dom)

S_raw ∈ [0, 1] and is later mapped to visual bins.

## Rationale

Using normalized dominance instead of raw effort ensures size represents decisiveness of control, not market activity. Square-root scaling improves perceptual sensitivity at low magnitudes.

## Status

Superseded by FL-0056.
