# FL-0062 – Dynamic X Baseline Line (Two-Speed, Breakout-Gated)

## Decision

Add a **dynamic baseline line for X (control)** as a *visual context marker*.

- The dot’s X position semantics remain unchanged.
- The baseline is a **two-speed** exponential smoother toward a rolling-median target with **breakout + confirmation**
  gating:
  - **Peg mode:** baseline updates very slowly when within `breakout_band`.
  - **Re-anchor mode:** baseline updates quickly after a sustained breakout (`confirm_s`) until back within an exit band.

Determinism / hygiene requirements:

- Bootstrap: initialize `baseline_x = x` on first valid state per symbol.
- Timebase: all `dt_s` and `breakout_age_s` must be computed from the engine frame timestamp deltas (not wall clock).
- No-state: when state is unavailable, hold baseline state; reset only on explicit per-symbol context reset.
- Target sampling: append samples only when elapsed `>= target_update_s`; use a time-bounded deque and a
  `max_window_samples` cap derived from `target_window_s/target_update_s`.

UI requirements:

- Render the baseline as a subordinate/dim vertical line behind the dot.
- The canonical center axis at `x=0` remains visually primary; the baseline must not visually override it when near 0.
  - Implement suppression/dimming within a small band around 0 (configurable as `control_baseline.center_suppress_band`).
- Hide the baseline line for a short warmup period after first valid state (default: 120s; `control_baseline.line_hide_warmup_s`)
  to avoid a misleading startup pin. Baseline computation still runs during warmup; only rendering is delayed.

Diagnostics:

- Emit baseline diagnostics only when enabled, and include `baseline_initialized`.

## Rationale

The lens is real-time; users need an immediate read of control shifts (spot stepping in / fading) while preserving the
single, sacred meaning of X. A breakout-gated, two-speed baseline provides a stable “recent normal” anchor in chop, but
re-anchors quickly when the regime truly changes—without recentering X or introducing a second control signal.

## Implementation Notes

See `SPEC-dynamic-x-baseline.md` for the full algorithm, defaults, and tuning ranges.

Acceptance constraint:

- X output from the engine must remain unchanged with baseline enabled vs disabled (exact equality if code path is
  unchanged; otherwise tolerance `<= 1e-12`), and there must be no side effects on other channels.

## Status

Accepted.
