# SPEC — Input Hygiene & Canonical Timebase (Recv-Time Engine Clock)

## Goal

Improve Flow Lens correctness and stability by ensuring the engine operates on a **clean, deterministic event timeline** and by filtering obvious feed pathologies (history bursts, duplicates) *before* they enter the rolling window.

This spec is strictly about **input hygiene**, not venue scoring or venue selection.

## Design Constraints

- Preserve Flow Lens intent: “single-symbol structural flow diagnostic”, not a decision engine.
- Do not overload visual channels or introduce interpretation logic.
- Adapters remain “dumb”; hygiene is implemented in a thin ingestion layer plus minimal adapter metadata.
- Hygiene metrics MUST be logged to diagnostics but MUST NOT be surfaced in the UI. (FL-0067)
- Determinism: hygiene behavior and logged metrics must be stable across live/replay for identical inputs.

## Applicable Decisions

- FL-0064: Canonical event timebase is **local receive time**.
- FL-0065: Drop **stale-on-arrival** messages using `wire_lag_ms` threshold (one-sided).
- FL-0066: Bounded dedupe by stable venue trade id (no heuristic dedupe).
- FL-0067: Hygiene metrics logged (diagnostics), not UI.

## Background (Why This Exists)

The venue discovery tournament demonstrated:

- venue timestamps are not a shared clock and can drift/offset in ways that break staleness/windowing,
- reconnect/history bursts and delayed prints do occur and can pollute the rolling window,
- duplicate trades can occur during reconnects/retries and must not inflate effort.

The lens should reflect **what we actually received** in real time and should ignore data that is clearly not “present-time”.

## Proposed Architecture

### 1) Canonical Engine Timestamp (Recv Time)

Per FL-0064:

- The engine uses `ts_recv_ms` as the canonical timeline.
- `Event.timestamp` is **always** `ts_recv_ms` in live operation.
- Venue timestamps are carried only as metadata for hygiene/diagnostics, never for windowing.

#### Clock Source + Monotonic Guard (Redline)

Adapters MUST compute `ts_recv_ms` at ingest using:

- `ts_recv_ms = time.time_ns() // 1_000_000`

Because `time.time_ns()` can move backwards (NTP / clock steps), adapters (or a shared helper they call) MUST enforce a
non-decreasing receive timestamp **per `(source_id, symbol)`**:

- If `ts_recv_ms <= last_ts_recv_ms[(source_id, symbol)]`, clamp to `last_ts_recv_ms + 1`.

#### Hygiene Must Not Re-Stamp Receive Time

The hygiene layer MUST treat `Event.timestamp` as the authoritative receive timestamp and MUST NOT call wall-clock functions
(`time.time_ns()`, `time.time()`) to re-stamp events.

Rationale: this keeps behavior deterministic under replay and avoids duplicating time semantics in multiple layers.

### 2) Event Schema Changes

Extend `flow_lens.models.event.Event` to carry auxiliary metadata needed for hygiene:

Required:

- `timestamp`: `int` (canonical, `ts_recv_ms`)

Optional:

- `venue_timestamp_ms`: `int | None`
  - The timestamp contained in the venue message, if provided.
  - Used only for wire-lag calculation and diagnostics.
- `trade_id`: `str | None`
  - Stable per-venue trade identifier when available.
  - Used only for bounded dedupe.

Non-goal:

- Do not add venue-time ordering to the engine or any “global time correction” mechanism in this phase.

### 3) Hygiene Ingestion Layer (Pre-Buffer)

Add a thin ingestion layer (owned by the runtime/adapter consumer) that receives `(symbol, source_id, event)` and can:

- compute wire-lag when possible,
- drop stale-on-arrival messages,
- drop duplicates when possible,
- update hygiene counters/quantiles for diagnostics logging.

This layer is the only place that is allowed to “look at metadata” and decide whether an event is fit for ingestion.

### 4) Stale-On-Arrival Filter (Wire-Lag Drop)

Per FL-0065:

