# Spec: Dynamic X Baseline (Control Context Line)

## Purpose

Add a **dynamic baseline line for X (control)** that provides a stable “normal lately” anchor without changing the meaning of X.

This baseline is a **visual context marker**, not a new signal, and must not alter:

1. The dot’s X position semantics.
2. Any engine state semantics used by the lens.

Goal: make it easier to perceive when spot participation meaningfully steps in (or fades) relative to recent context, while keeping the lens real-time and non-laggy.

Additionally, add a **UTC-midnight anchor tick** on the X axis so intraday drift can be read at a glance relative to a fixed daily reference.

## Non-Goals

1. Do not recenter or normalize X such that `x=0` means “normal.” X keeps its literal meaning.
2. Do not use the baseline to gate, weight, or modify the dot position, halo, size, or effectiveness.
3. Do not add a second “control” variable to the dot channels.

## Definitions

1. `x` refers to the existing control value already computed by the engine for the dot position on the X axis.
2. `baseline_x` is the new dynamic baseline line value rendered on the X axis.
3. `target_x` is a robust estimate of “where X has been lately,” used to update `baseline_x`.
4. “Valid state” means a frame where the engine’s state for the symbol is available (e.g., not in a price-series-unavailable condition, not during a symbol switch, not during a source-filter reset).

## High-Level Behavior

The baseline has two update modes:

1. **Peg mode (stable in chop):** baseline moves very slowly (or effectively holds) while X is within a tolerance band around the baseline.
2. **Re-anchor mode (respond to real shift):** baseline moves quickly after a sustained breakout, then returns to Peg mode once re-anchored.

This is a two-speed filter with breakout detection and confirmation to prevent bouncing.

## Algorithm

### Inputs

1. `x`: the current (already-smoothed) X value used for dot position.
2. `frame_ts_ms`: the frame timestamp (ms) used by the engine loop for live and replay.
3. `state_valid`: whether this frame has valid state (see “Valid state” definition above).

### Bootstrap Semantics

For each symbol context:

1. Initialize `baseline_x = x` on the **first valid state** for that symbol.
2. Set `baseline_initialized = true` at that moment.

Rationale: avoid artificial early breakouts that would occur if `baseline_x` defaulted to `0`.

### Baseline Visibility Warmup (Startup Bias Guard)

Hide the baseline line briefly after startup to avoid a misleading “pinned” anchor:

1. Start a per-symbol warmup timer at the first valid state timestamp.
2. Hide the baseline line for `line_hide_warmup_s` after that timestamp (default: 120s).
3. Baseline computation still runs during warmup; only rendering is delayed.

Reason: avoids a misleading startup pin without waiting too long.

### Rolling Target

Maintain a rolling buffer of recent `x` samples. This buffer must be deterministic under both live and replay.

1. `target_x` computation: `median(x_samples over target_window_s)`.

Notes:

1. Median is chosen to be robust to impulse spikes and brief excursions.
2. `target_x` is computed on the same X that is rendered, so it remains a “context of the lens,” not a new measurement.

#### Target Sampling Contract (Deterministic)

To avoid ambiguity and ensure deterministic behavior:

1. Only append a new sample when `elapsed_s >= target_update_s` since the last appended sample for that symbol.
2. Compute `elapsed_s` from `frame_ts_ms` deltas (not wall clock).
3. Store samples as `(sample_ts_ms, x)` and evict anything older than `frame_ts_ms - target_window_s*1000`.
4. Add a hard cap: `max_window_samples = ceil(target_window_s / target_update_s) + 2` and drop oldest if exceeded.

### Breakout Detection

Define:

1. `delta = target_x - baseline_x`.
2. Breakout condition: `abs(delta) > breakout_band`.

Accumulate `breakout_age_s` while breakout condition holds. Reset it to `0` when breakout condition is false.

Breakout is “confirmed” when:

1. `breakout_age_s >= confirm_s`.

#### Timebase Determinism

Both:

1. `breakout_age_s` accumulation, and
2. baseline smoothing `dt_s`

must be derived from frame-to-frame `frame_ts_ms` deltas used by the engine loop, so behavior matches between live and replay and does not drift with wall clock scheduling.

