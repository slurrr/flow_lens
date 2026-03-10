# Liquidity Rollup vs `agent-layer-plan.md` (Alignment Notes)

This note compares the current liquidity rollup spec (`SPEC-liquidity-rollup-layer-v1.md`) to the original agent-layer
planning sketch (`docs/reference/agent-layer-plan.md`).

The goal is to ensure the rollup produces **clean, research-ready liquidity_state** (15m) that an agent and daily
reports can consume without needing raw replay datasets.

## What we now cover (matches the plan)

From `docs/reference/agent-layer-plan.md`, the proposed liquidity accumulator wanted:

- buy/sell aggression totals
- spot/perp buy/sell breakdown
- acceptance/rejection summary
- dominance / control summary
- “poc drift” (anchor drift)

The rollup spec now provides:

- **buy/sell aggression + spot/perp breakdown** (event-summed over the interval)
  - `liquidity_interval.effort_spot_buy/sell`, `effort_perp_buy/sell`, plus `effort_total`
- **composition / participation breadth** (no caps)
  - `liquidity_interval.effort_matrix` (per source × side_type × aggressor_side)
  - `liquidity_interval.effort_by_source` + concentration scalars (`source_hhi`, `source_entropy`, `top_source_share`)
- **accept/reject summary** (outcome, time-integrated)
  - quadrant shares over `(x sign, y sign)` capture “control × acceptance”
  - interpretable state-machine events:
    - `accept_event_count`, `reject_event_count`, time shares, longest runs
  - histogram “mirror” view:
    - `outcome_interval.y_hist` (full array) + `poc_bin`
  - `outcome_interval.mean_y` and related effectiveness plumbing (`mean_eff_raw`, `mean_disp*`)
- **control/dominance summary**
  - `outcome_interval.mean_x`, `mean_dominance`, `mean_e_spot_share`
- **anchor/baseline drift proxy**
  - `control_baseline_drift`, `control_baseline_range`, and baseline-relative deltas

- **POC / volume-profile analog**
  - `price_poc` (log-bucketed full arrays) for:
    - notional POC (always available via `Event.effort_value`)
    - base-volume POC (derived via `effort_value/price`)
  - segmentation (required): total, spot, perp, spot_buy/sell, perp_buy/sell

## Where we provide more than the plan

The planning sketch did not explicitly include:

- price-series composition (which series was used, selector policy shares)
- persistence-line summary fields (persist raw/dir/slope + activity share)
- stability counters (flip counts) and gating occupancy
- deterministic interval price context (`price_open/close`, `log_return`, `range_high/low`)
- explicit occupancy histograms for x/y (full arrays) + POC bin IDs

These additions increase research/report usefulness without changing lens semantics.

## Remaining gaps vs the plan (and why)

### 1) “Acceptance events” / “rejection events” as discrete counts

V1 now includes an interpretable, deterministic acceptance state machine with dwell + deadband, producing discrete
accept/reject counts and run durations.

### 2) “POC sum/samples” as a true accumulator

The plan mentions:

- `poc_sum`, `poc_samples`, `poc_drift`

The repo does not currently expose a true price-volume POC accumulator for the lens. V1 adds `price_poc` as an
observer-only accumulator derived from raw events (volume-at-price).

Optional robustness upgrade: preserve `Event.base_qty` explicitly and cross-check derived base volume (see FL-0074).

## Next logical extension (if needed)

- Add optional “conditional effort” breakdowns:
  - effort composition while `y<0` (rejection) vs `y>0` (acceptance)
  - effort composition while `x>0` (spot control) vs `x<0` (perp control)

These remain clean, queryable, and can be added without caps while keeping the top-level record compact.
