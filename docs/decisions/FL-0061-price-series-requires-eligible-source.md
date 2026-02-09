# FL-0061 – Price Series Requires Eligible Source (No Ineligible Fallback)

## Decision

If no price-eligible source is active, the buffer must return no price range and the engine must skip the state update for
that tick. Ineligible sources must never be used as implicit price series fallbacks.

## Rationale

Price selection must remain orthogonal to effort inclusion. Falling back to non-eligible sources violates the selector
contract and can distort X/Y via incorrect price deltas. Skipping the tick makes the failure explicit and preserves
semantic correctness.

## Status

Accepted (Phase 1).
