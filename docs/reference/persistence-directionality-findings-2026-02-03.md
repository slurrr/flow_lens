# Persistence Directionality Findings (Informal)

Date: 2026-02-03  
Status: informal alignment memo for quant/planner/dev review

## Context

Current line semantics:

- `Y` = effectiveness (accepted vs rejected), not bullish vs bearish.
- `S` = persisted effectiveness.

This is semantically consistent with the original lens design, but it creates a practical interpretation issue in live flow:

- bullish accepted flow and bearish accepted flow can both push `S` upward,
- so `S` can remain "more acceptance" while directional regime has already flipped.

## Key Finding

Your concern is correct: this is not just a decay problem.  
It is primarily a **state decomposition problem**.

Why:

- Dormancy decay addresses stale memory in quiet regimes.
- It does not solve directional stacking when accepted flow changes side.

## Semantic Check

Your framing is system-consistent:

- Keep line position as persisted effectiveness (accept/reject memory).
- Add an orthogonal layer for bull/bear provenance so users know who is getting paid.

This preserves channel intent and improves interpretability without turning the lens into a signal engine.

## Recommended Model (High-Level)

### 1) Keep `S_eff` as persisted effectiveness

- Do not repurpose the line position into pure direction.
- `S_eff` continues to answer: "is effectiveness persisting?"

### 2) Add directional accepted-flow state (`S_dir`) for provenance

Use a directional input built from accepted flow only:

- `A_dir = sign(E_dir) * max(Y_gated, 0)`

Interpretation:

- bullish accepted flow drives `A_dir` positive,
- bearish accepted flow drives `A_dir` negative,
- rejected flow does not claim provenance.

Then persist `A_dir` (with opposition + dormancy logic) into `S_dir`.

### 3) Use `S_dir` for color/provenance, not line position

- line position = `S_eff` (unchanged meaning),
- line color = bull/bear provenance from `S_dir` with hysteresis/deadband.

This gives:

- "how much persistence" and
- "who built it"

at the same glance.

### 4) Add conditional dormancy decay (fallback already captured)

Apply only when near-zero effective input persists for a grace period.

- active regime: opposition-driven behavior dominates,
- dormant regime: controlled relax-to-neutral prevents ghost persistence.

### 5) Add pivot neutralization for active directional handoff

Dormancy decay is not sufficient for fast pivots.  
When accepted flow side flips while activity is still meaningful, persistence needs a fast handoff behavior.

Suggested behavior:

- detect accepted-direction flip (bull-accepted -> bear-accepted or reverse),
- enter a short neutralization phase that rapidly pulls `S_eff` toward zero,
- require sustained new-side accepted flow before rebuilding persistence away from neutral.

This is not a full hard reset:

- preserve transition structure,
- avoid single-tick false resets,
- still respond quickly when regime genuinely pivots.

## Why Hard Reset Is Risky

A conditional hard reset is tempting but usually too destructive:

- hides transition structure,
- reduces explanatory value around regime handoff,
- can create artificial cliffs in UI behavior.

If needed, prefer a **fast-neutralization rule** over instant zero:

- strong opposite accepted flow for N updates -> accelerate unwind toward neutral.

## Practical Implication

Without this split, persistence can remain mathematically "correct" but visually misleading for discretionary use.  
With this split, persistence remains true to lens semantics and becomes operationally readable.

Operationally:

- dormancy controller handles stale/no-trade lingering,
- pivot neutralization handles active directional regime handoff.

## Open Decisions To Lock Before Implementation

1. Exact `A_dir` definition:
   - accepted-only (`max(Y_gated,0)`) vs signed full effectiveness (`sign(E_dir)*Y_gated`).
2. Dormancy entry/exit thresholds and grace time.
3. Provenance color flip hysteresis and neutral band.
4. Fast-neutralization trigger and rate for directional pivots.
5. Sustained-confirmation rule before post-pivot rebuild.

## Suggested Next Step

Create a small Phase-1b behavior contract for:

- `S_eff` (position semantics),
- `S_dir` (provenance semantics),
- dormancy controller,
- replay fail metrics (chop drift, stale hold, flip latency, false persistence).

---

## Quant Notes

- Line color semantic conflict (must resolve): docs/decisions/FL-0054-persistence-line-control-color.md says persistence line color = current bull/bear pressure
  (E_dir/E_total). Your findings doc proposes color from S_dir (provenance of accepted flow). Those are different meanings (“who is pushing now” vs “who built the acceptance”). We need to pick one, or we’re overloading color.
  **owner notes:** upating decision for line color will resolve.
