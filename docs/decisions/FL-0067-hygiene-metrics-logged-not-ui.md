# FL-0067 – Hygiene Metrics Are Logged (Diagnostics), Not Surfaced In UI

## Decision

Input hygiene metrics MUST be logged to diagnostics output when diagnostics are enabled, and MUST NOT be surfaced in the UI.

At minimum, hygiene diagnostics should include:

- stale-on-arrival drops count per `(symbol, source_id)` when `ts_venue_ms` is present,
- dedupe drops count per `(symbol, source_id)` when `trade_id` is present,
- wire-lag distribution summaries (at least median and p95) per `(symbol, source_id)` when `ts_venue_ms` is present.

## Rationale

Hygiene metrics are essential for interpreting lens behavior and debugging adapters, but surfacing them in the UI risks competing with
the lens’ core semantic channels (position/size/halo/lean) and encourages “dashboard thinking”.

Diagnostics output provides observability without diluting the lens.

## Notes

- Logging cadence must be bounded to keep diagnostic files from growing unbounded.
- Event shape and cadence key are contractual:
  - event_type: `hygiene_metrics`
  - cadence key: `(symbol, source_id)`
  - emission is bucketed by `bucket_id = now_ms // (log_interval_s * 1000)` (at most one record per key per bucket).
- Wire-lag fields reflect raw `wire_lag_ms` distribution; when a baseline-relative stale drop is enabled, reports should interpret
  drops as “excess lag” beyond the baseline (see FL-0065 / hygiene spec).

## Status

Accepted.
