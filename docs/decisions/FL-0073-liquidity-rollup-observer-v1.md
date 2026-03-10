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

## Status

Proposed
