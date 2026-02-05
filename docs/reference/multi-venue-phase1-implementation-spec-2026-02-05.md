# Multi‑Venue Phase 1 Implementation Spec — 2026-02-05

Status: implementation spec. This should be followed after the Phase 1 contract is locked.

Contract reference:

- `docs/reference/multi-venue-adapter-architecture-contract-wip-2026-02-04.md`

Required decisions (must exist before merge):

- `FL-0057` base_symbol contract + migration
- `FL-0058` canonical aggressor inference + diagnostics gates
- `FL-0059` filter context reset scope
- `FL-0060` multi-source price selector policy + switch logging

## 0) Non-goals (Phase 1)

- No “leader-based” price selector (policy must be pluggable, but default remains `priority_sticky`).
- No options integration into effort semantics.
- No cohorts.
- No UI venue dropdown yet (only plumbing to make it possible later).

## 1) Phase 1 deliverable definition

After Phase 1, Flow Lens can:

1) ingest multiple sources (≥2 venues) for the same base symbol,
2) route events by canonical `base_symbol` without hardcoded Binance symbol mapping,
3) compute lens state from all enabled sources, and
4) select a reference price series from multiple price-eligible sources with conservative hysteresis and full audit logs.

## 2) Data contract changes (minimal)

### 2.1 `AdapterEvent`

- Add `base_symbol: str | None` (required for all non-legacy adapters).
- Keep `symbol` as venue-native instrument id.
- Router behavior:
  - route by `base_symbol` when present
  - fallback to legacy mapping only when `base_symbol` is missing (temporary migration window)

### 2.2 Source registry (config-backed)

Introduce a canonical per-source registry describing each `source_id`:

- `source_id`, `venue`, `instrument_class`
- `market_type_for_x` (`spot|perp`)
- `price_eligible` (bool), `price_priority` (int)
- capability flags: `has_size`, `has_aggressor`, `aggressor_mode`, `quote_mode`

Add a startup validator that fails fast on:

- duplicates, missing required fields, contradictory capability combinations

## 3) Diagnostics schema additions (must-have)

Per tick (per symbol):

- `active_price_source_id`
- `selector_policy`
- `price_series_side` (spot/perp) if still tracked

On price switch (structured event):

- `from_source_id`, `to_source_id`
- `reason`
- `staleness_from_ms`, `staleness_to_ms`
- `priority_from`, `priority_to`

Inference diagnostics (per source, per capture/replay):

- `aggressor_mode`
- `% inferred_with_bbo`, `% inferred_mid_fallback`, `% inferred_tick_rule_fallback`
- `% unknown_side`
- `bbo_age_ms_p50/p95`

Filter reset (structured event):

- event type: `filter_context_reset`
- old/new filter masks
- per-symbol reset confirmation (what reset was applied)

## 4) Engine/buffer plumbing changes (high level)

### 4.1 Rolling buffer

- Store last price per `(base_symbol, source_id)`.
- Store staleness per `(base_symbol, source_id)`.
- Keep events as a superset store for Δ.

### 4.2 Price selector

- Implement policy interface + default `priority_sticky`.
- Ensure tie-break determinism when priorities match.
- Emit per-tick `active_price_source_id` and explicit switch logs.

### 4.3 Aggregation and filtering hooks (future)

- Add a hook point to apply a `source_allowlist` during aggregation (off by default in Phase 1).
- Define filter reset action list (per `FL-0059`) even if UI control is not yet present.

## 5) Phase 1 validation gates (must pass)

### 5.1 Stability gates

- Existing tuning/diagnostics gates on BTC and SOL remain green (top1 sanity).
- No increase in price-series switching churn beyond expected staleness behavior (auditable via switch logs).

### 5.2 Inference gates (if any inferred sources are enabled)

- `%unknown_side` and `bbo_age_ms_p95` meet the locked defaults.
- No silent fallback to unsigned semantics.

### 5.3 Manual UI sanity pass (required)

During a volatile window:

- verify dot X/Y semantics remain coherent
- verify persistence line color/provenance behaves as expected
- verify price series does not “teleport” (selector switches are explainable)

## 6) Implementation sequencing (recommended)

1) Add `base_symbol` field + router migration fallback.
2) Add source registry + validator.
3) Extend diagnostics schema (selector + inference + filter reset).
4) Generalize price selector (policy interface + `priority_sticky`) and add switch logs.
5) Only then integrate the first new venue adapter (Coinbase spot is the recommended first non-Binance source).
6) Replay-gate BTC+SOL, then run a live sanity pass.

