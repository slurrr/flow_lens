# FL-0059 – Filter Context Reset Is Full Per-Symbol (Phase 1)

## Decision

Venue/source filtering (future UI control) must be implemented as **engine filtering** (truthful recomputation), and any
filter mask change is treated as a structural context change.

On filter mask change for a symbol, perform one atomic **full per-symbol context reset**:

1) Clear the rolling buffer window Δ for that symbol.
2) Reset smoothing state (X/Y smoothers).
3) Reset persistence state (`S_eff`, `S_dir` and controller counters).
4) Reset normalization windows / cached scales (recent effort scale, dispersion rate scale, etc).
5) Reset visual bin/hysteresis caches (halo/size bins) and lean transitional state.
6) Reset active price selector latch/hysteresis state.
7) Emit a structured diagnostics event `filter_context_reset` including:
   - old/new filter mask,
   - timestamp,
   - symbol.

## Rationale

Filter toggles must not masquerade as market regime shifts.

Partial resets (e.g., clearing buffer but keeping persistence/normalization caches) create semantically mixed state where
early post-toggle windows can look valid but are contaminated by the prior context.

Full reset makes the context switch explicit and auditable.

## Status

Accepted (Phase 1). Defines required behavior when filter toggles are introduced.

