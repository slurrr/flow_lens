---
title: "SPEC — Distribution State Layer (Phase 2: Row Tokens + TUI)"
created: 2026-03-06
status: "draft"
related:
  - "SPEC-dist-state-layer-phase1.md"
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
  - "docs/decisions/FL-0070-open-interest-sampling-contract.md"
  - "docs/decisions/FL-0071-dist-state-row-tokens-v1.md"
  - "docs/reference/dist_state_layer_tui_notes.md"
---

# SPEC — Distribution State Layer (Phase 2: Row Tokens + TUI)

Phase 2 adds an optional **row token** translation layer for the dist-state panel.

It does not change the underlying `V/S/A/P/T` math, and it does not add cross-timeframe narrative.

## 0) Goals

1. Compute a deterministic per-row token from continuous dist-state row metrics (`V/S/A/T`, `P` as modifier only).
2. Render the token in the TUI in a visually subordinate way (ribbons remain primary).
3. Keep token behavior stable (no flicker, no free-form text, no “opinions”).

## 1) Non-goals

1. No Flow Lens changes.
2. No “narrative line” synthesis across rows.
3. No alerts, scores, thresholds-as-signals, or action language.

## 2) Inputs

Phase 2 consumes only the Phase 1 output snapshot:

- `DistRowSnapshot.metrics` (`V, S, A, P, T`)
- `DistRowSnapshot.bins` (rendering only; do not classify from bins)
- `DistRowSnapshot.ready_core`, `DistRowSnapshot.ready_p`

Token classification must not be performed from display bins; it must read continuous bounded metrics.

Minimal per-row token state (required):

- previous metrics (`V,S,A,P,T`) for 1-step deltas (direction of change),
- previous token and a dwell counter (stability).

## 3) Token computation (binding)

Token vocabulary, modifiers, and mapping rules are controlled by:

- `docs/decisions/FL-0071-dist-state-row-tokens-v1.md`

Phase 2 must mirror `FL-0071` rule ordering exactly (no “interpretation drift” between spec and implementation).

Implementation notes:

- Token updates only when the row is updated (bar close).
- Token mapping must not require historical buffers beyond the previous snapshot + token dwell state.
- Structural token selection uses `V/S/A/T` only; `P` is modifier-only and never blocks token assignment.
- Classification uses hysteresis (enter/exit thresholds) and minimum hold bars to reduce churn.

### 3.1 Missingness

Rules:

- if `ready_core == false`: `token = None`, `token_strength = None`.
- if `ready_core == true`: token is optional; default is `None` when no token predicate matches.
- `NEUT` must not be used as a catch-all default; it is emitted only when an explicit neutral predicate is true.

## 3.2 V1 starter predicates (Phase 2; concrete)

These are v1 initial thresholds intended for tuning. They are deterministic and apply per-row/per-timeframe.

Directional deadband:

- `S_dir = up` if `S >= s_dir_deadband`
- `S_dir = down` if `S <= -s_dir_deadband`
- `S_dir = neutral` otherwise

Core thresholds (hysteresis pairs):

- expansion impulse (`EXP`) from `T`:
  - enter if `T >= t_exp_enter`
  - exit if `T <= t_exp_exit`
- compression impulse (`COMP` predicate) from `T`:
  - enter if `T <= t_comp_enter`
  - exit if `T >= t_comp_exit`
- continuation bias (`CONT` predicate) from `A`:
  - enter if `A >= a_cont_enter`
  - exit if `A <= a_cont_exit`
- reversion bias (`REVERT` predicate) from `A`:
  - enter if `A <= a_revert_enter`
  - exit if `A >= a_revert_exit`
- extension magnitude for exhaustion gating (`EXH` predicate) from `S`:
  - extended enter if `abs(S) >= s_ext_enter`
  - extended exit if `abs(S) <= s_ext_exit`
- low-vol state for compression (`v_low`) from `V`:
  - `v_low` iff `V <= v_low_threshold`

Token precedence (first match wins; from `FL-0071`):

