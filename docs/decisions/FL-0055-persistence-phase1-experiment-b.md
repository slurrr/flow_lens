# FL-0055 – Persistence Phase 1 Experiment B (provenance + support-required handoff)

## Decision

Phase 1 persistence moves to Experiment B:

- Line position integrates **persisted effectiveness** (`S_eff`) from `A_eff = Y_gated`.
- Line color encodes **accepted-flow provenance** (`S_dir`) from `A_dir = sign(E_dir) * max(A_eff, 0)`.
- Axis flash remains a **now-pressure** cue from `E_dir / E_total`.

Persistence dynamics use dt-safe approach-to-target updates with explicit mode control:

- `active`: track `A_eff` and `A_dir`
- `pivot`: accelerate `S_eff -> 0` under confirmed accepted-direction handoff, with capped per-second change and cooldown
- `dormant`: quiet-time relax of `S_eff`/`S_dir` toward neutral

All persistence mode inputs, thresholds, taus, and caps are runtime-configurable and must be wired through both live runtime and replay.

## Rationale

Experiment A could stack accepted effectiveness across directional flips, causing the persistence line to stay elevated while accepted-flow side had already changed. Splitting magnitude (`S_eff`) from provenance (`S_dir`) preserves semantic truth while improving regime handoff readability.

dt-safe mode control (active/pivot/dormant) prevents tick-size coupling, supports fail-fast replay validation, and keeps behavior stable across adaptive cadence.

## Status

Accepted (Experimental)
