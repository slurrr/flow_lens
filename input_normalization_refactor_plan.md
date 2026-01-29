# Input Normalization Refactor Plan (Global `k`, Symbol-Local Scales)

## Goal

Keep a **global** `tanh_k` while making effectiveness input (`eff_raw`) **dimensionless** and **comparably scaled** across symbols by normalizing both:

- displacement (log-return) by a symbol-local typical displacement rate, and
- effort by a symbol-local typical effort rate,

so the TUI’s Y-axis has a consistent “feel” without hiding regime shifts.

This plan targets the invariant outcome:

> Convert raw spot + perp participation into a normalized, source-agnostic state model that shows who is in control and whether their effort is accepted or absorbed.

## Non-goals (Guardrails)

- No symbol-specific `tanh_k`.
- No multi-symbol scoring, signals, alerts, or overlays.
- No overloading visual channels (X/Y/size/halo/lean semantics unchanged).

## Definitions (per symbol, per update)

Let the **active window length** be `Δ_seconds`.

1) **Window-size invariant displacement rate**

- `log_return = ln(price_end / price_start)` (already computed)
- `disp_rate = log_return / (Δ_seconds + ε)`
- `disp_rate_dir = sign(dominance) * disp_rate`
  - `dominance = E_spot - E_perp` (positive means “spot dominant”)
  - This preserves **directional effectiveness**: acceptance vs rejection is relative to who is dominant.

2) **Window-size invariant effort rate**

- `E_total = E_spot + E_perp`
- `E_rate = E_total / (Δ_seconds + ε)`

3) **Rolling typical scales (slow to adapt)**

- `disp_scale = median(|disp_rate| over N_scale)` (symbol-local)
- `E_scale = median(E_rate over N_scale)` (symbol-local)

Notes:
- `N_scale` should be expressed in **time** (e.g., last 10 minutes of updates) rather than ticks, because update cadence can vary with `Δ`.
- Apply a **slow update** (or asymmetric smoothing) so regime shifts remain visible (avoid baseline-chasing).

4) **Relative effectiveness per relative effort (dimensionless)**

- `eff_rel = (disp_rate_dir / (disp_scale + ε)) / (E_rate / (E_scale + ε))`
- equivalently: `eff_rel = (disp_rate_dir * (E_scale + ε)) / (E_rate * (disp_scale + ε) + ε)`

5) **Y transform**

- `Y_raw = tanh(k * eff_rel)` with **global** `k`
- Keep the **air-pocket gate** on Y if desired:
  - compute the gate from `E_rate` (not `E_total`) so it remains window-size invariant
  - `Y = gate * Y_raw`

## Implementation Steps

### Step 0 — Decide scale update mechanics (one choice)

Pick one and document in the decision record:

- **Simple rolling median (phase 1):**
  - keep `Deque[float]` for `|disp_rate|` and `E_rate` samples
  - compute `median()` each update
  - optional: smooth the median into `disp_scale`/`E_scale` with a small alpha (or asymmetric rates)
- **Streaming quantiles (phase 2 / optional):**
  - only if median() becomes a performance bottleneck across many symbols

### Step 1 — Plumb `Δ_seconds` into the state computation

Current `FlowFrame` does not carry the active window duration.

- Add `window_seconds: float` (or `window_ms: int`) to `src/flow_lens/models/flow_frame.py`
- Populate it in `src/flow_lens/engine/loop.py` from the `RollingEventBuffer` window (`window_delta_ms`)
- Record it in `StateSnapshot` for diagnostics (optional but recommended)

### Step 2 — Compute `disp_rate`, `E_rate`, and store scale samples

In `src/flow_lens/engine/state_engine.py`:

- Compute `disp_rate_dir` and `E_rate` from `window_seconds`
- Maintain rolling histories and compute `disp_scale`, `E_scale`
- Keep explicit `ε` handling for zero/near-zero scales

### Step 3 — Redefine effectiveness input

Replace:

- `eff_raw = disp / effort_norm`

with:

- `eff_raw := eff_rel` (dimensionless relative effectiveness)

Then:

- `Y_raw = tanh(k * eff_raw)`

### Step 4 — Make the air-pocket gate window-invariant

If the gate remains in use:

- compute floor + gate from `E_rate` (not `E_total`)
- keep the existing asymmetric/slow behavior as defined by decisions

### Step 5 — Keep X semantics clean (control only)

Dominance (X) should remain:

- `X_raw = (E_spot - E_perp) / (E_spot + E_perp + ε)`

and should **not** be gated by the air-pocket floor (gate stays a Y-only damping tool).

### Step 6 — Recalibrate `tanh_k` (global)

After normalization, re-run diagnostics and pick `k` so that (for typical conditions) `|Y_raw|` uses meaningful range without saturating.

Practical acceptance target:

- `p90(|Y_raw|)` ~ `0.3–0.6` on majors during normal activity
- avoid `p90(|Y_raw|)` ~ `0.95+` (sign-function behavior)

### Step 7 — Validate against storyboard scenarios

Using `mock_main` and live diagnostics:

- Uptrend + broad participation should push Y positive (accepted)
- Uptrend + concentrated effort / poor follow-through should keep Y near 0 or negative (absorbed/rejected)
- Ensure the same narrative “reads” similarly across symbols (TUI consistency) while still reflecting regime changes over time

## Diagnostics / Acceptance Criteria

Minimum expected shifts after refactor:

- `y_near_zero_rate` should drop materially from ~1.00 on active symbols
- Y should move in response to actual directional displacement, not just noise
- Adaptive window should not distort Y amplitude (rate invariance)

## Files Expected to Change (when implementing)

- `src/flow_lens/models/flow_frame.py` (add window duration)
- `src/flow_lens/engine/loop.py` (populate window duration)
- `src/flow_lens/engine/state_engine.py` (compute rates, scales, eff_rel; gate on rates; ungate X)
- `docs/decisions/*` (new decision + supersessions)

