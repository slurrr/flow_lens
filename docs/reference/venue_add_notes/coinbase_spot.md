# Venue Add Notes — Coinbase Spot (`coinbase_spot`)

Status: working notes (Pass 1). Dev to complete endpoint and field-level confirmation during implementation.

Checklist:

- `docs/reference/venue-addition-checklist-phase1.md`
- endpoint/field reference: `docs/reference/venue-endpoints-and-fields-working-notes-2026-02-05.md`

## 0) Summary

- Venue: Coinbase
- Source id(s): `coinbase_spot`
- Symbol scope (Phase 1): BTC/SOL only (yes) — start with BTC-USD and SOL-USD
- Market type for X: `spot`
- Instrument class: `spot`
- Quote mode: `usd_like`
- Aggressor mode: `native` (Coinbase match messages include `side`)
- Price eligible + priority:
  - `price_eligible = true`
  - `price_priority = 90` (secondary to `binance_spot`=100 for Phase 1 stability)

## 1) Endpoints / Subscriptions

(To be confirmed during implementation; expected from tournament tooling)

- WS URL(s): `wss://ws-feed.exchange.coinbase.com`
- Auth requirements: none (public)
- Subscribe payload(s):
  - `{"type":"subscribe","product_ids":["BTC-USD","SOL-USD"],"channels":["matches"]}`
- Heartbeat/ping: (confirm if needed; adapter uses WS ping/pong)
- Expected cadence (qualitative): high for BTC; moderate for SOL
- Known limits / disconnect patterns: TBD

Confirm during implementation:

- Adapter subscribes to `matches` and only parses `type == "match"`.
- `product_id` mapped to base symbol via `split("-")[0]`.

## 2) Message field mapping

For `coinbase_spot`:

- Venue-native instrument id field → `AdapterEvent.symbol`:
  - `product_id` (e.g., `BTC-USD`)
- Canonical base symbol derivation → `AdapterEvent.base_symbol`:
  - `product_id.split("-")[0]` (e.g., `BTC`)
- Exchange timestamp field → `Event.timestamp` (ms since epoch):
  - `time` (ISO-8601)
- Price field → `Event.price` (USD-like):
  - `price` (string)
- Size field → `Event.effort_value` (definition):
  - `effort_value = price * size` where `size` is base qty from `size`
- Aggressor side field → `Event.aggressor_side`:
  - `side` (buy/sell) from `match` message

Assumption:

- Coinbase `match.side` is usable as aggressor side without additional inference. If we discover it’s not semantically
  equivalent to our current “aggressor” meaning, we must revisit capability flags and potentially treat it as inferred.

## 3) Unit conversion (quote handling)

- Stream is USD-quoted (`quote_mode=usd_like`), so no conversion required.
- USD≈USDT basis concerns: treat as acceptable bootstrap; monitor any selector switch churn or price delta discontinuities.

## 4) Failure modes / caveats

- Message types: ensure we only parse `type == "match"` (and ignore `subscriptions`, `error`, etc).
- Timestamp parsing: ISO-8601 parsing must be robust.
- Product availability: ensure SOL-USD is supported on the feed.
- Potential per-product throttling / intermittent gaps: monitor via selector switch + `price_series_unavailable`.

## 5) Config / registry entry (draft)

`config/app.toml` snippet to add (draft; finalize during implementation):

`[adapters.coinbase_spot]`

- `type = "coinbase_spot_ws"`
- `symbols = ["BTC","SOL"]` (base symbols; adapter maps to product ids internally)

`[sources.coinbase_spot]`

- `venue = "coinbase"`
- `instrument_class = "spot"`
- `market_type_for_x = "spot"`
- `price_eligible = true`
- `price_priority = <TBD>`
- `price_priority = 90`
- `has_size = true`
- `has_aggressor = true`
- `aggressor_mode = "native"`
- `quote_mode = "usd_like"`

## 6) Diagnostics expectations (what to look for)

- `price_series_unavailable`:
  - should be 0 (or extremely rare/explainable). Any non-trivial rate is a stop condition (FL-0061).
- Selector behavior:
  - switch frequency reasonable; reasons are `stale/recovered/priority`
  - if we set Coinbase as high priority, watch for churn between Coinbase and Binance spot (should be rare with confirm cycles).
- Per-source effort presence:
  - `coinbase_spot` shows up in per-source tables
  - `source_count_active` increases (>=3 when both Binance spot/perp and Coinbase spot are active)

## 7) Validation log

Replay gates:

- top1 BTC/SOL replay: pass (Phase 1 plumbing smoke)
  - summary: `docs/diagnostics/diagnostics-summary-20260205-121346_coinbase_phase1_smoke.txt`
  - note: Coinbase prints are not present in the replay dataset yet; this gate confirms config/registry/selector/diagnostics
    plumbing remains stable with `coinbase_spot` enabled.
  - `Series unavailable` rate: 0.00/m
  - selector switch churn: unchanged vs baseline (expected: none)

Live smoke:

- time window observed: 2026-02-05 13:07–13:31 (from `logs/flow_lens_diagnostics-20260205-130743-p00.jsonl`, ~23.7 min)
- diagnostics log file used: `logs/flow_lens_diagnostics-20260205-130743-p00.jsonl`
- diagnostics report: `docs/diagnostics/diagnostics-report-20260205-130743-BTC-SOL.txt`
- notes on price selector behavior:
  - `price_series_unavailable`: 0 (BTC/SOL)
  - selector switches: 0 (BTC/SOL)
  - price series used: `spot` 100% (BTC/SOL) for this window
  - `top_source_counts`: Coinbase shows up as a contributor (`coinbase_spot` appears for BTC and SOL)
- notes on lens coherence: UI looks good, Feeds show active, no notable difference in behavior, seems just like before only with another adapter (good)
- owner note (sign-off): phase 1 implementation complete and approved

Code health:

- `ruff check .`: pass (2026-02-05)
- `scripts/pyright.sh`: pass (2026-02-05)
