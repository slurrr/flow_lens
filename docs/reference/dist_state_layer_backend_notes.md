---
title: "Distribution State Layer (Backend Notes)"
created: 2026-03-04
status: "notes"
source_plan: "docs/reference/dist_state_layer_plan.md"
---

# Distribution State Layer (Backend Notes)

This document captures backend-facing ideas for incorporating the distribution-state layer into this repo **without changing the lens**.

It is intentionally not an implementation spec; it is a map of likely change locations, contracts, and guardrails.

## A) Hard guardrails (repo invariants + intent)

1. The lens engine remains the lens:
   - No changes to `X`, `Y`, dot size, halo, lean semantics.
   - No distribution-state values may gate/weight/normalize the lens.
2. The layer is single-symbol:
   - It follows the currently selected base symbol only (no multi-symbol dashboarding).
3. No “decision engine” behavior:
   - No alerts, no scoring, no trade recommendations.
   - Optional tokens/narrative are **bounded translations** of already-displayed metrics.

## B) Data inputs (what the layer needs)

The plan assumes *bar-level* inputs at a chosen horizon `H`:

- `close` (and preferably `open/high/low` for ATR)
- `timestamp` (bar close time, venue time is OK as metadata)
- `open interest` (OI) snapshots at or near bar close (perp-only)
- `funding` (sparse / stepwise; optional early phase)

Important: this can and should be sourced from venue-provided candle/klines where available.

### Practical implication for this repo

The existing adapters are trade-stream adapters. To avoid “bar construction in adapters” (existing invariant), the cleanest
approach is **new adapters that consume venue candle/OI/funding streams** and emit raw “bar events” (not computed metrics).

## C) Timebase / determinism

Flow Lens uses canonical recv-time for events (`Event.timestamp` = local receive time). For distribution-state events:

- Keep the same philosophy:
  - capture `ts_recv_ms` at ingest, keep it for ordering + determinism.
- Preserve venue bar close timestamp as metadata:
  - needed for UI display and for debugging feed alignment (but not as the “engine clock”).

This matches the “hygiene” mindset: observed-now semantics are defined by what the machine received.

## D) Suggested internal structure (new module boundary)

To prevent semantic bleed into the lens engine, treat distribution-state as a separate subsystem:

- New package boundary (illustrative):
  - `src/flow_lens/dist_state/`
    - `models.py` (bar input + computed state snapshot types)
    - `engine.py` (incremental update logic per timeframe)
    - `tokens.py` (optional deterministic token translation; bounded vocabulary)
    - `narrative.py` (optional cross-timeframe templates)

The layer should expose a small, pure output object per symbol:

- per timeframe:
  - bounded metrics `{V,S,A,P,T}` + bins/glyph levels
  - optional `token` (+ strength / transition modifier)
- optional:
  - global narrative template string + supporting “why” fields (debug-only)

## D.1) V1 scope contracts (captured)

For v1 we are explicitly choosing:

- **Perp-coherent rows** (price/OI/funding aligned within the same perp source).
- **Single source only** (no selector/failover runtime logic).
- `P` (OI positioning pressure) is first-class with explicit availability + bounded normalization.

See: `docs/reference/dist_state_layer_v1_contract.md`

## E) Computation style (streaming, bounded memory)

From the plan:

- Prefer **EWMA** for streaming variance/ATR-style calculations (O(1) memory).
- Use **small deques** only when needed for:
  - percentile ranks (V percentile, funding percentile),
  - optional autocorrelation windows.

Warmup:

- Needs roughly `~ 2 × half_life_bars` (EWMA stabilization).
- Can be bootstrapped via a short historical fetch (no DB; no disk persistence).

## F) Multi-timeframe orchestration

The plan’s UI implies 3–4 independent timeframes.

Backend options:

1. **Subscribe to multiple klines** (3m/15m/1h/4h) directly per venue (preferred if supported).
2. Subscribe to a base timeframe (e.g. 1m) and aggregate upward in a dedicated dist-state component.
   - Note: aggregation is bar construction and should not live in existing trade adapters.

Given repo constraints, option (1) is the lowest-risk: “adapters remain dumb; they only forward raw venue klines.”

## G) Integration point in the runtime loop

Current runtime layout (simplified):

adapters → `AdapterEvent(Event)` queue → hygiene → per-symbol pending → `EngineLoop.step()` → `Renderer.draw()`

Distribution-state layer should:

- run **in parallel**, not inside `StateEngine`.
- receive its own stream of “bar-ish” events (candles, OI, funding),
- update at bar-close cadence, and
- provide a `DistStateSnapshot` that the TUI can render.

This implies a new runtime state member, similar to `RuntimeState.loops` / `RuntimeState.hygiene`, e.g.:

- `runtime.dist_state` keyed by timeframe.

## H) Phase boundaries (for later spec work)

The plan naturally decomposes into phases that isolate risk:

1. **Bars only**: compute V/S/A/T from klines (no OI/funding yet).
2. **Add OI**: compute P (positioning pressure).
3. **Add tokens**: deterministic per-row token translation (bounded vocabulary).
4. **Add narrative** (optional): cross-timeframe templates, explicitly subordinate in UI.

These are not commitments—just clean cut-lines that keep the layer reviewable.

## I) Edge cases / “unknowns” to resolve before specs

- Venue availability and cadence for:
  - 3m klines (not universal),
  - OI history vs OI snapshot,
  - funding update mechanisms.
- Missing-data semantics:
  - what does “P unavailable” mean for token translation?
- Replay + diagnostics strategy:
  - whether dist-state participates in existing replay harness or ships with its own minimal replay feed.

## J) Binance v1 input reality check (probed)

We probed Binance USDT-margined futures inputs for `BTCUSDT` and confirmed:

- kline WS messages include an `is_closed` flag (`k.x`) suitable for bar-close updates,
- REST provides `openInterest` snapshots with timestamps, and
- REST provides `openInterestHist` for `5m/15m/1h/4h` periods (3m history should not be assumed).

Details: `docs/reference/dist_state_layer_binance_inputs.md`
