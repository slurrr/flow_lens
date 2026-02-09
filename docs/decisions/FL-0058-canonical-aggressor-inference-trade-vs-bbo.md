# FL-0058 – Canonical Aggressor Inference (Trade vs BBO) + Gates (Phase 1)

## Decision

When a venue does not provide native aggressor side, `aggressor_mode=inferred` is permitted in Phase 1 only if it uses the
single canonical inference method below and reports required diagnostics.

Canonical method (deterministic):

1) Require BBO context at inference time (best-bid and best-ask), using the nearest prior BBO within a max age budget.
2) Map side with a fixed chain:
   - if `trade_px >= ask_px - epsilon` → `buy`
   - else if `trade_px <= bid_px + epsilon` → `sell`
   - else compare to mid:
     - if `trade_px > mid` → `buy`
     - if `trade_px < mid` → `sell`
     - if `trade_px == mid` → tick rule fallback (vs last trade price)
3) If no valid BBO within age budget: do not infer (unknown side for that print).

Phase 1 rule:

- Unknown-side prints must be treated as dropped for that source (counted + logged). They must not silently enter the
  engine as “unsigned” effort.

Defaults (Phase 1):

- `bbo_max_age_ms = 500`
- `epsilon = 0.5 * tick_size` (tick-size-based, not fixed absolute)
- Gates:
  - `%unknown_side <= 5%` (warn at 3%)
  - `bbo_age_ms_p95 <= 500ms` (warn at 300ms)

Required diagnostics (per source, per capture/replay):

- `aggressor_mode` (`native|inferred|none`)
- `% inferred_with_bbo`
- `% inferred_mid_fallback`
- `% inferred_tick_rule_fallback`
- `% unknown_side`
- `bbo_age_ms_p50/p95`

## Rationale

“Inferred” aggressor side is acceptable only if:

- it is mechanically defined and consistent across venues, and
- its error modes are observable (so we can fail fast rather than debate “looks wrong”).

The BBO-based method is auditable and avoids per-adapter bespoke inference logic.

## Status

Accepted (Phase 1). Applies only to `aggressor_mode=inferred` sources.

