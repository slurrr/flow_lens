# Persistence Line — Behavior Contract (Skeleton)

Status: draft skeleton (open-ended; not official)

Purpose: define a **testable behavior contract** for Phase 1 persistence that focuses on outcomes (“truth translated into a glanceable cue”), not the implementation technique.

Once finalized and agreed, capture the locked behavior as a decision record (likely an amendment to FL-0050 or a new FL-XXXX), especially if it changes any invariants.

---

## 0) Scope

This contract applies only to:

- Phase 1 persistence line (`S_t`) behavior and rendering

It does not define:

- Phase 2 opposition gauge semantics
- any change to dot semantics or state-space definitions

---

## 1) Non-negotiables (must remain true)

- Dot semantics remain unchanged:
  - X = control (spot vs perp dominance)
  - Y = instantaneous effectiveness (current window truth)
  - size = force magnitude (dominance magnitude)
  - halo = dispersion
  - lean = transitional only
- Persistence is **orthogonal**: it must not encode extra meaning into existing channels.
- Persistence is not a signal/score/alert.

---

## 2) Definitions (to be finalized)

### 2.1 Persistence state

- `S_t ∈ [-1, 1]`: persisted acceptance/rejection state rendered as a horizontal line.
- `dS/dt`: persistence slope (for diagnostics; optional for UI).

### 2.2 Persistence input (Phase 1 default)

Phase 1 default (current working assumption):

- `A_t = Y_gated`

Decision notes:
- `A_t` must integrate the **same semantic variable** the dot is built from (unless explicitly re-decided).
- Persistence is allowed to be larger in magnitude than the current dot Y because it is **cumulative** over time.
- Acceptance direction must come from the same signed effectiveness basis used by `Y_raw` (i.e., the same direction mapping and deadbanding that makes `Y_raw` meaningful).
- Do not mix in separate “price-only direction” logic to define acceptance semantics.

Open alternatives (not chosen for Phase 1 unless re-decided):
- `A_t = Y_raw` (more impulse-sensitive; risks integrating artifacts the dot suppresses)
- `A_t = Y` (very stable; can obscure early buildup)

### 2.3 Core dynamics (DC gain / “momentum” mental model)

Working mental model:

- Persistence is cumulative “acceptance momentum”: repeated same-signed `A_t` pulses should build `S_t`.
- `S_t` should be able to reach any magnitude in `[-1, 1]` under sufficiently sustained pressure (i.e., no low fixed-point ceiling).
- `S_t` should change primarily/most visibly due to **opposition**.

Implementation is intentionally unspecified here; the contract is about these outcome properties.

### 2.4 Timebase invariance (dt safety)

Persistence behavior must be stable under variable update spacing:

- With adaptive cadence and/or conditional stepping, `dt` will vary across symbols/regimes.
- The persistence update must be formulated so that “how far `S_t` moves” is not an artifact of `dt` alone.

At minimum, the implementation must:

- use the per-tick `dt_s` explicitly, and
- expose `dt_s` and the applied per-tick coefficients in diagnostics so we can confirm the behavior is timebase-invariant in replay.

### 2.5 Semantics (must be locked before implementation)

We must choose which meaning `S_t` represents:

- **(M1) Structural memory model (opposition-primary, no fast time decay):**
  - `S_t` represents accumulated structural acceptance/rejection evidence.
  - In this model, it is valid for `S_t` to remain elevated through quiet periods; it unwinds primarily when opposing pressure arrives.
- **(M2) Active acceptance model (support-required):**
  - `S_t` represents “currently active acceptance”.
  - In this model, `S_t` must relax toward neutral when supportive input disappears, even without explicit opposition.

Phase 1 Experiment A (current plan): start with **M1** and validate via replay with predefined fail metrics and a fallback plan.

---

## 3) Behavioral requirements (what the line must do)

### 3.1 Sustained acceptance builds visible persistence

Given a trend leg where `A_t` remains consistently positive and meaningfully above noise:

- `S_t` must show a clear positive drift.
- `S_t` must be capable of approaching high magnitude (up to the clamp) under sustained positive pressure.

Open questions:
- What horizon is expected (seconds to reach ~50% of max)?
- What input deadband (if any) defines “meaningfully above noise”?

### 3.2 Pullbacks do not erase persistence immediately

Given a brief counter-move (short-lived opposite-signed `A_t`) during a regime that is otherwise persistent:

- `S_t` should not whipsaw or collapse to near zero immediately.
- `S_t` should only materially roll over if counter-pressure persists long enough (define in time/updates).

### 3.3 Chop / net-zero regimes (must match chosen semantics)

