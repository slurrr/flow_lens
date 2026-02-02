# Flow Lens Phased Spec: Persistence Line + Opposition Gauge

## Purpose

Add **time-persistent effectiveness context** to the lens without changing existing instantaneous semantics:

- Keep dot behavior exactly as-is (instantaneous window truth).
- Add a persistence line first.
- Optionally add a counter-pressure gauge second.

This is a structural-flow enhancement, not a signal system.

---

## Non-Negotiables

- Dot `Y` remains instantaneous effectiveness (`Y_raw`/smoothed `Y`) at current window boundary.
- Dot `X`, size, halo, lean semantics remain unchanged.
- New visuals must be orthogonal:
  - Persistence line = persisted net effectiveness state.
  - Opposition gauge = opposing pressure buildup relative to persisted state.

---

## Core Definitions

Let:

- `A_t` = instantaneous signed acceptance strength (recommended: use current `Y_raw`).
- `S_t` = persisted acceptance/rejection state (new line state).
- `dt` = update interval in seconds.

### Persistence update (Phase 1)

Use leaky integration with asymmetric rates:

`S_t = clamp(S_{t-1}*(1 - decay(dt)) + build(dt)*A_t, -1, 1)`

Where:

- `build(dt) = 1 - exp(-dt / tau_build)`
- `decay(dt) = 1 - exp(-dt / tau_decay)`
- `tau_build > tau_decay` (slow build, faster fade)

Recommended starting ranges:

- `tau_build`: 45–120s
- `tau_decay`: 10–45s

Line can be smoothed separately for rendering only (optional).

---

## Phase 1 — Persistence Line Only

### Scope

1. Add `S_t` computation in engine update path (or a dedicated post-state layer if preferred).
2. Expose persistence state in snapshot/model.
3. Render a horizontal line at persistence level in lens.
4. Add diagnostics logging fields:
   - `persist_raw` (`S_t`)
   - `persist_slope` (`dS/dt`)
   - `persist_sign`

### Behavior goals

- Line rises/falls gradually with sustained acceptance/rejection.
- Line fades faster when acceptance disappears/reverses.
- Line supports both positive and negative persistence.
- Dot behavior remains identical to current system.

### Phase 1 acceptance checks

- No semantic change in existing dot channels.
- Line responds in trends and decays in consolidation.
- Replays/logs show expected asymmetry (build slower than decay).

---

## Phase 2 — Opposition Gauge (Optional, gated on Phase 1 UX)

Only implement if Phase 1 still lacks early visibility of opposition buildup.

### Pressure model

Use normalized signed pressure dominance from aggressor flow:

`PD_t = (BuyNotional - SellNotional) / (BuyNotional + SellNotional + eps)`  in `[-1,1]`

Relative to persistence direction:

- `CT_t = max(0, -sign(S_t) * PD_t)` (counter-trend pressure component)

Absorption-weighted opposition (recommended):

- `align_t = max(0, sign(PD_t) * A_t)`
- `AB_t = CT_t * (1 - align_t)`

Gauge state:

`O_t = clamp(O_{t-1}*(1 - o_decay(dt)) + o_build(dt)*AB_t, 0, 1)`

Display ratio (optional):

`R_t = O_t / (abs(S_t) + eps)`

### Gauge semantics

- Gauge increases when opposing pressure builds against persisted state.
- Gauge decreases when opposition weakens or gets accepted.
- High gauge + flattening/falling persistence line = rising reversal risk context.

### Phase 2 acceptance checks

- Gauge adds early opposition context without changing core dot semantics.
- No extra “signal” labels (buy/sell calls); purely state depiction.

---

## UX / Rendering Guidance

- Keep persistence line visually subtle but always visible.
- If gauge is added, place it as a compact side meter to avoid lens clutter.
- Avoid introducing extra colors/channels that overload existing meanings.

---

## Config Additions (proposed)

Phase 1:

- `persist_enabled` (bool)
- `persist_tau_build_s`
- `persist_tau_decay_s`

Phase 2:

- `opp_gauge_enabled` (bool)
- `opp_tau_build_s`
- `opp_tau_decay_s`
- `opp_use_absorption_weighting` (bool)

Defaults should be conservative and non-disruptive.

---

## Rollout / Test Plan

1. Implement Phase 1 only.
2. Replay top scenarios + live observation:
   - confirm line behavior in trend/chop/impulse.
3. Decide:
   - keep line-only, or
   - proceed to Phase 2 gauge.
4. If Phase 2 proceeds, evaluate clutter and usefulness; retain only if clearly additive.

---

## Decision Gate

- If Phase 1 line alone gives sufficient “acceptance over time” readability, stop there.
- If early opposition is still hard to read before line rollover, implement Phase 2 gauge.

