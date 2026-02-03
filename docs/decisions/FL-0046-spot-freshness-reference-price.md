# FL-0046 – Spot reference price is freshness-gated

## Decision

Reference price prefers spot only if spot has printed within the active window Δ; otherwise the perp price is used. Window displacement is computed from the same side (spot or perp) to keep the price path coherent.

## Rationale

Stale spot prints can freeze displacement even when perp is moving. A freshness gate preserves the intent of spot preference without sacrificing responsiveness when spot is inactive.

## Status
Accepted (amended by FL-0052 for stale-tick hysteresis before spot→perp failover)
