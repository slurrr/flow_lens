# FL-0021 – Air Pocket Effort Floor Gate

## Decision

Effectiveness is scaled by an effort floor gate:

E_floor = α · median(E over last N ticks)  
gate = clamp(E / (E_floor + ε), 0, 1)

Y = gate · Y_raw

## Rationale

Prevents thin-liquidity price jumps from being interpreted as conviction. Ensures air-pocket moves remain visually distinct from force-driven trends.

## Status

Accepted (Invariant)
