# FL-0029 – No Bar Construction in Adapters

## Decision

Adapters do **not** construct OHLC bars.  
They stream trade-level events and accumulate effort within the engine’s rolling update window Δ.

## Rationale

Bars introduce arbitrary aggregation boundaries and lag. Flow Lens operates on rolling structural windows, not candle semantics.

## Status

Accepted (Invariant)
