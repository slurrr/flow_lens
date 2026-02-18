# Decisions On Hold

## These Decisions Will Need to be Made During Implementation

### Symbol lifecycle management

- switching symbols

- buffer resets

- warm-up period

### Multi-adapter time sync

- handling skew between spot/perp feeds

### Failure modes

- dropped feeds
- stale data
- adapter disconnect behavior
- reconnect-aware hygiene gating (explicit adapter lifecycle events): `docs/decisions/FL-0068-adapter-lifecycle-events-for-hygiene.md`

---

These are operational, not conceptual.
