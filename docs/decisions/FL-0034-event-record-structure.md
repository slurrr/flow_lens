# FL-0034 – Event Record Structure

## Decision

Each buffered event has the structure:

{
  timestamp: int,
  source_id: string,
  side_type: enum {spot, perp},
  effort_value: float,
  price: float
}

## Rationale

This minimal structure supports:
- effort aggregation
- dispersion calculation
- reference price tracking
without embedding any derived metrics.

## Status

Accepted (Invariant)
