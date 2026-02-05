# FL-0028 – Reference Price Source

## Decision

Reference price `p(t)` for a symbol is computed as:

Last trade price from the **spot adapter** if available;  
otherwise fallback to perp last trade.

## Rationale

Spot price better reflects actual asset transfer. Perp price is used only when spot is unavailable.

## Status

Superseded by FL-0052 (stale-tick hysteresis) and FL-0060 (multi-source selector policy).
