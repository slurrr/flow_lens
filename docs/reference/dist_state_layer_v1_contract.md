---
title: "Distribution State Layer (V1 Contract)"
created: 2026-03-04
status: "contract-notes"
---

# Distribution State Layer (V1 Contract)

This captures the v1 scope contracts agreed during planning, so later specs can cite them.

Primary spec: `SPEC-dist-state-layer-phase1.md`
Primary decision: `docs/decisions/FL-0069-distribution-state-layer-v1.md`
OI/P decision: `docs/decisions/FL-0070-open-interest-sampling-contract.md`

## 1) Perp-coherent rows (required)

Each timeframe row is computed from a **single perp source** such that:

- price/returns inputs (the bar series),
- open interest (OI) inputs, and
- funding inputs (if/when used)

are all sourced from the **same perp instrument family** (same venue/instrument mapping rules).

If a required input is missing or cannot be aligned to the bar close, the affected metric is **unavailable** for that row
(no “best effort” substitution), except where an explicit mode contract overrides this behavior.

For `P`, `FL-0070` is the controlling contract:

- `strict` mode: alignment/quality failures produce unavailable `P` with explicit reason codes.
- `continuous` mode: no semantic fallbacks; still fail-closed when tolerance cannot be met. The goal is to make misses
  operationally rare by improving sampling/verification.

## 2) Single source only (v1 scope)

v1 does **not** implement a selector/failover policy.

- The dist-state layer runs on exactly one configured source (one venue, perp).
- Robustness in v1 means: tolerate gaps/out-of-order/stale data from that source gracefully (missingness, warmup states),
  not “auto-switch to another venue”.

Selector/failover is explicitly deferred to a later phase.

## 3) Positioning pressure `P` is first-class

`P` is treated as a core part of the distribution-state model (not a “nice-to-have”).

Contractual requirements:

- **Availability must be explicit**:
  - v1 rollout uses explicit modes (see `FL-0070`):
    - validation: strict missingness allowed to expose gaps,
    - production target: `P` computed on every close under normal operation (tolerance met); otherwise missing (no
      guessing).
  - OI quality must be exposed via diagnostics in both modes.
- **Normalization must be explicit and bounded**:
  - `P` must be expressed as a dimensionless, bounded value suitable for stable binning/glyph rendering.
  - the normalization must be per-row/per-symbol (no cross-symbol scoring).

Note: the specific `P` formula (e.g., `ΔOI` z-score aligned with return sign; quadrant derivation) belongs in the spec,
but the existence of availability + bounded normalization is a v1 contract.

## 4) UI coherence requirement

Because the lens may use a different price series (often spot-preferred), the TUI must:

- label the dist panel’s configured source (so users do not assume it matches the lens price series), and
- keep the dist panel visually separate from the lens plot (no overlay).
