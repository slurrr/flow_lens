# FL-0026 – Adapter Output Contract

## Decision

Each adapter must emit, at engine update cadence, a **FlowFrame** containing:

- `symbol` (string)
- `timestamp` (ms or ns)
- `price` (reference price)
- `efforts`: list of effort contributions

Each effort contribution is:

{
  source_id: string,
  side_type: enum {spot, perp},
  aggressor_side: enum {buy, sell},
  effort_value: float ≥ 0
}

No normalized values are produced by adapters.

## Rationale

This keeps adapters simple and allows the engine to:
- Aggregate effort
- Compute dominance
- Compute dispersion
- Apply normalization and smoothing uniformly

## Status

Accepted (Amended by FL-0049)
