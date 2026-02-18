# FL-0064 – Canonical Event Timebase Is Local Receive Time

## Decision

The engine’s canonical event timeline is **local receive time**.

- `Event.timestamp` MUST be set to `ts_recv_ms` (milliseconds since epoch on the local machine).
- `ts_recv_ms` MUST be captured at ingest using `time.time_ns() // 1_000_000` and MUST be enforced as non-decreasing per
  `(source_id, symbol)` by clamping forward by `+1ms` on local clock rollback.
- Downstream hygiene and engine layers MUST treat `Event.timestamp` as authoritative receive time and MUST NOT re-stamp events using
  wall clock time.
- Venue-provided timestamps MUST be treated as **auxiliary metadata** for hygiene/diagnostics, not as the engine clock.
- Windowing, staleness, and price-source freshness decisions MUST be computed against the canonical receive-time clock.

## Rationale

Flow Lens is a real-time structural diagnostic. Its rolling window semantics (“what is in the active window Δ”) must match what
was actually observed in real time on this machine.

Using venue timestamps as the engine clock is not safe because:

- venue timestamps are not a shared clock across venues,
- offsets/drift can make staleness and expiry incorrect (including negative or near-zero staleness),
- “history bursts” can pollute the active window if not strictly filtered.

## Notes

- Venue timestamps remain valuable for computing stale-on-arrival metrics (`wire_lag_ms`) and for debugging feed quality.
- This decision is about time hygiene and determinism; it does not encode interpretation or alter any visual semantics.

## Status

Accepted.
