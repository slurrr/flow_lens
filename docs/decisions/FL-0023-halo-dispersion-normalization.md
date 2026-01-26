# FL-0023 – Dispersion Normalization (Halo)

## Decision

Dispersion is computed from normalized source weights:

wᵢ = Eᵢ / ΣEᵢ  
H = 1 / Σ(wᵢ²)  
Hn = (H − 1) / (K − 1)

Halo_raw = Hn ∈ [0, 1]

## Rationale

Hill-number normalization produces an “effective number of contributors,” which aligns with the halo’s semantic role as dispersion of effort. Normalization removes dependence on number of sources.

## Status

Accepted (Invariant)
