---
id: FL-0069
title: "Distribution State Layer (V1): Separate Panel, Perp-Coherent, Single-Source"
status: Accepted
created: 2026-03-04
---

# FL-0069 — Distribution State Layer (V1): Separate Panel, Perp-Coherent, Single-Source

## Decision

Add a **distribution state layer** as a **separate instrument** rendered in the TUI **in addition to** the Flow Lens plot.

V1 constraints:

1. **Lens remains untouched**
   - No changes to the semantics or computation of X/Y/dot size/halo/lean/persistence.
   - The distribution layer must not gate, weight, or normalize the lens.
2. **Perp-coherent rows**
   - Distribution rows use a single perp instrument family for price/returns and positioning inputs (OI/funding when used).
   - No mixing spot price with perp OI in v1.
3. **Single-source only**
   - V1 runs on one configured source (Binance USDT-margined perp).
   - No selector/failover policy in v1.
4. **Positioning pressure is first-class**
   - The dist layer includes an OI-derived positioning metric (`P`) with explicit availability and bounded normalization.
5. **UI labeling**
   - The TUI must label the distribution panel’s source so users do not assume it matches the lens price series.
6. **Missingness, unless a continuity contract exists**
   - Default: when required inputs are missing or fail validation, row metrics must be marked unavailable rather than
     imputed or silently carried forward.
   - Exception: if a later decision record defines an explicit continuity/availability contract (e.g., OI sampling for
     `P`), follow that contract.

## Rationale

Flow Lens is a structural flow diagnostic. A distribution-state panel can complement it by exposing **effects-only distribution
geometry** (volatility, stretch, memory, positioning) without redefining the lens or turning the project into a signal engine.

Perp coherence is required because OI/funding are perp-native. Single-source v1 reduces complexity and avoids runtime switching
logic that could create non-deterministic behavior.

## Notes

Implementation details and formulas belong in:

- `SPEC-dist-state-layer-phase1.md`
- `docs/reference/dist_state_layer_v1_contract.md`
- `docs/reference/dist_state_layer_binance_inputs.md`
- `docs/decisions/FL-0070-open-interest-sampling-contract.md`
