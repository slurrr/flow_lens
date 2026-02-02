# FL-0050 – Persisted effectiveness line with phased opposition gauge

## Decision

Flow Lens will add a new **persisted effectiveness state** (`S_t`) as a leaky integrator of instantaneous effectiveness (`Y_raw`) and render it as a horizontal line in the lens.

This change is **phased**:

1. **Phase 1 (accepted):** implement and ship the persisted effectiveness line only.
2. **Phase 2 (conditional):** add an opposition gauge only if Phase 1 does not provide enough early visibility of counter-trend pressure.

The dot and existing channels keep their current semantics:

- Dot Y remains instantaneous effectiveness (window-boundary truth).
- X, size, halo, and lean remain unchanged.

## Rationale

The current lens is strong at instantaneous structural read, but it under-represents whether acceptance/rejection is **building or fading over time**.

Adding `S_t` solves this without redefining Y:

- preserves present-window truth in the dot,
- adds temporal context as a separate orthogonal layer,
- supports both positive and negative persistence,
- enables asymmetric dynamics (slow build, faster fade) that match observed participation behavior.

A phased rollout reduces risk:

- Phase 1 validates readability and clutter with one new visual layer.
- Phase 2 is only added if needed to expose early counter-pressure before persistence rollover.

## Status

Accepted (Phase 1), Phase 2 Conditional

