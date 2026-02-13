# Venue Add Notes — Bybit Spot + Perp (`bybit_spot`, `bybit_perp`)

Status: working notes (Pass 1). Endpoint + field mapping confirmed via live captures on 2026-02-11.

Checklist:

- `docs/reference/venue-addition-checklist-phase1.md`
- Phase 1 contract: `docs/reference/multi-venue-adapter-architecture-contract-wip-2026-02-04.md`

## 0) Summary

- Venue: Bybit
- Source id(s): `bybit_spot`, `bybit_perp`
- Symbol scope (Phase 1): BTC + SOL (yes, but BTC-first; SOL used as control)
- Market type for X:
  - `bybit_spot`: `spot`
  - `bybit_perp`: `perp`
- Instrument class:
  - `bybit_spot`: `spot`
  - `bybit_perp`: `perp` (USDT linear perpetual)
- Quote mode:
  - `bybit_spot`: `usd_like` (USDT; USDC pair exists but excluded for Phase 1)
  - `bybit_perp`: `usd_like` (USDT quote + USDT settle)
- Aggressor mode (side):
  - `bybit_spot`: `native` (trade row includes `S=Buy|Sell`)
  - `bybit_perp`: `native` (trade row includes `S=Buy|Sell`)
- Price eligible + priority (draft):
  - `bybit_spot`: `price_eligible=true`, `price_priority=70` (below `coinbase_spot=100`, `binance_spot=90`; leaves room for `okx_spot=80`)
  - `bybit_perp`: `price_eligible=true`, `price_priority=9` (below `binance_perp=10`)

## 1) Endpoints / Subscriptions

REST (instrument metadata / quote currency confirmation):

- Spot instruments: `https://api.bybit.com/v5/market/instruments-info?category=spot&symbol=BTCUSDT`
- Spot instruments: `https://api.bybit.com/v5/market/instruments-info?category=spot&symbol=BTCUSDC`
- Linear perp instruments: `https://api.bybit.com/v5/market/instruments-info?category=linear&symbol=BTCUSDT`

WS (trade prints):

- Spot WS: `wss://stream.bybit.com/v5/public/spot`
- Linear perp WS: `wss://stream.bybit.com/v5/public/linear`
- Subscribe payload:
  - `{"op":"subscribe","args":["publicTrade.BTCUSDT","publicTrade.SOLUSDT"]}`

## 2) Message Field Mapping

### 2.1 Trade stream envelope

Messages observed:

- Subscribe ack: `{"op":"subscribe","success":true,...}`
- Trade payload:
  - `topic`: `publicTrade.<SYMBOL>` (e.g. `publicTrade.BTCUSDT`)
  - `type`: `snapshot`
  - `ts`: envelope timestamp (ms)
  - `data`: list of trade rows

### 2.2 Trade row mapping (spot + linear)

Observed trade row keys (subset):

- `s`: symbol (e.g. `BTCUSDT`, `SOLUSDT`)
- `T`: trade timestamp (ms since epoch)
- `p`: trade price (string, USDT-quoted)
- `v`: trade size (string, base quantity)
- `S`: side (`Buy`/`Sell`) (string)
- `i`: trade id (string)

Adapter mapping:

- Venue-native instrument id field → `AdapterEvent.symbol`:
  - `s`
- Canonical base symbol derivation → `AdapterEvent.base_symbol`:
  - `s` stripped of quote suffix (`BTCUSDT` → `BTC`, `SOLUSDT` → `SOL`)
- Exchange timestamp field → `Event.timestamp` (ms since epoch):
  - row `T`
- Price field → `Event.price` (USD-like):
  - float(row `p`) (USDT treated as USD-equivalent)
- Size field → `Event.effort_value` (definition):
  - `effort_value = float(p) * float(v)` (USD-equivalent notional)
- Aggressor side field → `Event.aggressor_side`:
  - row `S`: `Buy` → `buy`, `Sell` → `sell`

Notes:

- `S` is assumed to be taker-side (aggressor) and treated as `native` in Phase 1. If later evidence shows `S` is not
  taker-side, this must be revisited as a capability semantics issue (not a tuning knob).

## 3) Unit Conversion (quote handling)

- Bybit spot has both `BTCUSDT` and `BTCUSDC` markets (confirmed via REST instrument info).
- Phase 1 scope uses `BTCUSDT` / `SOLUSDT` only:
  - `quote_mode=usd_like`
  - no conversion plumbing required

## 4) Failure Modes / Caveats

- Message types: ensure we ignore non-trade messages (`op` acks, errors, etc).
- Snapshot semantics: trade messages observed with `type="snapshot"`; parser should treat every `data[]` row as a trade.
- Symbol parsing: base extraction assumes `*USDT` suffix for Phase 1; if we add USDC pairs later, base parsing must handle
  `*USDC` explicitly (do not guess).
- Rate limits / disconnect patterns: TBD; monitor via existing adapter stats + diagnostics switch logs.

## 5) Config / Registry Entry (Draft)

`config/app.toml` snippets to add (draft; finalize during implementation):

`[adapters.bybit_spot]`

- `type = "bybit_spot_ws"`
- `symbols = ["BTC","SOL"]` (base symbols; adapter maps to `BTCUSDT`/`SOLUSDT`)

`[adapters.bybit_perp]`

- `type = "bybit_perp_ws"`
- `symbols = ["BTC","SOL"]` (base symbols; adapter maps to `BTCUSDT`/`SOLUSDT`)

`[sources.bybit_spot]`

- `venue = "bybit"`
- `instrument_class = "spot"`
- `market_type_for_x = "spot"`
- `price_eligible = true`
- `price_priority = 70`
- `has_size = true`
- `has_aggressor = true`
- `aggressor_mode = "native"`
- `quote_mode = "usd_like"`

`[sources.bybit_perp]`

- `venue = "bybit"`
- `instrument_class = "perp"`
- `market_type_for_x = "perp"`
- `price_eligible = true`
- `price_priority = 9`
- `has_size = true`
- `has_aggressor = true`
- `aggressor_mode = "native"`
- `quote_mode = "usd_like"`

## 6) Diagnostics Expectations (what to look for)

- `price_series_unavailable`: should be 0 (or stop-and-fix) (FL-0061).
- Selector behavior:
  - switches should remain rare; reasons must be `stale/recovered/priority`.
  - `bybit_spot` should not become active price series unless higher-priority spot sources are stale.
- Per-source effort presence:
  - `bybit_spot` and `bybit_perp` appear in per-source effort breakdown tables for BTC (and SOL if enabled).
