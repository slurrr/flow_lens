# FL-0065 – Stale-On-Arrival Drops Using Wire-Lag (History Burst Hygiene)

## Decision

When a venue-provided timestamp is available for an incoming message, compute:

`wire_lag_ms = ts_recv_ms - ts_venue_ms`

and drop the message as **stale-on-arrival** when it is stale relative to a rolling baseline:

- Maintain a rolling robust baseline `wire_lag_baseline_ms` (median) per `(symbol, source_id)` using sampled wire-lag values.
- Define `excess_wire_lag_ms = wire_lag_ms - wire_lag_baseline_ms`.
- Drop when:
  - `excess_wire_lag_ms > hygiene.max_excess_wire_lag_ms` (once the baseline is initialized), OR
  - `wire_lag_ms > hygiene.hard_max_wire_lag_ms` (safety cap, even before baseline init)

Additional rules:

- This filter is **one-sided**: negative `wire_lag_ms` MUST NOT be dropped (clock offsets can make lags negative).
- If `ts_venue_ms` is unavailable, stale-on-arrival dropping MUST be skipped for that message (but other hygiene can still apply).
- Adapters MUST normalize `ts_venue_ms` to **ms since epoch** before use; non-parseable or ambiguous-unit timestamps MUST be treated
  as “unavailable”.

## Rationale

Some venues and transports emit reconnect backfill / history payloads. These messages are not representative of current structural
flow and can corrupt effort intensity, dispersion, and effectiveness when ingested into a rolling window model.

Dropping extremely stale data is a hygiene rule, not an interpretation rule.

Using a baseline-relative threshold makes the rule robust to local clock skew (e.g., WSL clock offset) while still capturing
history bursts as large positive *excess* lag.

## Notes

- Threshold choice is operational; defaults are set in the corresponding spec/config.
- This decision is compatible with FL-0064: the engine clock remains `recv`; `wire_lag_ms` is purely a hygiene diagnostic for deciding
  whether a message is “too old to be trusted as present-time”.

## Status

Accepted.
