# Persistence (Phase 1) — Planning Notes (2026-02-02)

Status: working notes (not a decision record)

This document captures analyst reasoning and options for Phase 1 persistence work so we can reference it later without re-litigating context.

If any of the items below become “locked behavior” (especially anything that redefines semantics or invariants), capture it as a decision record in `docs/decisions/FL-XXXX`.

---

## 1) Intent recap (what Phase 1 is for)

Phase 1 adds an **orthogonal temporal context layer** to the lens:

- The dot remains the **instantaneous window truth** (current window boundary).
- The persistence line should answer: “is acceptance/rejection *accumulating across windows*, or is it mostly noise?”
- The goal is *truthful translation* into a glanceable cue: rise, fade, rollover, and “nothing is persisting”.

This is not a signal/score/alert system. It should not infer trades; it should depict structural state over time.

---

## 2) Goals vs. issues (what “failed” and why)

The current Phase 1 implementation can be “working” yet still fail expectations because the viewer’s mental model is:

> If acceptance spikes and remains positive, the persistence line should show meaningful buildup and not look faint or disappear.

Observed issues are largely **definition + dynamics**, not “needs more tuning”.

### Issue A — low steady-state ceiling from the current update law

Current form (leaky integrator with separate build/decay):

`S_t = clamp(S_{t-1}*(1 - decay(dt)) + build(dt)*A_t, -1, 1)`

For constant `A_t`, the equilibrium is approximately:

`S* ≈ build/decay`

If `tau_build > tau_decay` (slow build, faster fade), then `build < decay` and `S*` is forced low even when `A_t` is strongly sustained.

Result: the line can be “truthfully integrating” but still look weak relative to sustained dot effectiveness, which reads as “not persisting” to the eye.

### Issue B — input mismatch vs what the dot shows

- The dot Y is derived from a gated/deadbanded pipeline (e.g., `Y_gated → smooth → Y`).
- Persistence currently integrates `Y_raw`.

This makes the line react to impulses the dot may intentionally suppress (deadband / effort gate), and it reduces perceptual coherence even when both are “internally correct”.

**owner notes:** the issue at the UX level for a human end user is that persistence should have greater amplitude than Y itself. If instantaneous Y keeps spiking up persistent Y should show that there is a cumulative effect and the persistence over time is greater than the instantaneous or it can't make sense with the current UI design. Human brain reads line below dot as diminishing, persistence lower than Y, must be going down. Every pulse of Y should push persistence line up if force keeps building. Basically capturing and displaying momentum, which was how I originally wanted to model persistence but within the vaccuum of this system force is not the same as it is in physics 101. p=mv is hard to apply here.

### Issue C — center suppression hides the most important case

If the renderer suppresses the line when it maps to center, neutral persistence is visually indistinguishable from “not computed” / “missing”.

Neutral persistence is meaningful (it says: “no regime is persisting”), so it must remain visible.

**owner notes:** I never said I had an issue with the line disapearing at 0, in fact I prefer it. The lack of a line gives the same intuition as the line at 0. They both mean there is nothing persisting and I am aware and the UI is cleaner this way removing focus from a line that is effectively noise in that range anyway. 

---

## 3) Principles for choosing the persistence approach

### 3.1 “Truth” is the outcome, not the equation

The method is acceptable if it produces a stable, truthful depiction of:

- accumulation,
- fade,
- rollover,
- neutrality.

**owner notes:** This kind of spills over into phase 2 but yes, and must be clear in the ui at a glance with no confusion or second guessing.  

Implementation names (“attack/release” vs “leaky integrator”) are secondary.

### 3.2 Align persistence to the same semantic layer as the dot (unless explicitly decided otherwise)

If the dot uses an effort gate and deadband to suppress artifacts, then persistence should usually integrate the **same semantic variable** unless we intentionally decide to show a different notion of “acceptance”.

**owner notes:** agreed. this goes along with the confusion surrounding the current implementation at the human user UI level. If one represents a thing and the other represents persistence of that thing, they ought, no, have to integrate the same semantic variable, unless we find a better way like the rate of change of that variable or something clever like that.

Candidate inputs:

- `A_t = Y_raw` (more responsive; can integrate artifacts the dot is trying to ignore)
- `A_t = Y_gated` (aligns to “effort-credible acceptance”; likely the best first experiment)
- `A_t = Y` (very stable; may lag and obscure early buildup)

If we change the chosen input, document it explicitly as part of the behavior contract.

### 3.3 A persistence line should be able to approach the magnitude of sustained acceptance

If the input stays high and consistent long enough, the line should be able to become meaningfully high as well.

If the update law structurally caps this (low fixed point), it will continue to read as “faint” regardless of tuning.

**owner notes:** this is the issue that needs solved for first and then determine how to get here best by working backwards from this. If the lens can't show persistence in a clear manner without adding confusion, it's a failure as a feature. The line should naturally be able to show anywhere Y can show even above it. If Y dot pulses up repeatedtly and S crosses Y then that should signal a significant amout of agreement. and more importantly, if S > Y and the line is below Y then the lens is lying. Not just confusing that is a straight up lie and cannot be tolerated.

### 3.4 Neutral persistence must remain visible

Neutrality is information. Do not hide it.

**owner notes:** see note above

---

## 4) Suggested next experiments (lowest risk first)

These are experimentation directions, not decisions.

1) **Use `A_t = Y_gated` as persistence input**
   - Aligns persistence with “effort-credible acceptance/rejection” rather than raw impulse.
   - Improves perceptual coherence with dot behavior without changing dot semantics.

2) **Replace the persistence update law with an asymmetric approach-to-target form**
   - Keeps asymmetry (build vs fade) while allowing the line to approach sustained acceptance magnitudes on the same scale.
   - This is a dynamics/translation fix, not necessarily a semantic change, but it should be treated as “important behavior” and captured in the contract before locking.

   **owner notes:** for experimintation sake we should remove asymmetry entirely. Persistence comes on slow and degrades rapidly naturally so let's see if persistence even needs extra help, I'm pretty sure opposite forces are enough to erase it quickly. 

3) **Stop suppressing center-line draw**
   - Always render the line, with subtle styling at/near center.

   **owner notes:** we are already good here unless you can convince me otherwise, we will stick with a clean UI and implicit meaning of the line at Y = 0.

Do not proceed to Phase 2 (opposition gauge) until Phase 1 has a stable behavioral contract and passes replay-based sanity.

---

## 5) Phase 2 (opposition gauge): when it is appropriate

Phase 2 should be conditional:

- Only consider it after Phase 1 is both truthful and glanceable.
- Phase 2 is for early visibility of counter-pressure *before* the persistence line rolls over.
- Adding Phase 2 while Phase 1 is definition-confused increases cognitive load and makes regressions harder to diagnose.

---

## 6) Validation approach (what to use, not how to judge)

Use the existing Tier 1 gate (BTC + SOL top1) plus targeted drilldowns (per-file diagnostics) when persistence is in scope:

- Trend legs first (truth + buildup)
- Chop (neutrality + non-whip)
- Impulse (no false “persistence” from spikes)

For procedural steps, see `docs/reference/stability-checklist.md`.

