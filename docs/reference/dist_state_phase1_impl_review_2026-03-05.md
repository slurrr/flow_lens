---
title: "Dist-State Phase 1 Implementation Review"
created: 2026-03-05
scope: "review-only"
---

# Dist-State Phase 1 Implementation Review (2026-03-05)

Scope: review the current Phase 1 implementation against `SPEC-dist-state-layer-phase1.md`, focusing on places where `P`
could struggle to populate and on math/contract fidelity. No code changes in this document.

## 1) What matches the spec well

- **Perp-coherent, single-source**: implementation is hard-wired to Binance USDT-perp (`BTCUSDT`) and does not attempt
  runtime multi-source selection.
- **Bar-close only**: dist updates are triggered only when kline `x == true`.
- **Idempotency / out-of-order**: `_accept_close()` maintains per-row processed close tracking and rejects older-than-last.
- **Strict venue time policy for OI**:
  - OI bucket is accepted only if `oi_venue_time_ms` is present and within `oi_join_tolerance_ms` of `kline_close_ms`.
- **3m variance seeding from 5m**:
  - half-life mapping `hl_oi_bars_5m = hl_oi_bars_3m * (3/5)`
  - variance scaling `var_oi_3m = var_oi_5m * (3/5)`
- **V in [0,1]** and binned separately via `_bin_unit()` (consistent with the spec’s final redline).

## 2) Main risk: multi-timeframe P can miss systematically due to event ordering

### What happens

The OI bucket is created only when processing the `3m` close event:

- `BinancePerpDistFeed` attaches `(oi, oi_venue_time_ms)` only for `tf == "3m"`.
- `DistStateEngine` stores an OI bucket only inside `on_kline_close()` when `event.tf == "3m"`.

For aligned closes (e.g., a `15m` close that occurs on a `3m` boundary), the `15m/1h/4h` row will compute `P` only if
the OI bucket for `(source_id, kline_close_ms)` already exists *at the moment that row’s close is processed*.

Because the feed processes a multiplexed WS stream, close-event arrival order across intervals is not explicitly
controlled. If a `15m` close message is received before the corresponding `3m` close message for the same `kline_close_ms`:

- the `15m` row is processed first,
- the OI bucket is still absent (because `3m` has not been processed),
- `P` is marked unavailable for that `15m` close,
- the row close cannot be re-processed due to idempotency.

### Why this can be worse than “rare”

Even if Binance tends to deliver `3m` before `15m`, the `3m` close path includes a REST OI fetch (awaited via
`asyncio.to_thread`). If the feed receives the `3m` close first, it blocks before yielding the event until the REST call
returns; if the feed receives a higher-TF close first, it yields it immediately.

Net: `P` population for `15m/1h/4h` is sensitive to timing and stream ordering and can plausibly be intermittently missing
exactly on the bars where it matters most (aligned higher-TF closes).

### Stabilization guidance (architecture-level, not code)

To make `P` robust, the pipeline needs a deterministic rule ensuring:

- **the OI sample for a close bucket is obtained/stored before any TF close for that bucket is applied**, or
- aligned TF closes are staged until the OI bucket decision is known.

Examples of deterministic remedies (later implementation work):

- drain-sort rule in the runtime loop: group drained close events by `kline_close_ms` and process `3m` first within each
  group (so the OI bucket exists before higher TFs are applied).
- decouple OI sampling from the `3m` event object: emit a distinct OI event keyed by `kline_close_ms`, store it, then
  process closes (so no TF depends on arrival order).

## 3) Secondary risk: warmup OI history likely includes an in-progress bucket

The warmup OI fetch (`openInterestHist`) is parsed only as `sumOpenInterest` values; it does not inspect timestamps and may
include the most recent in-progress bucket.

Impact:

- seeding could be biased slightly by partial-bucket behavior.
- this affects `P` scaling early after startup and can create subtle differences across runs.

Stabilization guidance:

- treat the last OI-history point as potentially in-progress and drop it if it is “too recent” vs server time, or fetch one
  extra point and explicitly use only completed deltas.

## 4) Math sanity notes (as implemented)

- `P` uses `sign(r_t)` from the **row’s timeframe return**. This means:
  - if the candle return is effectively 0 (within EPS), `P` becomes 0 even if `ΔOI` is nonzero.
  - this matches the “aligned-with-price” intent but can suppress `P` on flat closes.
- `ready_p` can be true while `P` is None on a specific bar:
  - correct under the spec’s missingness rules, but worth remembering in operator interpretation.

## 5) Recommended “stability logging” (doc-only suggestion)

If we see unexpected `P` blanks live, the first things to log (diagnostics file only, not UI) are:

- bucket join failures:
  - `venue_time_ms missing`,
  - `abs(venue_time_ms - close_ms) > tolerance`,
  - bucket absent at higher-TF close (ordering).
- counts per TF of “close processed with no bucket”.

This will quickly distinguish “Binance data” issues from “ordering” issues.

