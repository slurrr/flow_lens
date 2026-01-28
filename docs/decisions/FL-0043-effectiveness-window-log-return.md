# FL-0043 – Window-anchored effectiveness with log-return and normalized effort

## Decision

Effectiveness (Y) is computed from window-anchored log-return displacement and effort normalized by recent median effort. Displacement uses price_start→price_end over the active window Δ, and effort is normalized as E_norm = E / median(E_recent).

## Rationale

This keeps Y dimensionless and comparable across symbols while preserving the core meaning: “over this window, given who is dominant, is price moving in that direction per unit of effort.” Log-return avoids unit mismatch, and effort normalization prevents notional scale from collapsing Y toward zero.

## Status
Accepted
