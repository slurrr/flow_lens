# FL-0032 – Engine-Side Effort Aggregation

## Decision

Within each update window Δ, the engine aggregates:

E_spot = Σ effort_value where side_type = spot  
E_perp = Σ effort_value where side_type = perp

Per-source values are retained for dispersion calculation.

## Rationale

Aggregation inside the engine ensures consistency across adapters and enables correct normalization and halo computation.

## Status

Accepted (Invariant)