- Axis flash semantic conflict (must resolve): docs/decisions/FL-0053-accept-reject-axis-flash.md already uses E_dir/E_total for bullish/bearish cue. If persistence line color also stays E_dir/E_total, we may be duplicating the same cue in two places; if we switch line color to S_dir, we preserve orthogonality between “now” (flash) and “built” (line).
  **owner notes:** this is exactly right and label flash will capture now, line will capture built by
- What exactly is “direction” for pivots? Decide the flip trigger source:
  - sign(E_dir) (pressure basis), or
  - sign(disp_rate) (price basis), or
  - sign(A_dir) itself.
    Mixing these will create “math is right, looks wrong” moments.
    **owner notes:** this is valid and may require a tunable config that allows us to crank up or down the sensitivity or make it another scale factor. I am up for trying things and open to suggestions. Make sure we implement in a way we can fail fast here and iterate if need be. This is the biggest factor that makes effectiveness persistence an issue. Must proceed thoughtfully here.
- Define “accepted-direction flip” precisely: with A_dir = sign(E_dir)\*max(Y_gated,0), what constitutes a flip when max(Y_gated,0) is near zero? We need a minimum activity threshold so chop doesn’t spam flip handling.
- Neutralization behavior shape (must lock):
  - Is it a rate boost toward 0 for S_eff only, or does it also affect S_dir?
  - Does it require N consecutive opposite-accepted ticks, or a time window?
  - Do we cap the neutralization speed (avoid “cliffs”)?
- Dormancy decay (support-required) definition: thresholds + grace time:
  - enter dormancy when |Y_gated| < quiet_abs for T_quiet_s,
  - decay target is 0, rate lambda_dormant,
  - exit dormancy when |Y_gated| >= active_abs (hysteresis).
- Do we keep one line or move to two-state internally but one rendered line?
  - If we keep position = S_eff, and color = something, we can still compute both S_eff and S_dir, but we must lock whether S_dir is purely visual (color) or also
    feeds neutralization logic.
- Asymmetry (“price falls under its own weight”) scope: if we introduce asymmetry, it must be attributable to support absence / dormancy, not “bull vs bear bias”, otherwise we’re baking in interpretation. Lock whether decay is symmetric or conditional-only.
- dt/cadence safety stays non-optional: make sure both S_eff and S_dir use timebase-safe updates and log persist_dt_s + applied coefficients (we already added the fields; just ensure the contract references them).
- Pass/fail metrics for Experiment B: we should add two new ones on top of the existing list:
  - “directional stacking prevention”: after a sustained accepted-direction flip, S_eff must stall/neutralize within X seconds even if acceptance stays positive
    (but opposite provenance),
  - “provenance coherence”: S_dir must flip within Y seconds under sustained opposite accepted flow, without flipping during quiet.

## Dev Proposal

Goal: keep line position semantics (`S_eff`) intact, add directional provenance (`S_dir`), and prevent directional stacking without hard resets.

### 1) Lock semantics before implementation

1. Axis flash = **now** (current pressure): bull/bear from `E_dir / E_total`.
2. Line position = **persisted effectiveness** (`S_eff`).
3. Line color = **who built persistence** (`S_dir`), not current pressure.
4. No dual meaning in one channel.

### 2) Experiment B state model (starting point)

Compute:

- `A_eff = Y_gated` (existing effectiveness input)
- `A_dir = sign(E_dir) * max(A_eff, 0)` (accepted-direction provenance input)

Persist two internal states:

- `S_eff`: persisted effectiveness (line position)
- `S_dir`: persisted provenance (line color)

Both dt-safe and clamped to `[-1, 1]`.

### 3) Controllers

#### 3.1 Active opposition controller (default behavior)

- In active flow, both states update opposition-first (no blanket time decay).

#### 3.2 Pivot neutralization controller (new)

Trigger when accepted-direction meaningfully flips (from `A_dir`, not raw `E_dir` or price alone):

- require minimum accepted activity (`|A_dir| >= active_abs`)
- require persistence (`N` consecutive ticks or `T` seconds)

On trigger:

- accelerate `S_eff` toward 0 (rate boost; no hard reset),
- allow rebuild only after neutral-zone crossing + sustained new-side confirmation.

`S_dir` should also move toward new provenance during this phase, but with hysteresis to avoid single-tick flip noise.

#### 3.3 Dormancy decay controller (fallback, scoped)

