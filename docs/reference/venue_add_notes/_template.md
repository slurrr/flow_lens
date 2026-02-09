# Venue Add Notes — Template

Status: per-venue working notes. Copy this file for each new venue/source addition and keep it updated through rollout.

## 0) Summary

- Venue:
- Source id(s):
- Symbol scope (Phase 1): BTC/SOL only (yes/no)
- Market type for X: spot/perp
- Instrument class:
- Quote mode: usd_like/converted/foreign
- Aggressor mode: native/inferred/none
- Price eligible + priority:

## 1) Endpoints / Subscriptions

- WS URL(s):
- Auth requirements:
- Subscribe payload(s):
- Heartbeat/ping:
- Expected cadence (qualitative):
- Known limits / disconnect patterns:

## 2) Message field mapping

For each source id:

- Venue-native instrument id field → `AdapterEvent.symbol`:
- Canonical base symbol derivation → `AdapterEvent.base_symbol`:
- Exchange timestamp field → `Event.timestamp` (ms since epoch):
- Price field → `Event.price` (USD-like):
- Size field → `Event.effort_value` (definition):
- Aggressor side field → `Event.aggressor_side`:

If `aggressor_mode=inferred`:

- BBO feed/channel:
- BBO fields:
- Inference method: must match FL-0058 (trade vs BBO, mid/tick fallbacks)
- Planned `bbo_max_age_ms`:

## 3) Unit conversion (quote handling)

- Is the stream USD/USDT/USDC already?
- If converted: what rate feed(s) are used?
- Any basis concerns (USD≈USDT≈USDC assumptions)?

## 4) Failure modes / caveats

- Missing fields:
- Timestamp quality:
- Size quality (is size reliable?):
- Trade aggregation/conflation:
- Session gaps:
- Geo-block / availability:

## 5) Config / registry entry (draft)

`config/app.toml` snippet to add:

- `[adapters.<source_id>]`:
- `[sources.<source_id>]`:

## 6) Diagnostics expectations (what to look for)

- Inference diagnostics:
  - `%unknown_side`, `bbo_age_ms_p95` gates (if inferred)
- Selector behavior:
  - switch frequency reasonable; reasons are `stale/recovered/priority`
  - `price_series_unavailable` count/rate is 0 (or explained and fixed before merge)
- Per-source effort presence:
  - source shows up in per-source tables

## 7) Validation log

Replay gates:

- top1 BTC/SOL replay: pass/fail + notes
- selector churn check: pass/fail + notes

Live smoke:

- time window observed:
- diagnostics log file used:
  - note: live diagnostics logs are written as timestamped parts:
    `logs/flow_lens_diagnostics-<YYYYMMDD-HHMMSS>-pNN.jsonl`
  - simplest workflow: run `scripts/diagnostics_report.py` without `--path` to auto-pick the latest log in `logs/`
- notes on price selector behavior:
- notes on lens coherence:
