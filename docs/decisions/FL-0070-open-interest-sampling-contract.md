---
id: FL-0070
title: "Open Interest Sampling Contract (V1 Rollout): Continuous REST, Strict Validation -> Continuous Availability"
status: Proposed
created: 2026-03-05
---

# FL-0070 — Open Interest Sampling Contract (V1 Rollout): Continuous REST, Strict Validation -> Continuous Availability

## Decision

For the distribution-state layer v1 (Binance USDT-margined perp), treat open interest (OI) as a **continuously sampled,
timestamped state** (not “OI at the exact close tick”).

Rollout policy:

- validation mode: strict missingness is allowed to expose issues quickly,
- production target: `P` is computed on every close under normal operation by ensuring OI is continuously monitored and
  sampled close to each boundary; if tolerance cannot be met, `P` should be missing rather than guessed.

This is a proposal to make the transition explicit and testable.

### 1) OI source

- Live OI: `GET /fapi/v1/openInterest?symbol=BTCUSDT` (`openInterest` in contract units, `time` as venue time when present).
- Warmup OI: `GET /futures/data/openInterestHist?symbol=BTCUSDT&period=<tf>` using `sumOpenInterest` in contract units.

Do not use `sumOpenInterestValue` for `P` in v1.

### 2) Continuous sampler

Run a continuous REST sampler at a configured cadence (`oi_poll_interval_ms`) to maintain:

- `oi_last: float` (last observed OI value),
- `oi_last_venue_time_ms: int | None`,
- `oi_last_recv_ms: int`,
- `oi_last_sample_seq: int` (strictly increasing local sequence id).

Idempotency / ordering:

- Maintain a monotonic sampler watermark `last_order_key`.
- `order_key` is deterministic:
  - primary: `venue_time_ms` when present,
  - fallback: `ts_recv_ms` when venue time is missing,
  - tie-break: `sample_seq` (strictly increasing).
- Accept sample iff `order_key > last_order_key`; otherwise discard.
- Missing `venue_time_ms` does not reset ordering state.

### 3) Selection at kline close and same-close coherence

At each kline close time `t_close` (venue ms), define the OI value used for `ΔOI` as:

- `OI(t_close) := oi_snapshot_close.oi` where `oi_snapshot_close` is an atomic read of:
  - `oi_last`, `oi_last_venue_time_ms`, `oi_last_recv_ms`, `oi_last_sample_seq`.

This is intentionally TF-agnostic and avoids per-timeframe “OI join” failure modes.

Tolerance contract (binding):

- Define a single global tolerance window `oi_tolerance_ms`.
- An OI sample is eligible for computing `P` at close `t_close` iff:
  - `venue_time_ms` is present (see `oi_time_missing_policy` below), and
  - `abs(venue_time_ms - t_close) <= oi_tolerance_ms`.

Same-close multi-timeframe coherence (binding):

- For a given close id `(source_id, kline_close_ms)`, freeze exactly one `oi_snapshot_close`.
- All rows that share that close id (`3m/15m/1h/4h` when aligned) must use this same frozen snapshot.
- Do not allow per-row drift for the same close id.

The engine must also compute diagnostics for tuning (not gating):

- `oi_offset_ms := (oi_last_venue_time_ms - t_close)` when venue time is present, else `None`.
- `oi_staleness_ms := (t_close - oi_last_venue_time_ms)` when venue time is present, else `None`.

### 4) Availability contract for P (mode-dependent, locked defaults)

Initialization prerequisite:

- Dist-state must not declare itself “initialized” until it has an initial `oi_last` baseline, sourced from either:
  - warmup OI history (preferred), or
  - the first successful live OI snapshot.

Mode A (`p_availability_mode = "strict"`, validation):

- `P` may be unavailable only for fixed miss reasons (enum below).
- staleness/offset/time-missing are logged and counted as explicit reasons.

Mode B (`p_availability_mode = "continuous"`, production target):

- No semantic fallbacks:
  - do not force `P=0`,
  - do not hold the last `P` as a substitute.
- `P` is computed only when the chosen OI sample meets tolerance; otherwise `P` is unavailable (and logged).
- The intent is to make “`P` unavailable” operationally rare by improving sampling/verification, not by inventing values.

Mode clarity:

- Both modes use the same `P` math, the same tolerance policy, and the same miss-reason enum + diagnostics fields.
- “Continuous” refers to sampling/verification posture (tuned to make misses rare), not different computation.

Mode default + switching (locked for v1 rollout):

- default mode is `strict`.
- switching to `continuous` is an explicit config change; no implicit auto-switch in v1.
- recommended practice: compute and review quality stats per timeframe so 3m cannot mask 1h/4h behavior.

Strict miss reason enum (mandatory in diagnostics):

- `not_initialized`
- `no_sampler_value`
- `stale_over_limit`
- `offset_over_limit`
- `time_missing_policy`

Miss reason mapping (binding):

- `not_initialized`: dist-state not initialized (no OI baseline, no previous OI for delta, or warmup incomplete).
- `no_sampler_value`: sampler has no current OI value.
- `time_missing_policy`: sample has missing `venue_time_ms` and policy rejects it.
- `stale_over_limit`: `venue_time_ms < t_close - oi_tolerance_ms`.
- `offset_over_limit`: `venue_time_ms > t_close + oi_tolerance_ms`.

### 5) Verification (recommended; diagnostics + robustness)

Verify-fetch must be bounded by config:

- `oi_verify_enabled: bool` (default `true`)
- `oi_verify_timeframes: ["3m","15m","1h","4h"]` (default)
- `oi_verify_timeout_ms: int` (default `1200`)
- `oi_verify_max_rate_per_min: int` (default `24`)
- `oi_time_missing_policy: "reject"` (default; v1)

When enabled, perform verify fetch under these limits and log:

- `oi_verify`, `oi_verify_venue_time_ms`,
- `oi_verify_diff := oi_verify - oi_last`,
- `oi_verify_offset_ms := oi_verify_venue_time_ms - t_close` (when present).

If verify fetch succeeds and has a newer venue time than the sampler’s current sample, it may update `oi_last`
deterministically (newer wins).

Close-time selection with verification (binding when verify is enabled for the close’s timeframe):

- For close id `(source_id, kline_close_ms)`, build candidate samples:
  - `candidate_a`: the atomic sampler snapshot at close processing time.
  - `candidate_b`: the verify snapshot (if fetched successfully within timeout).
- Filter candidates by the tolerance contract.
- If multiple candidates pass, choose the one with the smallest `abs(venue_time_ms - t_close)`; tie-break newer
  `venue_time_ms`, then larger `sample_seq`.
- Freeze the chosen candidate as `oi_snapshot_close` for that close id (same-close coherence rule applies).

Bootstrap diagnostics (mandatory):

- `oi_bootstrap_source: "warmup_hist" | "first_live"`
- `oi_bootstrap_age_ms` at init

## Rationale

- Binance’s live OI endpoint can return venue timestamps that lag server time and can repeat across calls; “first REST call
  after close implies post-close OI” is not enforceable as a hard correctness rule.
- Continuous sampling removes the brittle dependency between a particular kline timeframe and the ability to compute `P`.
- `P` is operator-critical and should be continuous in production.
- strict-mode missingness is still valuable during stabilization to expose real gaps quickly.
  In continuous mode, “continuous” refers to monitoring and capture, not to inventing fallback values.

## Notes

This decision concerns the **distribution-state panel only** and does not modify Flow Lens semantics.
