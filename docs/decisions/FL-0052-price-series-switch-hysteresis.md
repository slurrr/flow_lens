# FL-0052 – Spot-preferred price series with stale-tick hysteresis

## Decision

Price series selection remains spot-preferred, but switching from spot to perp now requires `spot_price_stale_switch_ticks` consecutive stale spot update ticks. Default is 3 ticks (with 2s cadence, about 6s).

Once failover occurs, selection returns to spot immediately when spot becomes fresh again.

## Rationale

Single-tick freshness switching causes noisy spot/perp ping-pong and inflates price-series switch rate in otherwise liquid symbols. A short stale-tick hysteresis preserves spot preference and carry-forward behavior during brief spot quiet patches while still failing over when spot truly stalls.

## Status

Accepted for single-venue baseline; superseded in multi-source scope by FL-0060.
