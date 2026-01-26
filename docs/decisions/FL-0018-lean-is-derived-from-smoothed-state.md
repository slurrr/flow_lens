# FL-0018 – Lean Is Derived from Smoothed State Only

## Decision

Dot lean direction is computed from changes in smoothed X and Y:

lean_dir = sign(Xₜ − Xₜ₋₁, Yₜ − Yₜ₋₁)

Lean is shown for 1–2 frames only after an update.

## Rationale

Lean must reflect structural change, not micro-noise. Using smoothed state prevents jitter and preserves the “gesture” semantics of lean.

## Status

Accepted (Invariant)
