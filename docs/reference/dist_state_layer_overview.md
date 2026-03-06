---
title: "Distribution State Layer (Overview)"
created: 2026-03-04
status: "notes"
source_plan: "docs/reference/dist_state_layer_plan.md"
---

# Distribution State Layer (Overview)

This is a **new auxiliary layer** that can live alongside Flow Lens.

It does **not** modify the lens channels (`X`, `Y`, dot size, halo, lean) and must not feed back into the lens engine.

The layer exists to answer an “effects-only” question:

> What is the current **return distribution geometry** and where is it likely vulnerable?

This document is a cleaned capture of the core idea from `docs/reference/dist_state_layer_plan.md`, without implementation specs.

## 1) Effect framing (not “regime labeling”)

The plan reframes “regime” into a small set of directly observable effects:

- **Distribution shape / stretch** (where price sits relative to recent distribution)
- **Volatility state** (how wide outcomes are; compression vs expansion)
- **Participation / positioning pressure** (who is being forced in/out)
- **Short-horizon memory** (continuation vs decay)
- **Transition pressure** (compression → expansion risk, post-expansion decay)

We are not trying to name the world. We are trying to **bound the state of outcomes**.

## 2) Core metric set (V/S/A/P/T)

The plan proposes five observable metrics (all horizon-specific, computed on bar closes):

1. **V — Volatility state** (e.g. realized vol percentile or normalized vol level)
2. **S — Stretch** (z-extension: distance from rolling mean in vol units)
3. **A — Autocorrelation** (short-horizon persistence vs mean reversion bias)
4. **P — Positioning pressure** (OI change aligned vs opposed to price change)
5. **T — Transition pressure** (ATR short/long ratio; compression/expansion impulse)

Key constraint (Flow Lens style): values should be **dimensionless, bounded, and stable** before visualization.

## 3) “Rows” = timeframes (distribution geometry per horizon)

The UI concept is multiple timeframe rows for the *current symbol* (not a multi-symbol dashboard), e.g.:

- `3m` (micro-intraday structure)
- `15m` (intraday structure)
- `1h` (bridge)
- `4h` (anchor)

Each row is independent and answers:

> What is this timeframe structurally doing?

Cross-timeframe interpretation is a separate (optional) layer.

## 4) Token translation (optional, bounded)

The plan suggests mapping each row’s five metrics into a **small token set** for faster scanning.

Important guardrail: tokens are **not** free-form text; they are a small deterministic vocabulary.

V1 token vocabulary + deterministic mapping rules are captured in:

- `docs/decisions/FL-0071-dist-state-row-tokens-v1.md`

Proposed structural token set (per timeframe row):

- `COMP` — compression
- `EXP` — expansion
- `CONT↑` / `CONT↓` — continuation bias by direction
- `EXH↑` / `EXH↓` — exhaustion by direction
- `REVERT` — mean reversion bias
- `NEUT` — balanced / no strong edge

Optional modifiers:

- `++` — strong
- `!` — transition risk / instability rising

## 5) Cross-timeframe narrative (optional, template-based)

The plan also sketches a second layer that synthesizes row-tokens into a single **template-based narrative**:

- Alignment (lower TF aligned with higher TF)
- Conflict (lower TF fighting higher TF)
- Propagation (expansion/shift moving upward in TF stack)
- Local exhaustion (only lower TF exhausted vs structural exhaustion)

Guardrails:

1. Narrative must not contradict row tokens.
2. Narrative must be **template-based and bounded** (small set of templates).
3. Narrative is visually subordinate to the rows (it is interpretation, not measurement).

## 6) Architectural intent (high-level)

The plan prefers:

- **Candles as the correct abstraction** for distribution metrics.
- **OI / funding** as bar-synchronous inputs (not trade-level).
- **Streaming incremental math** (EWMA where possible; small bounded deques only where needed).
- **Warmup** via short historical fetch (no DB; no disk persistence), then live updates.

Details and integration points are captured in:

- `docs/reference/dist_state_layer_backend_notes.md`
- `docs/reference/dist_state_layer_tui_notes.md`

## 7) V1 scope contract (captured)

For v1, we are explicitly choosing:

- **Perp-coherent rows**: price/returns, OI, and funding (when present) come from the same perp instrument family.
- **Single source only**: no selector/failover policy in v1 (source selection is a configuration choice, not runtime logic).
- **P is first-class**: positioning pressure (`P`) is not optional in the dist model; it must have explicit availability and
  normalization rules (missingness is shown as missing, never guessed).

See: `docs/reference/dist_state_layer_v1_contract.md`
