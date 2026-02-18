# FL-0068 – Adapter Lifecycle Events For Hygiene (Reconnect Awareness)

## Decision

On hold: consider adding explicit **adapter lifecycle events** (`connected` / `disconnected` / `reconnected`) to the runtime ingestion
pipeline so hygiene can re-arm reconnect gates deterministically and without heuristics.

## Rationale

Option A (gap-based + stale-burst-based re-arm) is a good practical solution without architectural churn, but it is inherently
heuristic.

Explicit lifecycle events would allow:

- re-arming reconnect gating exactly when a reconnect occurs (per `source_id`),
- optional per-reconnect hygiene counters (e.g., burst size, stale drops immediately post-connect),
- optional dedupe-scoping per reconnect (if ever desired),
- cleaner diagnostics: “this block of drops was post-reconnect”, not inferred.

## Proposed Shape (If Implemented)

- Introduce a new runtime-queue message type (separate from `Event`) emitted by adapters/supervisor:
  - `AdapterLifecycleEvent { source_id, ts_recv_ms, kind: connected|disconnected }`
- The consumer / hygiene layer handles lifecycle events by:
  - setting `_connect_seen_ms[source_id] = ts_recv_ms` on `connected`
  - clearing per-source rolling wire-lag sample buffers (optional)
  - incrementing reconnect counters for diagnostics (optional)

The engine clock remains canonical receive time (FL-0064). Lifecycle events are hygiene-only and must not affect lens semantics.

## Status

On hold (future refactor candidate).

