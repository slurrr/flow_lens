# FL-0038 – Buffer Performance Model

## Decision

Buffers are implemented using append-only queues with head pruning.

Operations required per tick:
- append new events
- remove expired events from head
- aggregate over active events

## Rationale

Trade streams are append-dominant. Queue-based buffers provide O(1) amortized operations and are suitable for high-frequency ingestion.

## Status

Accepted (Implementation Guideline)
