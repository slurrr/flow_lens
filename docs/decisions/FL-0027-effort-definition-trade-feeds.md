# FL-0027 – Effort Definition (Trade-Level Feeds)

## Decision

For trade-based adapters (e.g., Binance aggTrades):

effort_value = aggressive trade notional

effort_value = price × quantity

No sign is applied at adapter level. Direction is inferred later from spot vs perp grouping.

## Rationale

Notional aggressive volume is a direct proxy for “force” applied to the order book. It is simple, comparable, and robust across venues.

## Status

Accepted (Baseline Effort Model)
