# FL-0044 – Dominance (X) is gated by the effort floor

## Decision

The air-pocket effort floor gate applies to dominance (X) as well as effectiveness (Y). When effort is below the floor, X is damped toward 0 instead of snapping to ±1.

## Rationale

This prevents data-availability artifacts (e.g., one side missing for a tick) from forcing extreme control readings when actual effort is thin.

## Status
Accepted
