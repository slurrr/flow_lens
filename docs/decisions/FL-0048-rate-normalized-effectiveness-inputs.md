# FL-0048 – Rate-normalized effectiveness inputs

## Decision

Effectiveness inputs are rate-normalized and scaled by symbol-local typical rates:

- `disp_rate = log_return / Δ_seconds`
- `E_rate = E_total / Δ_seconds`
- `disp_scale = median(|disp_rate| over the last scale window)`
- `E_scale = median(E_rate over the last scale window)`

Directional effectiveness uses:

`disp_rate_dir = sign(E_dir) * disp_rate`

`eff_rel = (disp_rate_dir * (E_scale + ε)) / (E_rate * (disp_scale + ε) + ε)`

Then:

`Y_raw = tanh(k * eff_rel)` and `Y = gate * Y_raw`

The air-pocket gate is computed from `E_rate` (not total effort) so it is window-size invariant. Dominance (X) is not gated.

## Rationale

Normalizing by rates and symbol-local scales produces a dimensionless, comparable effectiveness input while preserving the meaning: “Given net directional effort, is price moving in that direction per unit of effort?” Rate normalization keeps Δ invariant, and scale medians prevent notional magnitude from collapsing Y toward zero.

## Status
Accepted (Amended by FL-0049 for directional sign)
