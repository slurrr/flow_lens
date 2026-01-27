# FL-0016 – Asymmetric Dispersion Dynamics (Halo)

## Decision

Halo (dispersion) evolves with asymmetric rates:

If halo_raw > haloₜ₋₁:  
 haloₜ = haloₜ₋₁ + g · (halo_raw − haloₜ₋₁)

If halo_raw ≤ haloₜ₋₁:  
 haloₜ = halo_raw  (or fast decay with high rate)

Default:
- g = 0.10 (slow growth)
- decay rate ≥ 0.5 (fast contraction)

## Rationale

Participation disperses gradually as independent actors join, but contracts rapidly as crowds exit. Asymmetry prevents false instant crowding and reflects real market diffusion behavior.

## Status

Accepted (Invariant)
