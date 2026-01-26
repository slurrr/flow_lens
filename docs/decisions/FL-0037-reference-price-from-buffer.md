# FL-0037 – Reference Price From Buffer

## Decision

Reference price p(t) is the most recent price observed within the active buffer window.

If no new trades occur within Δ, the last known price is carried forward.

## Rationale

Maintains continuity during low activity while ensuring price is always derived from real market events.

## Status

Accepted