1. `EXP` if expansion impulse is true.
2. `EXH↑/EXH↓` if:
   - `extended` is true,
   - `S_dir` not neutral,
   - and (reversion bias is true OR compression impulse is true),
   - and expansion impulse is false.
3. `CONT↑/CONT↓` if:
   - continuation bias is true,
   - `S_dir` not neutral,
   - `extended` is false,
   - and expansion impulse is false.
4. `REVERT` if:
   - reversion bias is true,
   - `abs(S) >= s_revert_min_stretch` (avoid labeling pure noise),
   - and expansion impulse is false.
5. `COMP` if compression impulse is true AND `v_low` is true AND expansion impulse is false.
6. `NEUT` if explicit neutral predicate is true.
7. else `None` (no token).

Dwell / minimum hold (stability):

- enforce `token_min_hold_bars` per timeframe before changing tokens, except:
  - `EXP` may override immediately (highest precedence),
  - `EXH` may override immediately (second precedence).

## 3.3 Modifiers (v1 starter)

Strength (`+` / `++`) is derived from distance-from-neutral of the controlling structural metric(s):

- `EXP`: strength from `T`:
  - `+` if `T >= t_exp_plus`
  - `++` if `T >= t_exp_plus_plus`
- `COMP`: strength from `T` (more negative is stronger):
  - `+` if `T <= t_comp_plus`
  - `++` if `T <= t_comp_plus_plus`
- `CONT↑/↓`: strength from `A`:
  - `+` if `A >= a_cont_plus`
  - `++` if `A >= a_cont_plus_plus`
- `REVERT`: strength from `A` (more negative is stronger):
  - `+` if `A <= a_revert_plus`
  - `++` if `A <= a_revert_plus_plus`
- `EXH↑/↓`: strength from stretch magnitude:
  - `+` if `abs(S) >= s_exh_plus`
  - `++` if `abs(S) >= s_exh_plus_plus`

`P` modifier-only rules (when `P` is present; never required):

- define:
  - confirm if `P >= p_confirm_threshold`
  - diverge if `P <= -p_confirm_threshold`
- for `CONT` and `EXP`:
  - confirm bumps strength by one step (None -> `+`, `+` -> `++`),
  - diverge adds `!`.
- for `EXH`:
  - diverge bumps strength by one step,
  - confirm adds `!`.

Transition risk `!` (structural, independent of `P`):

- compute `T_delta = T - T_prev` when `T_prev` exists.
- if `T_prev` is missing (first eligible close), set `T_delta = None` and do not emit `!` from any delta-based rule.
- add `!` if either:
  - `token == EXP` and `V <= v_low_threshold` (expansion impulse emerging from compression), or
  - `token == COMP` and `V <= v_low_threshold` and `T_delta >= t_rise_threshold` (expansion emerging; “COMP -> EXP” risk).

Modifier merge / assembly (deterministic):

- build `token_strength` in two independent steps:
  1. determine `strength ∈ {None, "+", "++"}` from the selected token’s controlling metric(s),
  2. determine `risk: bool` (`True` iff any `!` predicate is true).
- then assemble:
  - if `token is None`: `token_strength = None`
  - else if `strength is None` and `risk is False`: `token_strength = None`
  - else if `strength is None` and `risk is True`: `token_strength = "!"`
  - else if `strength is not None` and `risk is False`: `token_strength = strength`
  - else: `token_strength = strength + "!"`
- never emit more than one `!`.

## 3.4 NEUT predicate (explicit)

Emit `NEUT` only when the row is genuinely “quiet neutral”:

- `abs(S) <= s_neut_max`
- `abs(A) <= a_neut_max`
- `abs(T) <= t_neut_max`
- `V` is not in a low-vol or high-vol extreme:
  - `v_neut_min <= V <= v_neut_max`

Otherwise, emit no token (`token=None`).

## 3.5 Deterministic evaluation order (state-machine)

Hysteresis, dwell, and precedence must be applied in a fixed order per close to avoid divergent implementations.

Per row update (at each row close):

