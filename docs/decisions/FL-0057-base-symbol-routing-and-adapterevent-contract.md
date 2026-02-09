# FL-0057 – Base Symbol Routing + AdapterEvent Contract (Multi-Venue Phase 1)

## Decision

To enable multi-venue integration without venue-specific symbol mapping hacks:

- Add `base_symbol` as an explicit field on `AdapterEvent`.
- Keep `AdapterEvent.symbol` as the venue-native instrument id (for traceability/debugging).
- Router/supervisor routes events by `base_symbol` when present.

Migration safety:

- During a temporary migration window, routing may fall back to legacy mapping only when `base_symbol` is missing.
- After all adapters populate `base_symbol`, remove the fallback path.

## Rationale

Overloading `symbol` with canonical meaning becomes fragile as we add:

- dated futures and institutional codes,
- options instruments,
- venues whose instrument ids do not match simple `BASEQUOTE` conventions.

Explicit `base_symbol` keeps the adapter contract unambiguous and preserves debuggability.

## Status

Accepted (Phase 1). New invariant for multi-venue plumbing.