Given a window sequence where `A_t` is near zero on average and frequently changes sign:

- `S_t` must not **falsely accumulate** in chop from micro-oscillation alone.
- Under the Structural memory model (M1), `S_t` may persist until meaningful opposition arrives; “time passing” alone is not required to erase it.
- Under the Active acceptance model (M2), `S_t` should relax toward neutral when support disappears (define relaxation behavior explicitly).

Note:
- If Phase 1 chooses “no fast decay on silence”, then “drift toward 0” in chop must come from the fact that opposing pressures naturally cancel out over time (and/or from an explicit input deadband that suppresses micro-noise).
- Start with relying on `A_t = Y_gated`’s existing deadband/gating to avoid random-walk drift. Add an explicit `|A_t|` deadband only if replay shows clear, untruthful accumulation during chop.

### 3.4 Impulse does not create “fake persistence”

Given a short impulse spike in `A_t` without sustained follow-through:

- `S_t` may respond slightly, but must not look like a regime has built.
- This must be satisfied in a way that matches the chosen semantics:
  - Under **M1 (Structural memory)**: choose **(A)** — a lone impulse must remain small enough that it cannot build large `S_t` (so it’s safe even if `S_t` holds until opposition).
  - Under **M2 (Active acceptance)**: choose **(B)** — add support-required relaxation after impulses when follow-through fails.

---

## 4) Rendering requirements (what the UI must do)

- The persistence line visibility near neutral is an explicit UX choice:
  - Option A: always render (including near `S_t ≈ 0`)
  - Option B: hide (or de-emphasize) at/near `S_t ≈ 0` to reduce clutter
- Styling must remain orthogonal (do not reuse color/size/halo/lean semantics).
- The line must not erase axes (axes remain continuous and readable).

Open questions:
- Should the line use a distinct glyph or thickness when magnitude is high?
- Do we need subtle binning/hysteresis for line position, or should it remain continuous?

---

## 5) Diagnostics requirements (what must be logged/reported)

Must log per tick (or per update):
- `S_t` (`persist_raw`)
- `dS/dt` (`persist_slope`)
- `sign(S_t)` (`persist_sign`)
- the chosen `A_t` source identifier (e.g., `"Y_raw"`, `"Y_gated"`, `"Y"`) (`persist_input`)
- the persistence update `dt_s` used (`persist_dt_s`)
- the applied per-tick coefficients/rates used by the update rule (names TBD; include enough to reconstruct the update)
- the update mode for the tick (e.g., `persist_update_mode ∈ {build, oppose, hold}` or equivalent)
- optional: `persist_activity_flag` (active vs dormant), if the implementation naturally supports it
- optional: a coarse phase label (e.g., `persist_phase ∈ {build, hold, fade, flip}`), if the implementation naturally supports it

Open questions:
- Do we need additional “persistence target” fields for debugging (e.g., `persist_input`)?

---

## 6) Evaluation procedure (how we will judge it)

Primary gate: replay stability on Tier 1 (BTC + SOL top1).

Persistence-specific review set (to be finalized):
- Identify a small list of canonical replay segments (trend up/down, chop, impulse) where persistence should be visually obvious and truthful.
- For each, define expected qualitative behavior (build/hold/decay/rollover) and a few quantitative checks (bounds, monotonicity windows, time-to-build).

Behavioral test targets to define (numbers TBD):
- **Trend build time**: time (or updates) for `S_t` to reach 0.5 and 0.7 in a clean trend leg.
- **Reversal latency**: how long `S_t` should take to stall/roll over after sustained opposite `A_t`.
- **Chop drift bound**: max |S| allowed in net-zero chop segments (to catch random-walk false accumulation).
- **Stale hold duration**: how long |S| may remain elevated while |A_t| stays near zero.
- **Impulse false-persistence check**: max post-impulse |S| and allowed persistence window without follow-through.
- **Opposition unwind half-life**: time/updates to materially reduce |S| under sustained opposite `A_t`.
- **Regime-flip latency**: time/updates for `S` to stall/roll and optionally cross zero after sustained opposite regime.

Procedural steps: see `docs/reference/stability-checklist.md`.

---

## 7) Fallback plan if Experiment A (no-decay / M1) fails

If replay shows that M1 produces confusing or untruthful persistence (e.g., “amnesia” is not the problem; stale `S` becomes noise), do not jump straight to blanket time decay.

Fallback steps (in order):

1) Add **conditional dormancy decay**:
   - only after prolonged low activity / near-zero `|A_t|` periods (as defined by the “stale hold duration” metric).
2) Keep **opposition-driven unwind** primary during active markets.
3) Re-run the same replay gates and judge via the five objective metrics in §6.
