# FL-0030 – Binance Adapter (Initial Implementation)

## Decision

Initial adapter set includes:

- Binance Spot WebSocket: `aggTrade`
- Binance Perp (USDⓈ-M Futures) WebSocket: `aggTrade`

Subscriptions are per-symbol.

Each message produces:
- effort_value = price × quantity
- source_id = "binance_spot" or "binance_perp"

## Rationale

Binance provides high-liquidity reference markets with consistent trade feeds. aggTrade reduces noise while preserving aggressive flow.

## Status

Accepted (Phase 1 Adapter)
