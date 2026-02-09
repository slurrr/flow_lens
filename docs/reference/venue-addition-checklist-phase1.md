# Venue Addition Checklist (Phase 1) — Canonical

Status: canonical checklist for adding a new adapter/source to Flow Lens under the Phase 1 multi-venue contract.

Contract + decisions this checklist assumes:

- `docs/reference/multi-venue-adapter-architecture-contract-wip-2026-02-04.md`
- `docs/decisions/FL-0057-base-symbol-routing-and-adapterevent-contract.md`
- `docs/decisions/FL-0058-canonical-aggressor-inference-trade-vs-bbo.md`
- `docs/decisions/FL-0059-filter-context-reset-is-full-per-symbol.md`
- `docs/decisions/FL-0060-multi-source-price-selector-policy-sticky.md`
- `docs/decisions/FL-0061-price-series-requires-eligible-source.md`

Per-venue notes should be recorded using:

- `docs/reference/venue_add_notes/_template.md`
- Endpoint/field reference (pre-implementation): `docs/reference/venue-endpoints-and-fields-working-notes-2026-02-05.md`

## 0) Rule of thumb (don’t skip)

For any new venue/source:

- Adapters stay dumb (parsing + unit conversion only).
- Capability gaps must be explicit (no silent inference/unsigned behavior).
- Pass replay gates first; then live smoke.

## 1) Define the source(s) (paper step)

- Source id(s): `venue_market` (e.g. `coinbase_spot`, `okx_perp`).
- Market type for X: `spot` or `perp` (do not invent new X categories without a decision).
- Instrument class: spot/perp/futures/options/etc (informational unless a decision says otherwise).

## 2) Endpoint feasibility (paper step)

Record in per-venue notes:

- WS endpoint(s) and subscription payload(s)
- expected cadence and rate limits
- auth requirements (none / API key / subscription)

## 3) Capability declaration (must be config-driven)

Decide per source:

- `has_size` (bool)
- `has_aggressor` (bool)
- `aggressor_mode` (`native|inferred|none`)
  - If `inferred`: confirm a BBO feed exists and meets `bbo_max_age_ms` gate (FL-0058).
  - If `none`: stop; Phase 1 default disables it unless an explicit decision enables it.
- `quote_mode` (`usd_like|converted|foreign`)
  - If `foreign`: stop unless conversion plumbing is part of this change set.
- `price_eligible` + `price_priority`

## 4) Data mapping (adapter contract)

Map venue fields to:

- `base_symbol` (required)
- venue-native `symbol` / instrument id (kept for traceability)
- `timestamp_ms` (exchange if available; else recv)
- `price_usd`
- `size` → `effort_value` (`price_usd * size_base` when available)
- `aggressor_side`

## 5) Implementation (adapter only)

- Add adapter module(s) + integration wiring.
- Add/extend `[sources.*]` in `config/app.toml`.
- Ensure `source_registry` validation passes.
- Update TUI feed counts to reflect any new adapters so spot/perp “Feeds X/Y” remain accurate.
- If the venue needs multi-pair resolution (multiple quotes), implement a per-venue resolver in orchestration (not adapter);
  reuse a shared resolution shape/interface if helpful, but keep venue logic separate.
  Reference sketch: `docs/reference/venue-resolver-interface-sketch.md`.

Non-goal: do not change engine semantics while adding a venue unless required by an explicit decision.

## 6) Diagnostics wiring (required)

Before any UI judgment, confirm the capture/replay includes:

- `active_price_source_id`, `selector_policy`, `price_series_side/used`
- `price_source_switch` events with reason + staleness fields
- `price_series_unavailable` events are zero, or explicitly explainable (e.g., all eligible sources were stale/misconfigured)
- inference diagnostics records (even if all zeros for `native`)

## 7) Replay gates (required)

- Run top1 BTC+SOL replay suite and compare to baseline:
  - no new saturation regressions (Y/S)
  - no selector churn explosion
  - no new direction mismatches
- If the new source is enabled in replay, verify it contributes as intended:
  - shows up in per-source effort tables
  - doesn’t break price continuity

## 8) Live smoke (required)

During a volatile window:

- price series behavior looks continuous (switches are explainable via logs)
- X/Y/persistence remain coherent when watching alongside a price chart
- no adapter stalls or reconnect storms
- diagnostics report points at the correct live log file (timestamped `logs/flow_lens_diagnostics-...-pNN.jsonl`)

## 9) “Stop conditions” (do not merge until resolved)

- missing or unstable `base_symbol` routing
- inference gates failing (`%unknown_side`, stale BBO)
- selector switch churn that is not explained by staleness
- non-trivial `price_series_unavailable` rate (usually indicates price_eligible misconfig or a stalling feed)
- any semantic change to a visual channel without a decision record