- If `event.venue_timestamp_ms` is present:
  - `wire_lag_ms = event.timestamp - event.venue_timestamp_ms`
  - estimate a rolling baseline `wire_lag_baseline_ms` (median) per `(symbol, source_id)`
  - define `excess_wire_lag_ms = wire_lag_ms - wire_lag_baseline_ms`
  - drop if `excess_wire_lag_ms > hygiene.max_excess_wire_lag_ms` once baseline is initialized
  - always drop if `wire_lag_ms > hygiene.hard_max_wire_lag_ms` (safety cap, even pre-baseline)
- If `event.venue_timestamp_ms` is missing:
  - do not drop on this rule (but record `venue_ts_missing` for diagnostics).

Additional invariants:

- Do not drop negative `wire_lag_ms` values (log as “negative lag observed” to aid debugging).
- This filter is for **obvious history** only; it is not intended to distinguish 10–50ms leadership.

#### Baseline Sampling & Initialization (Contract)

To keep memory and CPU bounded, the baseline estimator MUST be sampled at a fixed cadence per `(symbol, source_id)`:

- Only append a baseline sample when `event.timestamp - last_baseline_sample_ts_ms >= hygiene.wire_lag_baseline_sample_interval_ms`.
- Baseline is computed over samples within the last `hygiene.wire_lag_baseline_window_s` (bounded by time), with an additional
  `hygiene.wire_lag_baseline_max_samples` cap as a hard guard.

Baseline initialization gate:

- `wire_lag_baseline_ms` is considered initialized only after at least `hygiene.wire_lag_baseline_min_samples` baseline samples.
- Before initialization, stale-on-arrival dropping uses only the safety cap `wire_lag_ms > hygiene.hard_max_wire_lag_ms`.

Update rule:

- Baseline samples MUST be updated only from events that are ultimately **accepted** (not dropped as stale-on-arrival) to avoid
  learning from history bursts.

### 5) Bounded Dedupe (By Trade ID Only)

Per FL-0066:

- If `event.trade_id` is present:
  - compute dedupe key `(symbol, event.source_id, event.trade_id)`
  - drop if key was seen within `hygiene.dedupe_ttl_s`
- If `event.trade_id` is missing:
  - do not dedupe using heuristic keys

Implementation notes:

- Use an LRU/TTL cache with bounded memory:
  - cap size derived from expected msg rate * TTL (conservative) and evict oldest.

### 6) Reconnect / History Burst Handling

Primary mitigation is **wire-lag drop** (FL-0065), which directly targets “old data delivered now”.

Optional source-specific guard (only if needed after live observation):

- A short post-connect “hygiene gate” for sources known to backfill:
  - still accept fresh events immediately,
  - but apply a temporarily stricter stale threshold for the first `hygiene.connect_gate_s`:
    - drop if `excess_wire_lag_ms > hygiene.connect_gate_max_excess_wire_lag_ms` once baseline is initialized, and/or
    - drop if `wire_lag_ms > hygiene.connect_gate_hard_max_wire_lag_ms` (safety cap, pre-baseline)

This guard MUST be driven by freshness (`wire_lag_ms`), not by blindly ignoring the first N seconds of all traffic.

### 7) Hygiene Diagnostics Logging (No UI)

Per FL-0067:

Log hygiene metrics to diagnostics output when diagnostics are enabled.

Requirements:

- Bounded cadence: emit at most once per `(symbol, source_id)` per `hygiene.log_interval_s` (default: 10s).
- Cadence key and bucketing are contractual (see below).
- Do not emit one record per event.

#### Contractual Event Shape + Cadence Key (Redline)

Event type: `hygiene_metrics`

Cadence key: `(symbol, source_id)`

Cadence bucketing:

- Define `bucket_ms = hygiene.log_interval_s * 1000`.
- Define `bucket_id = now_ms // bucket_ms` (where `now_ms` is the engine cadence clock used for state updates/replay).
- Emit **at most one** `hygiene_metrics` record per `(symbol, source_id, bucket_id)`.
- Record MUST include:
  - `interval_start_ms = bucket_id * bucket_ms`
  - `interval_end_ms = interval_start_ms + bucket_ms`

Record shape (exact field names):

