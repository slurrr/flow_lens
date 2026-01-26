# FL-0035 – Window Expiration Rule

## Decision

At each update tick, events where:

event.timestamp < (t_now − Δ)

are removed from the buffer.

Expiration is time-based, not count-based.

## Rationale

Market activity varies over time. A time-based window preserves structural consistency regardless of trade frequency.

## Status

Accepted (Invariant)
