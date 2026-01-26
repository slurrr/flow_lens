# FL-0033 – Rolling Event Buffer Model

## Decision

The engine maintains a rolling time-based event buffer per symbol.  
All incoming effort events are stored with timestamps and automatically expired once they fall outside the active window Δ.

The buffer is the sole source of effort aggregation.

## Rationale

Flow Lens operates on structural rolling windows, not fixed bars. A time-based buffer ensures continuity, avoids boundary artifacts, and preserves real-time responsiveness.

## Status

Accepted (Architecture)
