# FL-0063 – UTC-Midnight Control Anchor Tick (Daily X Reference)

## Decision

Add a **single-glyph, dim anchor tick** on the X axis that provides a 24-hour reference for control drift.

- Tick value is computed as the **median X** from sampled X values using the same sampling cadence as the dynamic baseline target.
- Tick remains **hidden** until sufficient evidence is accumulated:
  - primary: at least `midnight_tick_min_samples` samples (default: 60)
  - fallback: at least `midnight_tick_min_elapsed_s` seconds of collection (default: 600s)
- At **00:00 UTC**, the tick is **locked/frozen** at the current anchor value and remains constant for the rest of the UTC day
  (or until explicit context reset/restart).
- Samples continue to collect during the locked day for the next midnight’s lock.

## Rationale

The dynamic baseline line provides “recent normal.” A midnight-locked anchor provides a fixed daily reference so intraday
drift in control (spot participation stepping in or fading) can be read quickly without altering the semantics of X.

## Notes

- This is a **visual context marker only**. It must not alter dot X semantics or influence any engine computations.
- The midnight boundary must be derived from the engine frame timestamp epoch time (UTC), not wall clock.

## Status

Accepted.

