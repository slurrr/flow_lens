# FL-0041 – Symbol Lifecycle (No Warm-Up, Provisional)

## Decision

On symbol switch, the system remains operational immediately with an empty buffer.
There is **no warm-up phase**. The lens is considered stable within ~10 update cycles.

## Rationale

The engine’s state derives from the rolling window and smoothing. Initial stability
is achieved quickly at the update cadence, and a warm-up gate would add operational
friction without improving semantic correctness.

This is provisional and will be revisited after live feed observation.

## Status

Accepted (Provisional)
