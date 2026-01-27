# FL-0042 – Connection Status Indicator (Operational)

## Decision

The TUI displays a connection status indicator next to the symbol:

- **Green**: connected and receiving data
- **Yellow**: data stale (no new events for > 5 seconds)
- **Red**: adapter disconnected

The indicator returns to green when data flow resumes.

## Rationale

Operators must know at a glance whether the lens reflects live flow or stale data.
This adds no market interpretation and supports operational correctness.

## Status

Accepted (Operational)
