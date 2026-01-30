# FL-0049 – Directional effort sign for effectiveness

## Decision

Effectiveness (Y) uses **net directional effort** (buy vs sell) to determine displacement alignment, rather than spot/perp dominance.

Adapters must provide **aggressor direction** for each effort contribution, enabling the engine to compute:

- `E_dir = Σ(sign(aggressor_side) * effort_value)`
- `disp_rate_dir = sign(E_dir) * disp_rate`
- Y remains normalized by effort and displacement scales per FL-0048

Control (X) remains **spot vs perp dominance** and is unchanged.

## Rationale

With a single venue (or venues that are all perps), “dominant venue” is not a proxy for buy/sell pressure. Without directional effort, Y can label upticks as “rejection” during dumps and downticks as “acceptance,” which violates the system’s intent to show **force direction vs price response**.

Adding aggressor direction is the minimal change that restores semantic correctness without overloading any visual channel.

## Status

Accepted
