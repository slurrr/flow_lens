# FL-0036 – Aggregation From Buffer

## Decision

At each update:

E_spot = Σ effort_value where side_type = spot  
E_perp = Σ effort_value where side_type = perp  

Per-source aggregates E_i are also computed for dispersion.

## Rationale

All effort metrics derive from the same rolling structural window, ensuring consistency between dominance, effectiveness, and dispersion.

## Status

Accepted (Invariant)
