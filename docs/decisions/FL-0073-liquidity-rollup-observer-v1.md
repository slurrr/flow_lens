---
id: FL-0073
title: "Liquidity Rollup Observer (V1): Time-Weighted Interval Summaries From Lens State"
status: Proposed
created: 2026-03-10
related:
  - "docs/decisions/FL-0064-canonical-event-timebase-recv.md"
  - "docs/decisions/FL-0067-hygiene-metrics-logged-not-ui.md"
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
  - "docs/decisions/FL-0072-dist-state-narrative-layer-v1.md"
  - "SPEC-liquidity-rollup-layer-v1.md"
---

# FL-0073 — Liquidity Rollup Observer (V1): Time-Weighted Interval Summaries From Lens State

## Decision

Add a **liquidity rollup observer** that summarizes the live Flow Lens state into **time-weighted interval rollups**
(v1 cadence: **15m**) and supports a **rolling 24h** “daily” summary/report.

The rollup observer:

- consumes the existing, already-computed runtime tick inputs/outputs:
  - the raw per-tick `Event` stream already processed by the engine loop step (spot/perp × buy/sell effort events), and
  - `StateSnapshot` (structure outcome + stability),
- is **observer-only** (does not modify lens computation, adapter behavior, or dist-state computation),
- produces a compact rollup record that is meaningful even when the dot is highly dynamic,
- persists rollups as **append-only JSONL** (no database required for v1).

Persistence/ownership constraints:

- Daily JSONL persistence is **single-writer per symbol/output directory**.
- Concurrent writers must not append to the same daily rollup file.
- The implementation may include a `run_id` to distinguish writer sessions, but `run_id` is not a substitute for the
  single-writer lock.

Gap semantics:

- The rollup log is not guaranteed to be contiguous across process downtime.
- Consumers must derive continuity from interval timestamps (and optionally `run_id`), not from file row order alone.

For implementation convenience, the persisted JSONL object may also include an **aligned combined snapshot** containing
the liquidity rollup plus optional dist-state context for the same 15m boundary.

This combined logged shape is intentionally **not locked yet**:

- it is a working contract for implementation,
- fields may be added, renamed, nested differently, or removed during iteration,
- no schema version field is required yet.

Suggested combined snapshot shape (informal):

- `ts_ms`
- `symbol`
- `interval_start_ms`
- `interval_end_ms`
- `liquidity_state`
  - full liquidity rollup object
- `dist_state` (optional)
  - latest aligned 15m dist-state snapshot
  - may include row metrics, row tokens, stack vector, primary/secondary class, and narrative fields
- `agent_inputs` (optional)
  - convenience block for non-deterministic consumers
- `context` (optional)
  - loose additional market/session context

Implementation intent:

- persist enough raw bounded deterministic context that research and agent iteration can discover the durable schema,
  rather than over-optimizing query ergonomics before the implementation settles.

## Rationale

The lens behaves like a continuous impulse seismograph. A single point-in-time snapshot at a cadence (e.g. every 15m)
is not representative. Time-weighted rollups answer the trader’s real questions:

- who was in control *most of the time*,
- whether effort was accepted vs rejected *most of the time*,
- what the effort composition looked like (spot/perp × buy/sell) while those outcomes were occurring,
- where force/dispersion spiked (highlights),
- how the persistence line behaved over the interval (optional but recorded for research),
- whether conditions were stable vs noisy (basic transition counters / quality flags).

Append-only JSONL is the smallest persistence surface that enables:

- “what happened while I was away?” catch-up,
- 24h summaries without storing raw trades or bars,
- later research workflows if desired (e.g. DuckDB over JSONL) without committing the repo to database infrastructure.

Keeping the logged shape evolvable is appropriate at this phase. The project needs clean, transformed, research-ready
state first; strict schema lock-down can follow once the implementation reveals which fields are actually durable and
useful.

## Status

Proposed