### Baseline Update

Use exponential smoothing with different time constants per mode.

1. Peg mode:

Update toward `target_x` using a slow half-life.

2. Re-anchor mode:

Update toward `target_x` using a fast half-life.

Mode transitions:

1. Peg -> Re-anchor when breakout is confirmed.
2. Re-anchor -> Peg when `abs(delta) <= exit_band`.

Where:

1. `exit_band = breakout_band * exit_band_frac` and `exit_band_frac < 1` provides hysteresis.

### No-State Handling

When `state_valid == false` for a frame:

1. Do not update `baseline_x`.
2. Do not update `breakout_age_s`.
3. Do not update the rolling target buffer.

Only reset baseline-related state on explicit per-symbol context reset (the same reset boundary used by the engine when symbol context is reset).

### Smoothing Math

Use a half-life parameter for interpretability. For a time step `dt_s`, convert half-life to `alpha`:

1. `tau_s = half_life_s / ln(2)`.
2. `alpha = 1 - exp(-dt_s / tau_s)`.
3. `baseline_x = baseline_x + alpha * (target_x - baseline_x)`.

Clamp:

1. `baseline_x` must remain in `[-1.0, 1.0]`.

### Optional Deadband (Noise Guard)

To prevent micro-updates when already re-anchored:

1. If `abs(delta) < peg_deadband`, do not update in Peg mode.

## Session-To-Midnight Anchor Tick (24h Reference)

Render a single dim tick/glyph on the X axis representing a median-X anchor that is locked at midnight.

### Computation

1. Sample `x` using the same sampling cadence as the baseline target (`target_update_s`).
2. Compute the anchor as the **median** of the collected sampled X values.

### Visibility Gate

Hide the tick until enough samples exist:

1. Primary gate: `midnight_tick_min_samples` (default: 60).
2. Fallback gate: `midnight_tick_min_elapsed_s` (default: 600s / 10 minutes).

The tick becomes visible once either gate is met. The sample-count gate is the primary guard.

### Midnight Lock (00:00 UTC)

At `00:00 UTC`:

1. Freeze the tick at the current anchor value.
2. Keep it locked for the rest of the UTC day (or until explicit context reset/restart).
3. Continue collecting samples during the locked day for the next midnight’s lock.
4. At the next midnight, recompute/lock again from that day’s collected samples.

## Configuration

Add a new config section (exact file/format may follow existing patterns):

1. `control_baseline.enabled` (bool)
2. `control_baseline.target_window_s` (float)
3. `control_baseline.target_update_s` (float)
4. `control_baseline.breakout_band` (float)
5. `control_baseline.confirm_s` (float)
6. `control_baseline.exit_band_frac` (float)
7. `control_baseline.peg_half_life_s` (float)
8. `control_baseline.reanchor_half_life_s` (float)
9. `control_baseline.peg_deadband` (float)
10. `control_baseline.max_window_samples` (int, optional; if omitted compute from `target_window_s/target_update_s` as above)
11. `control_baseline.center_suppress_band` (float)
12. `control_baseline.line_hide_warmup_s` (float)
13. `control_baseline.midnight_tick_enabled` (bool)
14. `control_baseline.midnight_tick_min_samples` (int)
15. `control_baseline.midnight_tick_min_elapsed_s` (float)

### Suggested Defaults

Defaults are tuned to:

1. Hold stable during chop and brief “spot stepped in” moments.
2. Re-anchor quickly when the shift is sustained.

Suggested starting defaults:

1. `enabled = true`
2. `target_window_s = 1800` (30 minutes)
3. `target_update_s = 10`
4. `breakout_band = 0.06`
5. `confirm_s = 30`
6. `exit_band_frac = 0.50` (exit band is half the entry band)
7. `peg_half_life_s = 7200` (2 hours)
8. `reanchor_half_life_s = 180` (3 minutes)
9. `peg_deadband = 0.015`
10. `max_window_samples = ceil(target_window_s/target_update_s)+2` (derived; do not hardcode unless needed)
11. `center_suppress_band = 0.02`
12. `line_hide_warmup_s = 120`
13. `midnight_tick_enabled = true`
14. `midnight_tick_min_samples = 60`
15. `midnight_tick_min_elapsed_s = 600`