- `ts_wall_ms`, `now_ms`, `symbol`, `source_id`
- `interval_start_ms`, `interval_end_ms`
- `samples_with_venue_ts`: count of events observed in the interval where `venue_timestamp_ms` is present
- `wire_lag_ms_p50`, `wire_lag_ms_p95` (computed over events where `venue_timestamp_ms` is present)
- `stale_on_arrival_dropped`: count in this interval
- `dedupe_dropped`: count in this interval
- `venue_ts_missing`: count in this interval
- `negative_wire_lag`: count in this interval (`wire_lag_ms < 0`)
- `future_venue_ts`: count in this interval (`venue_timestamp_ms > ts_recv_ms + hygiene.future_venue_ts_grace_ms`)

The diagnostics logger must not display these metrics in the UI; they are file-only.

## Adapter Requirements (Minimal, Non-Interpretive)

Each adapter must:

- capture `ts_recv_ms` locally when the message is received/processed,
- set `Event.timestamp = ts_recv_ms`,
- set `Event.venue_timestamp_ms` from the venue payload when provided,
- set `Event.trade_id` from the venue payload when provided.

Venue timestamp normalization contract (Redline):

- Adapters MUST normalize the venue-provided timestamp to **ms since epoch** before populating `Event.venue_timestamp_ms`.
- If the venue timestamp cannot be parsed or its units are ambiguous, `venue_timestamp_ms` MUST be set to `None`.

Adapters must not:

- drop stale-on-arrival themselves (centralize policy),
- apply dedupe heuristics themselves (centralize policy),
- normalize effort or interpret flow (engine responsibility).

## Configuration

Add `runtime.hygiene` config block (exact TOML shape can match existing config conventions):

- `enabled: bool` (default: true)
- `max_excess_wire_lag_ms: int` (default: 2000)
- `hard_max_wire_lag_ms: int` (default: 30000)
- `wire_lag_baseline_window_s: int` (default: 300)
- `wire_lag_baseline_sample_interval_ms: int` (default: 200)
- `wire_lag_baseline_min_samples: int` (default: 30)
- `wire_lag_baseline_max_samples: int` (default: 2000)
- `dedupe_ttl_s: int` (default: 30)
- `log_interval_s: int` (default: 10)
- `future_venue_ts_grace_ms: int` (default: 250)
- `connect_gate_s: int` (default: 0, disabled by default)
- `connect_gate_max_excess_wire_lag_ms: int` (default: 500)
- `connect_gate_hard_max_wire_lag_ms: int` (default: 5000)

Defaults are chosen to remove obvious history without turning hygiene into a latency contest.

## Filter Order (Redline)

Filter order MUST be deterministic and is locked as:

1. dedupe (if `trade_id` present)
2. stale-on-arrival drop (if `venue_timestamp_ms` present)
3. enqueue into rolling buffer

## Implementation Plan (Concrete)

1. Extend `Event` to include `venue_timestamp_ms` and `trade_id` (optional).
2. Update all adapters to:
   - capture `ts_recv_ms`,
   - set `Event.timestamp` to `ts_recv_ms`,
   - populate `venue_timestamp_ms` and `trade_id` where available.
3. Implement a central hygiene filter in the adapter consumption path:
   - stale-on-arrival drop (wire-lag),
   - bounded dedupe (by trade id),
   - per-source counters and wire-lag quantiles.
4. Integrate hygiene metrics emission into `DiagnosticLogger` with bounded cadence.
5. Add unit tests for:
   - stale-on-arrival drop logic (one-sided; no-drop on missing venue ts),
   - dedupe TTL behavior (no heuristic dedupe),
   - windowing/staleness correctness when venue ts is offset (ensures we’re using recv time).

## Acceptance Criteria

- Rolling window expiry and staleness are correct under venue timestamp offsets (no “permanently fresh” sources due to clock skew).
- History bursts (wire-lag above threshold) do not affect X/Y/halo/size.
- Duplicate trades (where trade_id exists) do not inflate total effort.
- Hygiene metrics appear in diagnostics logs when enabled and do not appear in UI.
- No changes to visual channel semantics; no new interpretation logic introduced.

### Explicit Limitation (No-Venue-TS Reconnect Bursts)

Sources that do not provide `venue_timestamp_ms` are protected only by:

- bounded dedupe (if they provide a stable `trade_id`)

They are **not** protected by stale-on-arrival dropping, by design (FL-0065). This limitation must be reflected in diagnostics
(`venue_ts_missing` counts) to avoid false confidence.
