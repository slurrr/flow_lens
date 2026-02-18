# FL-0066 – Bounded Trade Dedupe (By Venue Trade ID When Available)

## Decision

The ingestion layer MUST support bounded deduplication of trade events when a stable venue trade identifier is available.

- Dedupe key: `(source_id, symbol, trade_id)`
- Dedupe scope: per-process, bounded by a TTL window (`hygiene.dedupe_ttl_s`).
- If a trade has no stable `trade_id`, it MUST NOT be deduped using heuristic keys (to avoid false positives).

## Rationale

Reconnects, retries, and multi-stream delivery patterns can duplicate trades. Duplicates inflate effort magnitude and can distort
dispersion and effectiveness. A bounded dedupe window prevents double-counting while keeping memory predictable.

Avoiding heuristic dedupe for missing IDs preserves semantic correctness: dropping real trades is worse than counting a rare duplicate.

## Notes

- Dedupe is strictly hygiene. It must not bias control/effectiveness beyond preventing obvious double-counting.

## Status

Accepted.

