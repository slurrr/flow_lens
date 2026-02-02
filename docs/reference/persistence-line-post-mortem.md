# Persistence Line Post-Mortem

## Context

This document captures what we set out to do, what was discussed, what was implemented, why the result did not match intent, and what should be done differently.

Date context: this work happened in late January / early February 2026 during ongoing live + replay tuning.

---

## 1) What we set out to do

Primary objective:

- Keep the existing lens dot semantics intact (instantaneous window truth), and
- Add an additional visual that shows whether acceptance/rejection is **persisting over time**.

Desired behavior (as stated repeatedly):

- Dot moves with instantaneous conditions.
- New persistence layer tracks regime buildup.
- During pullback/noise, persistence should **hold** rather than collapse immediately.
- During sustained counter-pressure, persistence should fade/reverse.
- If needed later, add a gauge to show opposition building before the persistence line fully rolls over.

---

## 2) What we discussed and landed on

### Core alignment

- Dot `Y` remains instantaneous effectiveness.
- New persistence state should be orthogonal and temporal (not a trade signal).
- Use a phased approach:
  - Phase 1: persistence line.
  - Phase 2: optional opposition gauge if needed.

### Artifacts created

- Spec: `persistence-line-opposition-gauge-phased-spec.md`
- Decision: `docs/decisions/FL-0050-persisted-effectiveness-line-and-opposition-gauge.md`

---

## 3) What was implemented

Implemented pieces (confirmed in code):

- Engine persistence state added (`persist_raw`, `persist_slope`, `persist_sign`).
- New config knobs added:
  - `persist_enabled`
  - `persist_tau_build_s`
  - `persist_tau_decay_s`
- TUI draws a horizontal persistence line.
- Diagnostics log/report include persistence fields.

Relevant code paths:

- `src/flow_lens/engine/state_engine.py`
- `src/flow_lens/tui/renderer.py`
- `scripts/diagnostics_report.py`
- `config/app.toml`

---

## 4) How it failed in practice

Observed UX failure:

- Dot moves as expected.
- Persistence line often sits below the dot, is weak/faint, and disappears near zero.
- It does not provide meaningful “held state” value as intended.

This is consistent with the current implementation.

---

## 5) Why it failed (root causes)

### Root cause A — update equation produces low fixed-point persistence

Current update:

`S_t = clamp(S_{t-1} * (1 - decay) + build * A_t, -1, 1)`

With current defaults (`tau_build=90s`, `tau_decay=20s`, `dt~2s`):

- `build ≈ 0.022`
- `decay ≈ 0.095`
- steady-state for constant `A_t=1` is about `build/decay ≈ 0.23`

Implication:

- Even under sustained strong positive acceptance, persistence converges low (~0.23), so it will generally sit well below a high dot.

### Root cause B — center-line suppression hides the line

Renderer currently skips draw when persistence maps to center Y:

- `if y == center_y: return`

Implication:

- Around neutral regimes the line vanishes, matching the reported “line goes away.”

### Root cause C — behavior contract was under-specified at implementation time

The intended motion profile (“track then hold”) was not encoded as explicit acceptance criteria before coding. The implemented model was a generic leaky integrator, not the requested envelope behavior.

---

## 6) What would have worked better

Use an explicit **attack/release persistence model** (track fast, release slow):

- `target = Y_raw` (or smoothed `Y` if preferred)
- If reinforcing: larger gain (`attack`)
- If weakening/opposing: smaller gain (`release`)

Example behavior logic:

- Same sign and `|target| >= |S|` → fast track.
- Otherwise → slow release/rollback.

This directly matches the requested visual mental model:

- Dot up → line follows.
- Dot wiggles down → line holds.
- Sustained counter-flow → line rolls/reverses.

Also required:

- Never hide line at center; render with low-emphasis style instead.

---

## 7) Why it was not done this way initially

- Over-weighted “minimal, conservative, invariant-safe” implementation.
- Chose a mathematically simple leaky integrator without validating the dynamic shape against desired UX trajectory.
- No pre-implementation synthetic behavior tests (step, pullback, reversal) were used as a gate.

---

## 8) What I would do differently next time

1. Lock behavior with explicit testable acceptance criteria before coding:
   - sustained +Y drives persistence near +Y (not capped near 0.2),
   - short pullback does not erase persistence,
   - sustained opposite flow causes controlled reversal.
2. Choose equation from desired motion profile first (attack/release), then tune constants.
3. Add synthetic replay tests dedicated to persistence dynamics before live validation.
4. Avoid center-line suppression in renderer.
5. Stage delivery:
   - engine metric + log only,
   - then render,
   - then optional opposition gauge.

---

## 9) Current status and recommendation

Status:

- Phase 1 artifacts are in place, but current persistence behavior does not match intended use.

Recommendation:

- Keep FL-0050.
- Replace current persistence update with attack/release logic.
- Remove center hide condition.
- Re-run replay validation before any Phase 2 gauge work.

