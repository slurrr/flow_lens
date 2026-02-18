# Venue Tournament Lessons — Time Hygiene + Data Quality (Lens-Oriented)

This document captures non-score lessons from the venue discovery tournament that are relevant to **Flow Lens correctness and stability**.

The tournament work made one thing very clear: the biggest practical risk to the lens is not “who leads by 20ms”, it’s **time hygiene** (stale bursts, timestamp semantics, and freshness logic that is accidentally keyed off venue timestamps).

## Executive Summary (What To Carry Into The Lens)

- The lens should treat **local receive time (`recv`) as the canonical clock** for rolling windows, staleness, and “what we saw first”.
- Venue-provided timestamps are still useful, but primarily for **hygiene detection** (stale-on-arrival / history bursts), not for ordering the engine timeline.
- “Stale-on-arrival” trades exist in real life (reconnect history, delayed prints, SDK bursts). If we ingest them, they can **pollute X/Y/halo** and also **mislead price selection**.
- Current engine/buffer semantics assume `Event.timestamp` shares a clock with `now_ms`. If `Event.timestamp` is an exchange timestamp (often offset from local by seconds), freshness and windowing can silently become wrong.

## What We Observed In The Tournament (Behavior, Not Rankings)

### 1) Venue timestamps are not a reliable shared clock

Across blocks, we observed `ts_recv_ms - ts_exchange_ms` medians that can be materially non-zero (often on the order of seconds) and can shift by block/session.

Implications:

- If we use venue timestamps as “event time” inside the lens, `staleness_ms = now_ms - event.timestamp` can be biased or even negative (clamped to 0 in some codepaths), making a dead feed look artificially fresh.
- “Exchange-local” corrections can help in an offline analysis context, but in live operation they are fragile unless we continuously estimate offsets and treat them as diagnostics rather than truth.

### 2) Stale-on-arrival is real (and it matters more than fine-grained leadership)

We repeatedly encountered patterns consistent with:

- reconnect backfills (venue sends older trades on connection),
- bursty deliveries that include prints far older than the current stream,
- venues with “history-like” initial payloads (notably when using SDKs).

Offline, this manifests as `wire_lag_ms` outliers (large positive `ts_recv_ms - ts_venue_ms`).
Online, it manifests as sudden bursts that can:

- create fake “effort intensity” spikes,
- create false dispersion (halo inflation),
- bias effectiveness (Y) if stale trades cluster around a move that has already happened,
- disrupt price series selection by making a source look “active” when it is not.

### 3) Bucket quantization hides “same bucket” ties; ms crossing reduces that

The `crossing_time_mode=ms` experiment (“t_cross_ms”) showed that using per-bucket effective timestamps reduces quantization artifacts, especially in lower-liquidity slices.

Lens implication:

- This does not mean the lens should chase ms-precision leadership.
- It does reinforce that **coarse timing artifacts exist** and can dominate conclusions if hygiene is not handled first.

### 4) Dedupe is not optional

Reconnects and some delivery patterns can repeat trades. Without dedupe, the lens can double-count effort.

Lens implication:

- Every venue adapter should expose a stable per-trade id when possible, and the ingestion layer should dedupe by `(source_id, symbol, trade_id)` within a bounded time window.

## Where This Can Affect The Lens Today

Flow Lens currently uses `Event.timestamp` everywhere as if it is “ms since epoch on the same clock as `now_ms`”.

These areas are sensitive:

- Rolling window expiry: `RollingEventBuffer.expire(now_timestamp)` removes events by `event.timestamp < now - window`.
- Source staleness: `staleness_ms = now_timestamp - last_timestamp_by_source[source_id]`.
- Price source selection: `PriorityStickySelector` uses staleness to fail over and recover sources.
- Runtime stepping cadence: `_update_state()` uses `now_ms - runtime.last_event_ms[symbol] <= cutoff` to decide whether to step the engine even if no events arrived.

If adapter timestamps are venue timestamps (not receive timestamps), all of the above can be biased.

## Recommended Lens Updates (Hygiene-First, Non-Interpretive)

These are proposed implementation directions; each non-trivial change should be captured as an `FL-XXXX` decision before coding.

### A) Make “engine time” deterministic: use receive time for windowing + staleness

Recommendation:

- Define `Event.timestamp` (engine timeline) to be **local receive time** (ms since epoch from this machine).
- Carry the venue timestamp separately (optional field) for hygiene/diagnostics only.

Rationale:

- The lens is a real-time structural diagnostic. The semantics of “what is in the active window Δ” should match what we actually observed in real time.
- This immediately makes staleness, expiry, and failover behavior meaningful and stable.

Notes:

- This is consistent with tournament emphasis that `recv` is “where it counts” for a live lens.
- It also makes the existing `price_selector_stale_failover_ms` behave as intended.

### B) Add stale-on-arrival filtering (one-sided lag filter)

Recommendation:

- Compute `wire_lag_ms = ts_recv_ms - ts_venue_ms` when the venue timestamp is available.
- Drop messages when `wire_lag_ms` exceeds a configured threshold, or exceeds a robust per-source baseline by a threshold.

Two practical options:

- Fixed threshold: drop if `wire_lag_ms > max_wire_lag_ms` (tourney default that behaved well: `2000ms`).
- Adaptive threshold: maintain a rolling robust center (median) per source and drop if `wire_lag_ms > median + max_excess_ms`.

Rules that keep this semantically “hygiene” rather than “interpretation”:

- Filter is only applied to extremely stale data (history), not micro-latency differences.
- Filter is one-sided: do not drop negative lag (local clock/venue clock offsets can produce negative values).

### C) Add reconnect gating for known “history burst” venues

Recommendation:

- On (re)connect, apply a short gating window where only “fresh enough” messages are allowed through.
- Prefer gating based on `wire_lag_ms` (freshness) rather than blindly ignoring the first N seconds of *all* data.

Rationale:

- Preserves real-time sensitivity at startup while still blocking history payloads.

### D) Implement bounded dedupe at ingestion

Recommendation:

- Maintain a bounded cache keyed by `(source_id, symbol, trade_id)` with TTL (e.g. 10–60 seconds).
- Drop duplicates and count them in adapter/ingestion stats.

Rationale:

- Prevents over-counting effort during reconnects, retries, and redundant streams.

### E) Surface hygiene health in diagnostics/UI (without changing semantics)

Recommendation:

- Track and optionally log:
  - stale-on-arrival dropped count per source,
  - dedupe dropped count per source,
  - rolling `wire_lag_ms` median and p95 (when venue timestamps exist),
  - “future timestamp” events (venue timestamp ahead of receive time by suspicious amount).

Rationale:

- This helps interpret lens output when something looks “wrong” without adding any opinionated logic.

## Suggested Defaults (From Tournament Behavior)

These are starting points for a future spec/decision:

- `max_wire_lag_ms`: `2000`
- `warmup_gate_s` on reconnect: `5–15` seconds, but prefer `wire_lag`-based gating over time-only gating.
- `dedupe_ttl_s`: `30`

## What Not To Do (To Preserve Lens Intent)

- Do not attempt to “correct” venue timestamps into a shared clock and then use that as the engine timeline unless we are willing to own the complexity (continuous offset estimation, drift handling, and venue timestamp semantics differences).
- Do not use hygiene metrics as scoring signals inside the lens (keep channels sacred; hygiene is about input quality, not interpretation).

