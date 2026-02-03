# UI Evolution Planning (Idea Capture)

## Purpose

Capture high-level UI ideas that improve **at-a-glance semantic readability** for Flow Lens.
This is not a locked spec. It is a planning board for iterative UI evolution.

The target outcome is faster human interpretation of:

- who is in control,
- whether effort is being accepted/rejected,
- how strong and broad participation is,
- whether state is building, holding, or degrading.

## Guardrails

- Preserve existing channel semantics (X, Y, size, halo, lean).
- Add interpretation aids, not trading signals.
- Prefer low-clutter additions that improve glanceability.
- Any feature that introduces decision logic should be reviewed against decisions/invariants.

## Candidate Additions

### 1) Regime Strip (Primary Recommendation)

Add a compact semantic strip under (or above) the lens:

- `Control | Acceptance | Force | Dispersion | Persistence`
- Example values:
  - `SpotDom / Accepted / Strong / Broad / Building`
  - `PerpDom / Rejected / Weak / Narrow / Fading`

Why it helps:

- Converts numeric parsing into immediate semantic state.
- Aligns directly with lens purpose and visual channels.
- High value with minimal visual complexity.

What it needs:

- Discrete bins/labels for each state dimension.
- Stable label transitions (hysteresis already exists for several dimensions).

---

### 2) Direction Arrow Companions for Key State

Show small directional glyphs beside key values:

- `X`, `Y_s`, `S` each get `↑ / ↓ / →`.

Why it helps:

- Human eyes detect direction faster than reading signed numbers.
- Makes trend/build/fade state obvious in peripheral vision.

What it needs:

- Tiny slope/sign derivation from current vs prior value.
- Deadband around zero slope to avoid flicker.

---

### 3) Persistence Phase Label

Expose persistence-controller phase explicitly:

- `Build`, `Hold`, `Fade`, `Flip`

Why it helps:

- Makes persistence behavior interpretable without reverse-engineering `S` and `dS/s`.
- Clarifies whether structure is strengthening or decaying.

What it needs:

- Phase classification from persistence update regime.
- Optional subtle color/marker but can be plain text.

---

### 4) Inflection Context Badge (Non-Signal)

Add a contextual badge when state transition pressure is forming:

- Example: `Pressure shifting`

Why it helps:

- Highlights regime-change context without emitting trade calls.
- Supports discretionary users seeking early structure changes.

What it needs:

- Conservative context conditions (not directional “buy/sell” outputs).
- Suppression/hysteresis to avoid noisy flashing.

---

### 5) Multi-Symbol Quick Compare Row

Add a compact row for selected symbols (watchlist subset), showing semantic tuples:

- Example: `BTC + + ~`, `ETH - + +` (mapped to control/acceptance/persistence signs)

Why it helps:

- Lets user keep single-symbol lens focus while retaining market-relative context.
- Useful for quick rotation between symbols.

What it needs:

- Small fixed symbol subset (or configurable short list).
- Minimal representation to avoid dashboard sprawl.

---

### 6) Clean Mode Toggle

Add key toggle between:

- `Diagnostic Mode` (full status metrics),
- `Clean Mode` (lens + compact semantic strip + essential health).

Why it helps:

- Keeps tuning/debug capability while enabling distraction-light operational view.
- Prevents over-clutter from permanent diagnostics.

What it needs:

- Runtime UI mode state and keybind.
- Clear line-priority per mode.

---

## Problem Focus: Instantaneous Acceptance Direction Clarity

Observed ambiguity:

- Dot can move in a way that implies acceptance changed, but it is not always obvious whether instantaneous acceptance was tied to **upward** or **downward** displacement.
- Persistence line helps over time, but not always enough for short-horizon interpretation.
- Cross-checking with an external chart can introduce visual/time-sync uncertainty.

Base signal (no ambiguity):

- Direction and acceptance cues should derive from the same source used by effectiveness:
  - signed displacement after directional mapping and deadband handling (the same basis that drives `Y_raw`).
- Do not use separate “price-only” direction logic for acceptance semantics.

Top 3 retained solutions:

### 7) Acceptance/Rejecting Axis Color Flash (Primary)

Use the existing `ACCEPTING` / `REJECTING` axis labels as the cue:

- flash green for bullish displacement context,
- flash red for bearish displacement context.

Why it helps:

- Zero extra words; direction + acceptance context is immediate.
- Preserves a clean lens while still resolving “up vs down” ambiguity.

What it needs:

- Color derivation from the same effectiveness basis (signed displacement after directional mapping/deadband).
- Short flash window + cooldown so it reads as a cue, not constant blinking.
- Optional neutral/no-flash state when directional signal is near zero.

---

### 8) Acceptance Direction Ribbon (Short History)

Add a short (10–20 update) ribbon of signed acceptance states:

- Up-accept / down-accept / neutral buckets.

Why it helps:

- Shows whether acceptance is building persistently or oscillating.
- Provides micro-history without adding a chart.

What it needs:

- Compact glyph vocabulary and rolling buffer.
- Light smoothing/hysteresis for readability.

---

### 9) Price-Series Provenance Badge

Show active reference source and switch events:

- `P:spot` / `P:perp` (+ optional recent-switch marker).

Why it helps:

- Explains potential visual mismatch versus external chart feeds.
- Improves trust in instantaneous interpretation.

What it needs:

- Existing `price_series_used` in compact view.
- Optional switch cooldown marker.

## Suggested Rollout Order

1. Regime Strip  
2. Persistence Phase Label  
3. Direction Arrows  
4. Clean Mode Toggle  
5. Inflection Context Badge  
6. Multi-Symbol Quick Compare Row
7. Acceptance/Rejecting Axis Color Flash
8. Acceptance Direction Ribbon
9. Price-Series Provenance Badge

Rationale:

- Early steps are low risk, high interpretability, and preserve current workflow.
- Later items add context breadth after core glance semantics are stable.
