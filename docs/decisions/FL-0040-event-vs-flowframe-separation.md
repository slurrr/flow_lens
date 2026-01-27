# FL-0040 – Event vs FlowFrame Separation

## Decision

Two distinct data structures are maintained:

- `Event` represents a single raw market event stored in the rolling buffer.
- `FlowFrame` represents a time-sliced aggregation of effort contributions passed into the state engine.

Conversion path:

adapter → Event → rolling buffer → FlowFrame → engine

Adapters emit Events. The engine consumes FlowFrames. Adapters do not construct FlowFrames directly.

## Rationale

This separation preserves clean layering:

- Adapters handle ingestion only.
- Buffer manages time-domain state.
- Engine operates on windowed structural snapshots.

Without this distinction, normalization, aggregation, or smoothing logic could leak into adapters or the buffer, violating system architecture.

## Status

Accepted (Architectural Invariant)
