# Venue Endpoints + Field Notes (Working) — 2026-02-05

Status: working doc to prevent re-discovery. This is not a spec or a decision record.

Purpose:

- Centralize the **endpoints, subscription payloads, and field mappings** we already used in the venue prefilter and
  tournament tooling so adapter implementation does not require re-research.
- Capture “what we learned while implementing” in one place across venues.

Source of truth:

- Trade capture implementation: `scripts/venue_trades_capture.py`
- L1 prefilter implementation: `scripts/venue_l1_prefilter.py`

Notes:

- This document intentionally records **what we are using**, not the full vendor documentation.
- If a venue changes their API, update the script first, then update this doc.

---

## Candidate quick index

Candidates currently used in tournament tooling (trade + L1):

- `binance_spot`, `binance_perp`
- `coinbase_spot`
- `okx_spot`, `okx_perp`
- `bybit_spot`, `bybit_perp`
- `gate_spot`, `gate_perp`
- `kucoin_spot`, `kucoin_perp`
- `deribit_perp`
- `hyperliquid_perp`
- `upbit_spot` (regional signal; KRW-quoted)

---

## Binance (spot/perp)

Trade prints:

- Spot WS: `wss://stream.binance.com:9443/ws/<stream>`
- Perp WS: `wss://fstream.binance.com/ws/<stream>`
- Stream: `<symbol>@aggTrade` (e.g., `btcusdt@aggTrade`)

Fields (aggTrade):

- ts: `T` (ms)
- price: `p`
- size: `q` (base qty)
- aggressor: `m` (true = seller was maker → aggressor side = sell; else buy)

Notes:

- Spot quote conversion exists in live adapter (quote pairs streamed); tournament tooling assumes USD-like pairs.

---

## Coinbase (spot)

Trade prints:

- WS: `wss://ws-feed.exchange.coinbase.com`
- Subscribe: `{"type":"subscribe","product_ids":["BTC-USD"],"channels":["matches"]}`

Fields (match):

- ts: `time` (ISO-8601)
- price: `price`
- size: `size`
- aggressor: `side` (buy/sell)

Notes:

- USD-quoted products are `quote_mode=usd_like`.

---

## OKX (spot/perp)

Trade prints:

- WS: `wss://ws.okx.com:8443/ws/v5/public`
- Subscribe:
  - Spot instId: `<BASE>-USDT` (e.g., `BTC-USDT`)
  - Perp instId: `<BASE>-USDT-SWAP` (e.g., `BTC-USDT-SWAP`)
  - Payload: `{"op":"subscribe","args":[{"channel":"trades","instId": "<instId>"}]}`

Fields (trades data rows):

- ts: `ts` (ms)
- price: `px`
- size: `sz`
- aggressor: venue does not provide canonical aggressor; tournament tooling treats direction via venue’s trade message
  semantics only where available (live adapter work will decide `native` vs `inferred`).

---

## Bybit (spot/perp)

Trade prints:

- Spot WS: `wss://stream.bybit.com/v5/public/spot`
- Perp WS: `wss://stream.bybit.com/v5/public/linear`
- Subscribe: `{"op":"subscribe","args":["publicTrade.<SYMBOL>"]}` (e.g., `publicTrade.BTCUSDT`)

Fields (publicTrade rows):

- ts: `T` (row) or `ts` (envelope) (ms)
- price: `p`
- size: `v`
- aggressor: not always explicit; adapter implementation may require an inference policy if we treat it as directional.

---

## Gate (spot/perp)

Trade prints:

- Spot WS: `wss://api.gateio.ws/ws/v4/`
  - Subscribe: `{"time":<sec>,"channel":"spot.trades","event":"subscribe","payload":["BTC_USDT"]}`
- Perp WS: `wss://fx-ws.gateio.ws/v4/ws/usdt`
  - Subscribe: `{"time":<sec>,"channel":"futures.trades","event":"subscribe","payload":["BTC_USDT"]}`

Fields:

- Gate payload shapes can vary; confirm exact fields during adapter implementation.

---

## KuCoin (spot/perp)

Trade prints:

- Spot bullet token: `POST https://api.kucoin.com/api/v1/bullet-public`
- Futures bullet token: `POST https://api-futures.kucoin.com/api/v1/bullet-public`
- Subscribe topics:
  - Spot: `/market/match:<BASE>-USDT`
  - Perp: `/contractMarket/execution:<SYMBOL>` where BTC is `XBTUSDTM`, else `<BASE>USDTM`
- Ping: `{"type":"ping"}`

Notes:

- The bullet token fetch returns endpoint + ping interval; tooling caches for ~9 minutes.

---

## Deribit (perp)

Trade prints:

- WS: `wss://www.deribit.com/ws/api/v2`
- Subscribe channel: `trades.<BASE>-PERPETUAL.100ms`

Fields (subscription data rows):

- ts: `timestamp` (ms)
- price: `price`
- size: `amount`
- aggressor: not a simple buy/sell field; adapter implementation needs a directional policy decision if we include it in Y.

---

## Hyperliquid (perp)

Trade prints:

- WS: `wss://api.hyperliquid.xyz/ws`
- Subscribe: `{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}`

Fields (trades rows):

- ts: `time` (ms)
- price: `px`
- size: `sz`
- aggressor: not a simple buy/sell field; likely requires inference if used as directional.

---

## Upbit (spot, KRW)

Trade prints:

- WS: `wss://api.upbit.com/websocket/v1`
- Subscribe: `[{"ticket":"..."},{"type":"trade","codes":["KRW-BTC"]}]`

Fields:

- ts: `trade_timestamp` (ms)
- price: `trade_price` (KRW)
- size: `trade_volume` (base qty)

Notes:

- KRW quote means `quote_mode=foreign`; this is usually excluded from USD-like composite references and treated as a
  regional signal only unless conversion plumbing is added.

