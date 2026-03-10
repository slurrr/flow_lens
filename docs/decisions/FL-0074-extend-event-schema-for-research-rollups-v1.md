---
id: FL-0074
title: "Extend Event Schema (V1): Preserve Trade Quantity for Research + POC Rollups"
status: Proposed
created: 2026-03-10
related:
  - "AGENTS.md"
  - "docs/decisions/FL-0064-canonical-event-timebase-recv.md"
  - "docs/decisions/FL-0073-liquidity-rollup-observer-v1.md"
  - "SPEC-liquidity-rollup-layer-v1.md"
---

# FL-0074 — Extend Event Schema (V1): Preserve Trade Quantity for Research + POC Rollups

## Decision

Extend the internal `Event` record to optionally preserve **base quantity** (and derived quote notional) alongside the
existing `effort_value` and `price`.

V1 additions:

- `base_qty: float | None` (e.g. BTC size)
- `quote_qty: float | None` (e.g. USDT notional; may equal `effort_value` when `effort_value = price * base_qty`)

Adapters already parse trade size to compute `effort_value`; this change preserves that size so rollups can compute:

- price POC by **base volume** (true volume POC), and
- price POC by **notional** (quote-volume POC),

without requiring raw trade persistence or a second ingestion pipeline.

Note (v1):

- Base volume POC can be derived today as `base_qty = effort_value / price` because adapters currently set
  `effort_value = price * size`.
- Preserving `base_qty` explicitly is a robustness upgrade (guards against future changes to `effort_value` semantics and
  enables consistency checks).

Guardrails:

- Lens semantics remain unchanged: all existing lens computations continue to use `effort_value` exactly as today.
- The new fields are for rollups/research/agent/reporting only.

## Rationale

The liquidity rollup layer is intended to be **research-ready**. “POC” and volume-profile style questions require a
volume measure at price. `effort_value` alone is not always sufficient, because it is a quote-notional proxy and cannot
represent “true volume POC” in base units.

Preserving the already-observed base quantity in the `Event` schema enables truthful, queryable rollups without adding a
database, and without changing the core lens behavior.

## Status

Proposed