1. **Read inputs (atomic per close)**:
   - current `DistRowMetrics` values (`V,S,A,T`, and `P` if present),
   - previous latched predicate states (for hysteresis),
   - previous token + bars-since-token-change (for dwell),
   - previous metrics snapshot (for 1-step deltas).
2. **Update hysteretic predicate latches** (from current metrics + prior latch):
   - for each predicate with enter/exit thresholds (`EXP`, `COMP`, `CONT`, `REVERT`, `extended`), update:
     - if latch is `False` and enter condition is met → latch becomes `True`,
     - if latch is `True` and exit condition is met → latch becomes `False`,
     - else latch unchanged.
3. **Select candidate base token** by precedence (first match wins) using the *latched* predicate states and current
   `S_dir`:
   - evaluate precedence rules exactly as in §3.2.
   - candidate may be `None`.
4. **Apply dwell gate**:
   - if `candidate == prev_token`: accept (no change).
   - else if `prev_token is None`: accept `candidate` (no dwell gating from “no token”).
   - else if `candidate in {EXP, EXH↑, EXH↓}`: accept immediately (override).
   - else if `bars_since_token_change < token_min_hold_bars(tf)`: block change and keep `prev_token`.
   - else accept `candidate`.
5. **Apply modifiers** (after the final base token is fixed):
   - compute strength and risk (`!`) from §3.3, using current metrics + prior metrics for deltas.
   - assemble `token_strength` per the merge rule in §3.3.

NEUT vs `None` canonical meaning (UI/consumer rule):

- `NEUT` means “explicit quiet-neutral state” (all neutral-band predicates true).
- `None` means “no token predicate matched” (leave the row unlabelled; ribbons are the nuance).

## 4) Output contract changes (Phase 2)

Populate the reserved Phase 1 fields:

- `DistRowSnapshot.token: str | None`
- `DistRowSnapshot.token_strength: str | None`

Do not populate:

- `DistRowSnapshot.narrative_hint` (still `None` in Phase 2).

## 5) TUI changes (Phase 2)

Add a token column to the dist-state panel.

Rendering contract:

- tokens must be visually subordinate (e.g., separated by spacing, dimmer attribute, or placed at row end).
- do not color the ribbon glyphs based on token.
- token modifiers:
  - show `++` and `!` exactly as defined by `FL-0071`.
  - if `+` is used (v1), render it exactly as `+`.
- when `token is None` (either warmup or “no token predicate matched”), render a fixed-width blank (spaces). Do not emit
  `NEUT` as a default.
- Unicode fallback: if terminal rendering does not support arrow glyphs:
  - render `CONT↑` as `CONT^`, `CONT↓` as `CONTv`
  - render `EXH↑` as `EXH^`, `EXH↓` as `EXHv`
  - internal `DistRowSnapshot.token` strings remain the canonical unicode vocabulary; fallback is renderer-only.

Layout fallback:

- if terminal width is constrained, degrade in this strict order:
  1. render base token only (omit `token_strength`, including `+`, `++`, and `!`),
  2. drop the token column entirely (revert to Phase 1 ribbons-only),
  3. then follow Phase 1 layout fallback rules.

## 6) Configuration (Phase 2)

Extend `runtime.dist_state` config with:

- `tokens_enabled: bool` (default `false` in Phase 2 until explicitly enabled)
- `tokens_fail_fast_unknown: bool` (default `true` in Phase 2; when false, map unknown tokens to `None` and log)

Add token threshold defaults (v1 starter; deterministic).

These thresholds apply to the continuous normalized metric values (`DistRowMetrics`), not to display bins.

Config invariants (must be validated at load time; fail-fast):

- threshold bounds:
  - `t_*`, `s_*`, `a_*`, `p_confirm_threshold` must be within `[0,1]` for magnitude thresholds and `[-1,1]` for signed
    thresholds (see definitions below); reject NaNs.
  - `v_*` thresholds must be within `[0,1]`; reject NaNs.
  - `token_min_hold_bars_* >= 0`.
