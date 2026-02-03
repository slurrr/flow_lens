# FL-0056 – Dot Size Encodes Total Effort Intensity (Per-Symbol)

## Decision

Dot size represents **force magnitude as total effort intensity**, normalized per symbol over a rolling window, and is independent of:

- effectiveness (Y), and
- spot/perp dominance (X).

Implementation definition (dimensionless, bounded):

- `effort_rate = E_total / window_seconds`
- `effort_scale = percentile(rolling effort_rate, size_scale_percentile)`
- `effort_norm = effort_rate / (effort_scale + ε)`
- `size_raw = effort_norm / (1 + effort_norm)`  (monotonic saturating map to `[0, 1)`)
- `size_bin = bin(size_raw)` (existing coarse binning + hysteresis)

New tuning knob:

- `size_scale_percentile` (size-only), defaulting to `effort_scale_percentile` if not specified.

For now, dot-size normalization uses the same rolling window as other scale estimation:

- `scale_window_seconds` (shared)

## Rationale

The size channel must encode “how much pressure is being applied”, not “who is in control”.

The previous dot-size definition (normalized dominance) was tightly coupled to X (`|E_spot − E_perp| / E_total`), creating semantic overlap with the control axis and violating the orthogonality intent of the lens.

Per-symbol normalization preserves cross-symbol UX: a “large dot” means **unusually high effort for that symbol**, rather than “this is BTC so the dot is always bigger”.

## Status

Accepted (Invariant). Supersedes FL-0004 and FL-0022.