### Tuning Ranges (As Comments in Config)

Ranges to annotate in config comments:

1. `target_window_s`: 900 to 3600
2. `target_update_s`: 5 to 20
3. `breakout_band`: 0.03 to 0.10
4. `confirm_s`: 10 to 60
5. `exit_band_frac`: 0.30 to 0.70
6. `peg_half_life_s`: 3600 to 21600
7. `reanchor_half_life_s`: 60 to 600
8. `peg_deadband`: 0.005 to 0.03
9. `center_suppress_band`: 0.00 to 0.05
10. `line_hide_warmup_s`: 30 to 300
11. `midnight_tick_min_samples`: 10 to 240
12. `midnight_tick_min_elapsed_s`: 60 to 1800

## UI / Rendering Requirements

1. Render a vertical line at `baseline_x` behind the dot.
2. The line must be visually subordinate to the dot.
3. The line’s meaning must be labeled as “Control Baseline (recent normal)” or equivalent.
4. The line must not reuse “persistence line” styling to avoid semantic confusion.
5. Axis priority rule at `x=0`:
   - The canonical center axis at `x=0` must remain visually primary.
   - If `baseline_x` is very close to `0`, it must not visually override or confuse the center axis (use styling rules such as further dimming, slight dash change, or suppressing baseline when `abs(baseline_x) < center_suppress_band`).
6. Warmup visibility:
   - Do not render the baseline line until `line_hide_warmup_s` has elapsed since the first valid state for the symbol.
7. UTC-midnight anchor tick:
   - Tick is dim and only one glyph on axis.
   - Hide until the visibility gate is met.
   - At midnight lock, tick remains constant for the UTC day.

## Telemetry / Debug Outputs

Add optional debug fields for inspection:

1. `baseline_x`
2. `target_x`
3. `mode` (`peg` or `reanchor`)
4. `breakout_age_s`
5. `delta = target_x - baseline_x`
6. `baseline_initialized` (bool)
7. `baseline_visible` (bool; false during warmup)
8. `midnight_tick_visible` (bool)
9. `midnight_tick_locked` (bool)
10. `midnight_tick_x` (float | None)
11. `midnight_tick_samples` (int)

These are diagnostics only and must not affect lens semantics.

Diagnostics must only be emitted when the baseline feature is enabled.

## Acceptance Criteria

1. Baseline does not visibly bounce during typical chop when dot X oscillates within the breakout band.
2. If dot X shifts and stays displaced (sustained control regime change), baseline begins re-anchoring within `confirm_s` and converges quickly.
3. Brief excursions that revert before `confirm_s` do not re-anchor the baseline.
4. X dot position is unchanged with baseline enabled vs disabled.
   - Requirement: the numeric X output computed by the engine remains identical.
   - If implementation constraints require shared code path changes, allow a tolerance of `<= 1e-12` and no side effects on other channels.
5. Warmup:
   - Baseline line remains hidden for `line_hide_warmup_s` after first valid state, then appears.
6. Midnight tick:
   - Hidden until the visibility gate is met.
   - At 00:00 UTC, locks and remains constant for the rest of the UTC day.

## Test Plan

Unit tests for the baseline state machine:

1. No-breakout scenario: baseline remains stable in Peg mode.
2. Short breakout (< confirm_s): baseline does not enter Re-anchor mode.
3. Sustained breakout (>= confirm_s): enters Re-anchor mode and converges.
4. Hysteresis: does not mode-flip repeatedly near the boundary.
5. Clamping: baseline never exceeds [-1, 1].
6. Warmup visibility: baseline_visible stays false for `line_hide_warmup_s` after first valid state, then true.

Integration sanity:

1. Render baseline on top of existing UI and confirm it tracks expectations across a known capture replay.
2. Confirm baseline does not change dot X value or any other channels.

Unit tests for the midnight tick:

1. Sampling cadence matches `target_update_s`.
2. Visibility gate works (min samples and fallback elapsed time).
3. UTC day boundary detection is epoch-derived and deterministic.
4. Lock at 00:00 UTC and remain constant until the next 00:00 UTC.
5. Explicit context reset clears tick state.
