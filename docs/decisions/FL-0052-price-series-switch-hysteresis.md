# FL-0052 – Spot-preferred price series with stale-tick hysteresis

## Decision

Price series selection remains spot-preferred, but switching from spot to perp requires a short staleness hysteresis so we
do not ping-pong on brief quiet patches.

Implementation note:

- The original tick-based knob (`spot_price_stale_switch_ticks`) has been superseded by the multi-source price selector
  framework (FL-0060).
- For the Phase 1 single-venue baseline, the equivalent behavior is implemented via:
  - `price_selector_policy = "priority_sticky"`
  - `price_selector_stale_failover_ms = 6000` (≈ 3 ticks at 2s cadence)
  - `price_selector_recovery_confirm_cycles` (controls how quickly we return to the preferred spot source once recovered)

## Rationale

Single-tick freshness switching causes noisy spot/perp ping-pong and inflates price-series switch rate in otherwise liquid symbols. A short stale-tick hysteresis preserves spot preference and carry-forward behavior during brief spot quiet patches while still failing over when spot truly stalls.

## Status

Accepted for single-venue baseline; superseded in multi-source scope by FL-0060.