Enter dormancy only after prolonged quiet:

- `|A_eff| < quiet_abs` for `T_quiet_s`

In dormancy:

- apply slow relax-to-neutral decay to `S_eff` and `S_dir`.

Exit dormancy with hysteresis:

- `|A_eff| >= active_abs`.

### 4) Minimal knob set (for fast iteration)

Start small to avoid overfitting:

- `pivot_active_abs`
- `pivot_confirm_ticks` (or `pivot_confirm_s`)
- `pivot_neutralize_rate`
- `dormant_quiet_abs`
- `dormant_quiet_s`
- `dormant_decay_rate`
- `provenance_flip_hysteresis`

Keep defaults conservative and tuned for replay falsification, not visual dramatics.

### 5) Diagnostics (must-have for fail-fast)

Add/ensure per-tick logging:

- `A_eff`, `A_dir`
- `S_eff`, `S_dir`
- `persist_dt_s`
- applied coefficients/rates
- controller mode flags: `active`, `pivot_neutralize`, `dormant`
- pivot trigger + confirmation counters

### 6) Pass/fail criteria to lock for Experiment B

Use existing metrics + quant additions:

- directional stacking prevention window (`S_eff` stalls/neutralizes within Xs after sustained flip),
- provenance coherence window (`S_dir` flips within Ys under sustained opposite accepted flow),
- no quiet flip spam for `S_dir`,
- chop drift bounds preserved,
- no cliff behavior (neutralization speed cap respected).

---

## Quant Notes (2)

Poking holes / edge cases in the Dev Proposal (overall it’s solid):

### 1) Confirm the “direction basis” is truly one basis

The proposal uses `sign(E_dir)` to define `A_dir` and also uses `E_dir / E_total` for the axis flash (now-pressure).
That’s good *if and only if* `E_dir` is already the single canonical signed pressure basis everywhere.

Lock explicitly:

- `E_dir` sign is the *only* bull/bear sign source for persistence provenance (no price-only sign fallbacks).
- `E_dir` deadband definition for “neutral” is identical for axis flash and for `A_dir` sign (otherwise color/flash can disagree in confusing ways).

### 2) `A_dir` definition: accepted-only is right, but specify the gate

`A_dir = sign(E_dir) * max(A_eff, 0)` is clean. Two details to lock:

- `A_eff` should be *exactly* the same value used to compute the dot Y semantics (`Y_gated` vs smoothed `Y`), not a “nearby” variant.
- If `A_eff` is smoothed for the dot, decide whether `A_dir` uses the smoothed or unsmoothed value. (My bias: unsmoothed `Y_gated` for responsiveness, but with a pivot confirm window to prevent twitch.)

### 3) Pivot trigger vs pivot effect: avoid unintended cross-coupling

The proposal triggers pivots from `A_dir` (good), but the effect is “accelerate `S_eff` toward 0”.
This is the key behavior decision: we’re *forcing* the effectiveness memory to neutral during an accepted-direction handoff.

This is reasonable, but we should lock the intended meaning:

- During a pivot, we choose interpretability over strict “acceptance magnitude accumulation”.
- The neutralization is a *handoff visualization rule*, not an implied market mechanism.

### 4) What happens when acceptance stays positive but provenance flips rapidly?

The hardest live regime is “accepted chop”: `A_eff > 0` much of the time, while `sign(E_dir)` alternates.

Risk:

- frequent pivot neutralization could pin `S_eff` near 0 (the line becomes uninformative),
- or worse, oscillate hard if neutralize/rebuild cycles are too fast.

So we need to lock:

- minimum pivot activity (`pivot_active_abs`) and confirm window (`pivot_confirm_ticks/s`) tuned specifically to prevent pinning in accepted chop,
- a cooldown / refractory period after a pivot triggers (otherwise sustained chop becomes “continuous pivot mode”).

### 5) Dormancy controller must not fight the pivot controller

Both pivot-neutralize and dormancy-decay push toward neutral.
Lock mode precedence:

- Pivot mode always wins over dormancy if activity is present.
- Dormancy only engages when acceptance is *quiet*; pivot should not be possible in dormancy by definition.

### 6) “No cliffs” needs a measurable cap

“No cliff behavior” is currently qualitative. Turn it into a check:

- Maximum allowed `|ΔS_eff|` per tick (or per second) during neutralization.
- Same for `S_dir` color flips (hysteresis band + maximum rate of color state change).

This is critical to keep “perceptual stability over precision”.