- hysteresis pairs:
  - `t_exp_enter > t_exp_exit`
  - `t_comp_enter < t_comp_exit` (note sign)
  - `a_cont_enter > a_cont_exit`
  - `a_revert_enter < a_revert_exit` (note sign)
  - `s_ext_enter > s_ext_exit`
- strength cutoffs:
  - `t_exp_plus_plus >= t_exp_plus`
  - `t_comp_plus_plus <= t_comp_plus` (note sign)
  - `a_cont_plus_plus >= a_cont_plus`
  - `a_revert_plus_plus <= a_revert_plus` (note sign)
  - `s_exh_plus_plus >= s_exh_plus`
- neutral bands:
  - `0 <= v_neut_min <= v_neut_max <= 1`
  - `0 <= s_neut_max <= 1`, `0 <= a_neut_max <= 1`, `0 <= t_neut_max <= 1`
- deadbands within bounds:
  - `0 <= s_dir_deadband <= 1`

- `s_dir_deadband: float` (default `0.10`)
- `s_ext_enter: float` (default `0.60`)
- `s_ext_exit: float` (default `0.45`)
- `s_revert_min_stretch: float` (default `0.20`)
- `t_exp_enter: float` (default `0.40`)
- `t_exp_exit: float` (default `0.25`)
- `t_comp_enter: float` (default `-0.40`)
- `t_comp_exit: float` (default `-0.25`)
- `a_cont_enter: float` (default `0.35`)
- `a_cont_exit: float` (default `0.20`)
- `a_revert_enter: float` (default `-0.35`)
- `a_revert_exit: float` (default `-0.20`)
- `v_low_threshold: float` (default `0.25`)
- `t_rise_threshold: float` (default `0.05`)
- explicit NEUT bands:
  - `s_neut_max: float` (default `0.12`)
  - `a_neut_max: float` (default `0.12`)
  - `t_neut_max: float` (default `0.12`)
  - `v_neut_min: float` (default `0.30`)
  - `v_neut_max: float` (default `0.70`)
- strength cutoffs:
  - `t_exp_plus: float` (default `0.60`)
  - `t_exp_plus_plus: float` (default `0.80`)
  - `t_comp_plus: float` (default `-0.60`)
  - `t_comp_plus_plus: float` (default `-0.80`)
  - `a_cont_plus: float` (default `0.55`)
  - `a_cont_plus_plus: float` (default `0.75`)
  - `a_revert_plus: float` (default `-0.55`)
  - `a_revert_plus_plus: float` (default `-0.75`)
  - `s_exh_plus: float` (default `0.70`)
  - `s_exh_plus_plus: float` (default `0.85`)
- `p_confirm_threshold: float` (default `0.25`)
- per-timeframe dwell:
  - `token_min_hold_bars_3m: int` (default `2`)
  - `token_min_hold_bars_15m: int` (default `2`)
  - `token_min_hold_bars_1h: int` (default `1`)
  - `token_min_hold_bars_4h: int` (default `1`)

## 7) Acceptance criteria (Phase 2)

1. Lens outputs unchanged with tokens enabled/disabled.
2. Token values are always within the bounded vocabulary (no free-form strings).
3. Tokens update only on row bar closes and do not flicker at frame rate.
4. Tokens do not require `P` to be present; missing `P` still allows token generation.
5. TUI layout degrades gracefully: token column drops first under width constraint.
6. Vocabulary guard: unknown token strings must not reach the renderer (fail-fast in debug; otherwise map to `None` and
   log).
7. Stability logging (debug-only, not UI):
   - emit a single structured log event on token change with:
     - `tf`, `close_ms`, `prev_token`, `new_token`, `prev_strength`, `new_strength`
     - `predicate_hits` (deterministic schema; one of):
       - ordered list of canonical predicate names, or
       - fixed-key boolean map (preferred).
     - `dwell_blocked: bool`
     - `override_reason: str | None` (e.g. `exp_override`, `exh_override`)
     - `inputs` (`V,S,A,T,P_present,P` (if present), `T_delta`)
8. NEUT semantics:
   - `NEUT` is emitted only under the explicit neutral predicate; otherwise no token is emitted.