### 7) Two-channel cues: confirm we’re not duplicating or contradicting

Given FL-0053 and the proposal:

- Axis flash = now-pressure (`E_dir/E_total`)
- Line color = provenance (`S_dir`)
- Line position = persistence (`S_eff`)

This is great, but we need to lock the neutral behavior:

- What color is shown when `S_dir` is near zero? (Default/neutral.)
- What happens if axis flash shows strong bear now, but `S_dir` is still bull? (This is a feature, but only if we document it explicitly as “handoff in progress / inventory still bull-built”.)

### 8) Minimum viable knobs: confirm we can tune without overfitting

The suggested knob set is reasonable. Two additions I think are non-optional for stability:

- `pivot_cooldown_s` (or ticks) to avoid thrashing in chop regimes.
- `pivot_neutral_zone_abs` (how close to 0 counts as “neutral crossed”) to prevent rebuild jitter when hovering near 0.

Everything else can start conservative.

### 9) Diagnostics additions to ensure falsifiability

We already log persistence internals, but Experiment B needs explicit fields to avoid “can’t tell what happened”:

- `A_eff`, `A_dir` values per tick,
- `S_eff`, `S_dir` values per tick (even if one is not rendered),
- controller mode: `persist_mode = active|pivot|dormant`,
- pivot counters: `pivot_confirm_count`, `pivot_cooldown_remaining`,
- neutralization coefficients actually applied each tick (effective rate after dt scaling).

### 10) Decisions needed before turning this into a plan

Lock these before we update the Phase 1 planning doc:

1. Confirm FL-0054 is superseded: line color becomes provenance (`S_dir`), axis flash remains now-pressure (`E_dir/E_total`).
2. Define exact deadbands: `neutral_dir_abs` for `E_dir/E_total` and `active_abs/quiet_abs` for `A_eff/A_dir`.
3. Choose confirm mechanism: ticks vs seconds (ticks is simpler with fixed cadence; seconds is safer under dt variability).
4. Set explicit pass/fail numbers for the two new criteria:
   - pivot neutralize time-to-neutral (p50/p90) under sustained flip,
   - provenance flip latency under sustained opposite accepted flow,
   plus “pivot spam rate” upper bound in chop.

### 7) Implementation sequencing

1. Implement internal `S_dir` + line-color mapping only (no pivot/dormancy change) to validate provenance readability.
2. Add pivot neutralization controller for `S_eff` (and coordinated `S_dir` handoff).
3. Add dormancy controller.
4. Replay-gate each step independently before combining.

This gives clean attribution when something breaks and keeps iteration fast.

---

## Dev Proposal (2)

This is an updated, “ready-to-plan” proposal that incorporates the Quant Notes and keeps channel semantics non-overlapping.

**Note:** This is a semantic shift away from Experiment A’s “opposition-only / no-decay” framing. Experiment B is explicitly *support-required* (M2-like), with a pivot handoff controller to prevent directional stacking.

### 1) Locked semantic mapping (channels)

- Axis flash (FL-0053) = **now-pressure** cue from `E_dir / E_total` (with one canonical deadband).
- Persistence line position = `S_eff` = **persisted effectiveness** (accept/reject persistence).
- Persistence line color = `S_dir` = **provenance of accepted effectiveness** (“who built the persistence”), not current pressure.

This implies FL-0054 must be superseded (new decision) because it currently defines line color as now-pressure.

### 2) Canonical inputs

Use one “effectiveness” input and derive everything else from it:

- `A_eff = Y_gated` (same semantic variable as the dot’s effectiveness layer; no alternate basis)
- `A_dir = sign(E_dir) * max(A_eff, 0)` (accepted-only provenance; rejected does not claim direction)

Lock one canonical sign source (direction comes from `E_dir / E_total`), but allow separate deadbands if needed:

- `neutral_dir_abs_flash` (axis flash)
- `neutral_dir_abs_persist` (provenance + pivot logic)

Default them equal; allow divergence only if it improves perceptual stability.

### 3) Base update law (dt-safe, bounded)

Both states are updated with a dt-safe approach-to-target (exponential smoothing), bounded in `[-1, 1]`:

- `alpha(dt, tau) = 1 - exp(-dt / tau)`
- `S <- S + alpha * (A - S)`

This gives “support-required” fade automatically when `A_eff` drops toward 0, without needing blanket decay in active markets.

### 4) Controllers (mode machine)

Modes are exclusive with explicit precedence:

1) `pivot` (highest) → 2) `active` → 3) `dormant` (lowest; only when quiet)

#### 4.1 Active mode (default)

- `S_eff` tracks `A_eff` with `tau_eff_active`
- `S_dir` tracks `A_dir` with `tau_dir_active`
- No additional decay term.

#### 4.2 Pivot mode (handoff neutralization)

Trigger conditions (all required):

- Accepted activity: `|A_dir| >= pivot_active_abs`
- Accepted-direction flip: `sign(A_dir)` differs from a stored “last_confirmed_dir_sign”
- Confirmation: flip holds for `pivot_confirm_s` (seconds, not ticks; dt-safe)
- Cooldown: only if `pivot_cooldown_remaining_s == 0`

Behavior on trigger:

- Enter `pivot` mode for up to `pivot_max_s` (failsafe).
- Drive `S_eff -> 0` quickly but capped (no cliffs): use `tau_eff_pivot` and a hard cap on `max_delta_s_eff_per_second` (derive per-tick cap from dt).
- Drive `S_dir` toward the new `A_dir` (use `tau_dir_pivot`) but apply hysteresis/neutral band so color doesn’t flip on single-tick noise.
- Exit pivot only after:
  - `|S_eff| <= pivot_neutral_zone_abs` (neutral crossed), and
  - sustained new-side accepted confirmation is met again (`rebuild_confirm_s`).
- Start cooldown (`pivot_cooldown_s` in seconds).

Rationale: this prevents “accepted-direction stacking” from reading as continued regime persistence during handoff, without hard resets.

#### 4.3 Dormant mode (quiet-time relax)

Entry:

- `|A_eff| < dormant_quiet_abs` for `dormant_quiet_s`
- **and** low activity (so “active-but-balanced” does not get misclassified as dormant).

Activity should be derived from existing engine observables (examples):

- low `E_rate` (effort rate) below a threshold, and/or
- low event activity (spot+perp event counts in window) below a threshold.

Behavior:

- Relax both `S_eff` and `S_dir` toward 0 with `tau_dormant` (slow, stability-first).

Exit (hysteresis):

- `|A_eff| >= dormant_active_abs` (and/or activity rises above the dormancy threshold)

Hard rule: pivot cannot trigger in dormant (no accepted activity by definition).

### 5) Minimal knobs (start conservative)

Non-optional (stability/anti-thrash):

- `pivot_active_abs`
- `pivot_confirm_ticks` (or `pivot_confirm_s`)
- `pivot_cooldown_s`
- `pivot_neutral_zone_abs`
- `tau_eff_active`, `tau_eff_pivot`
- `tau_dir_active`, `tau_dir_pivot`
- `dormant_quiet_abs`, `dormant_quiet_s`, `dormant_active_abs`, `tau_dormant`
- `max_delta_s_eff_per_tick` (or per second) + equivalent cap/hysteresis for color flips

Optional later (only if needed):

- asymmetric dormant taus (support-loss “gravity”) *only* inside dormant mode.

### 6) Diagnostics (fail-fast)

Per tick, log:

- `A_eff`, `A_dir`
- `S_eff`, `S_dir`
- `persist_dt_s` and effective alphas/taus used (so dt artifacts are visible)
- `persist_mode` (`active|pivot|dormant`)
- pivot counters (`pivot_confirm_count`, `pivot_cooldown_remaining`, `last_confirmed_dir_sign`)

### 7) Pass/fail targets (lock numbers before coding)

Add explicit targets to the existing persistence criteria:

- Pivot neutralize time: under sustained accepted-direction flip, `S_eff` reaches `|S_eff| <= pivot_neutral_zone_abs` within `X` seconds (p50/p90).
- Provenance flip latency: under sustained opposite accepted flow, `S_dir` crosses 0 within `Y` seconds (p50/p90).
- Pivot spam bound: in chop, pivot triggers per minute must remain below `Z` (and cooldown must prove effective).
- No cliffs: enforce `max |ΔS_eff|` and `max color flips/min` bounds.

### 8) Implementation sequence (same intent, tighter gating)

1) Implement + log `S_dir` and switch line color to provenance (`S_dir`), keeping `S_eff` dynamics unchanged.
2) Add pivot controller + caps + cooldown (verify with replay; ensure no “accepted chop pinning”).
3) Add dormancy controller (verify stale-hold cleanup without harming active regimes).
4) Only after the above is stable: consider any asymmetry (“gravity”) strictly in dormancy.
