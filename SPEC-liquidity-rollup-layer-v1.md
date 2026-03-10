---
title: "SPEC — Liquidity Rollup Layer (V1): 15m Time-Weighted Summaries + Rolling 24h Report Inputs"
created: 2026-03-10
status: "draft (implementation-ready; schema intentionally evolvable)"
related:
  - "docs/decisions/FL-0064-canonical-event-timebase-recv.md"
  - "docs/decisions/FL-0067-hygiene-metrics-logged-not-ui.md"
  - "docs/decisions/FL-0073-liquidity-rollup-observer-v1.md"
  - "docs/decisions/FL-0074-extend-event-schema-for-research-rollups-v1.md"
---

# SPEC — Liquidity Rollup Layer (V1): 15m Time-Weighted Summaries + Rolling 24h Report Inputs

This spec defines a **liquidity rollup observer** that converts the continuous Flow Lens state stream into compact,
time-weighted interval summaries suitable for:

- a “what has liquidity been doing?” operator view,
- 24h summaries and daily reports,
- a future non-deterministic agent layer (interpretation only).

## 0) Goals (v1 initial)

1. Summarize liquidity conditions using **interval aggregation**, not point sampling.
2. Emit one compact rollup record every **15 minutes**.
3. Support a rolling **24h** summary/report by consuming the last 96 rollups.
4. Preserve lens invariants: no new bar logic in adapters, no changes to lens computation.

## 1) Non-goals (v1 initial)

1. No changes to the lens’s visual semantics or interpretation.
2. No requirement to persist raw trades/prints for this feature (rollups are the v1 persistence surface).
3. No database requirement in v1.
4. No feedback from rollups into lens or dist-state computations.

## 2) Inputs (v1)

The rollup observer consumes the already-computed runtime tick inputs/outputs:

- the per-tick raw events processed by the engine loop step:
  - `events: Iterable[Event]` (see `src/flow_lens/models/event.py`)
- the per-tick lens “outcome” state:
  - `StateSnapshot` (see `src/flow_lens/engine/state_engine.py`)
- `now_ms: int` (canonical receive-time epoch ms; see FL-0064)
- `symbol: str`

It may additionally consume dist-state narrative snapshots for later correlation, but v1 rollups must be useful without
dist-state enabled.

Important: rollups explicitly separate:

1) **interval event aggregation** (what liquidity did; summed from raw effort events), and
2) **interval outcome integration** (what happened to structure; integrated from StateSnapshot over time).

## 3) Timebase and interval boundaries (deterministic)

### 3.1 Canonical time

Rollups use the canonical local receive-time epoch milliseconds (`now_ms`) that already drives the runtime loop.

### 3.2 15m boundary definition

Define:

- `ROLLUP_MS = 15 * 60 * 1000`
- `interval_id = floor(now_ms / ROLLUP_MS)`
- `interval_end_ms = (interval_id + 1) * ROLLUP_MS`
- `interval_start_ms = interval_end_ms - ROLLUP_MS`

This produces deterministic, wall-clock-aligned 15-minute intervals.

Alignment requirement (v1 initial):

- Any other 15m cadence features (e.g. dist-state narrative driver close) must align to the same interval boundaries
  (i.e. close timestamps that are multiples of `ROLLUP_MS`).

### 3.3 Update integration

On each update tick:

- compute `dt_ms = now_ms - last_now_ms` (if first tick in interval, initialize `last_now_ms = now_ms` and do not
  integrate),
- integrate the previous sample’s **outcome state** across `dt_ms` into the current open interval accumulators.
- assign each incoming **raw event** to an interval by its `event.timestamp`:
  - include iff `interval_start_ms <= event.timestamp < interval_end_ms`
  - if an event falls into an already-closed interval, drop it and add quality flag `LATE_EVENT_DROPPED`.

If `dt_ms <= 0`, ignore the tick for aggregation and add quality flag `NON_MONOTONIC_TIME`.

If a single `dt_ms` crosses one or more interval boundaries, split integration at boundaries and emit rollups for each
completed interval (with an explicit quality flag `DT_CROSSED_BOUNDARY`).

## 4) Rollup model (v1 initial; additive schema)

Schema evolution stance (v1):

- The JSONL record schema is intentionally **additive and evolvable** during iteration.
- Producers may add new keys/fields at any time.
- Consumers must ignore unknown keys and tolerate missing optional keys.
- Determinism requirements apply to time boundaries and aggregation math, not to field completeness.

### 4.1 Core rollup record

Each emitted rollup record (one per interval) contains (v1 initial):

- identity:
  - `symbol: str`
  - `interval_start_ms: int`
  - `interval_end_ms: int`
  - `duration_ms: int` (integrated; may be < 15m if runtime started mid-interval)
  - `sample_count: int` (number of update ticks observed in the interval)
- metadata (optional; recommended for DB migration readiness):
  - `schema_version: str` (example only; value not locked)
  - `build_id: str | None` (example: git sha; optional)
  - `config_id: str | None` (example: config hash; optional)
- `liquidity_interval` (event-summed; “what liquidity did”):
  - `effort_total: float`
  - `effort_dir_net: float` (buy minus sell, across spot+perp)
  - `effort_control_net: float` (spot minus perp, across buy+sell)
  - `effort_spot_buy: float`
  - `effort_spot_sell: float`
  - `effort_perp_buy: float`
  - `effort_perp_sell: float`
  - effort matrix (no caps; queryable):
    - `effort_matrix: list[{source_id: str, side_type: str, aggressor_side: str, effort: float}]`
  - `effort_by_source: list[{source_id: str, effort: float}]` (no caps; stable sort descending by effort then source_id)
  - `effort_by_source_side_aggr: list[{
        source_id: str,
        spot_buy: float, spot_sell: float,
        perp_buy: float, perp_sell: float
    }]` (no caps; stable sort by total effort desc then source_id)
  - concentration (derived; scalar, research-friendly):
    - `source_hhi: float`
    - `source_entropy: float`
    - `top_source_id: str | None`
    - `top_source_share: float`
- `price_poc` (event-summed volume-at-price; “where activity was concentrated”):
  - Goal: produce a research-ready **market POC** view (price-axis) from the same trade stream that drives the lens.
  - Weightings (full arrays, no caps):
    - **notional** weight: `w_notional = Event.effort_value` (quote notional; already computed by adapters)
    - **base** weight: `w_base = Event.effort_value / Event.price` (base quantity; derived; exact if
      `effort_value == price * base_qty`)
    - optional robustness upgrade (future): preserve `Event.base_qty` explicitly (see FL-0074) and cross-check
      `abs(base_qty - effort_value/price)` in diagnostics.
  - Segmentation (required; v1 initial):
    - total
    - by instrument: `spot`, `perp`
    - by side×aggressor:
      - `spot_buy`, `spot_sell`, `perp_buy`, `perp_sell`
    - (optional convenience; may be derived later): `total_buy`, `total_sell`
  - Representation uses **log-price buckets** so binning is stable across price levels and comparable across days.
  - Log-bucket definition (binding):
    - choose `bucket_pct` (example: `0.001` = 0.10% bins; configurable),
    - define `bucket_id(price) = floor( ln(price) / ln(1 + bucket_pct) )`,
    - histogram is stored as an array of values over a contiguous bucket-id span:
      - `start_bucket_id: int`
      - `values: list[float]` (length = `end_bucket_id - start_bucket_id + 1`)
  - Histogram object schema (used for each segment and each weighting):
    - `start_bucket_id: int`
    - `values: list[float]`
    - `poc_bucket_id: int`
    - `poc_price_mid: float` (deterministic mid-price for that bucket)
    - `poc_value: float`
  - Fields:
    - `bucket_pct: float`
    - `notional: dict[str, <Histogram>]` (keys are the segment ids listed above)
    - `base: dict[str, <Histogram>]` (same keys; derived from `effort_value/price`)
    - `quality_flags: list[str]` (bounded; e.g. `LOW_EVENT_COUNT`)

Headline convention (v1 initial):

- When a report or agent needs a single “POC” value, it should use `price_poc.notional["total"].poc_price_mid` unless it
  explicitly opts into base weighting.
- `outcome_interval` (time-integrated; “what happened to structure”):
  - time-weighted means (Σ(value * dt)/duration):
    - `mean_x: float`
    - `mean_y: float`
    - `mean_dominance: float`
    - `mean_e_spot_share: float`
    - `mean_total_effort_window: float` (StateSnapshot.total_effort; rolling-window level)
    - `mean_e_dir_window: float` (StateSnapshot.e_dir; rolling-window net)
    - `mean_halo: float`
    - `mean_gate: float`
    - `mean_max_source_share_window: float`
    - `mean_eff_raw: float`
    - `mean_disp: float`
    - `mean_disp_rate: float`
    - `mean_effort_rate: float`
  - price context (interval-level; derived from tick prices, not raw prints):
    - `price_open: float`
    - `price_close: float`
    - `log_return: float`
    - `range_high: float`
    - `range_low: float`
    - Definition (v1): use the per-tick `StateSnapshot.price_end` as the “tick price” for the interval aggregator:
      - `price_open` = first observed tick price in the interval
      - `price_close` = last observed tick price in the interval
      - `range_high/low` = max/min tick price observed in the interval
  - time-share occupancy (Σ(1{predicate}*dt)/duration):
    - `share_q_xpos_ypos: float`
    - `share_q_xpos_yneg: float`
    - `share_q_xneg_ypos: float`
    - `share_q_xneg_yneg: float`
    - `share_gate_low: float`
    - `share_spot_fresh: float`
    - `share_perp_fresh: float`
  - occupancy histograms (full arrays; no caps):
    - `x_hist`:
      - `bin_edges: list[float]` (length = `bin_count + 1`; fixed range `[-1, 1]`)
      - `dt_ms: list[int]` (length = `bin_count`; dt-weighted occupancy)
      - `poc_bin: int` (argmax of dt_ms)
    - `y_hist`:
      - `bin_edges: list[float]` (length = `bin_count + 1`; fixed range `[-1, 1]`)
      - `dt_ms: list[int]` (length = `bin_count`)
      - `poc_bin: int`
  - price-series / selector composition (no caps; stable ordering for queryability):
    - `price_series_used_share: list[{price_series_used: str, share_time: float}]`
    - `active_price_source_id_share: list[{source_id: str, share_time: float}]`
    - `selector_policy_share: list[{selector_policy: str, share_time: float}]`
  - baseline-relative control (see §4.3):
    - `mean_control_baseline_x: float`
    - `mean_midnight_tick_x: float | None`
    - `mean_x_rel_control_baseline: float` (mean(x - control_baseline_x))
    - `mean_x_rel_midnight: float | None` (mean(x - midnight_tick_x) when tick exists)
    - `mean_control_baseline_rel_midnight: float | None` (mean(control_baseline_x - midnight_tick_x) when tick exists)
    - `control_baseline_drift: float` (baseline_x_close - baseline_x_open)
    - `control_baseline_range: float` (max(baseline_x) - min(baseline_x))
  - persistence-line summary (time-weighted; “persistence line data”):
    - `mean_persist_raw: float`
    - `mean_persist_dir_raw: float`
    - `mean_persist_slope: float`
    - `share_persist_activity: float` (time `persist_activity_flag == true`)
    - `mean_persist_pivot_confirm_elapsed_s: float`
    - `mean_persist_pivot_cooldown_remaining_s: float`
  - acceptance/rejection events (interpretable state machine; v1):
    - Define `y_state` from `StateSnapshot.y` using:
      - a deadband `y_deadband` (example: `0.02`), and
      - a dwell time `y_dwell_ms` (example: `2000ms`) to avoid flicker.
    - State:
      - `ACCEPT` if `y > +y_deadband` held for `y_dwell_ms`
      - `REJECT` if `y < -y_deadband` held for `y_dwell_ms`
      - `NEUT` otherwise
    - Persist:
      - `accept_event_count: int` (transitions into ACCEPT)
      - `reject_event_count: int` (transitions into REJECT)
      - `accept_time_share: float` (time in ACCEPT)
      - `reject_time_share: float`
      - `neut_time_share: float`
      - `longest_accept_run_ms: int`
      - `longest_reject_run_ms: int`
    - Note: the histogram `y_hist` provides the “mirror of POC” view for accept/reject intensity distribution.
  - stability counters (sign changes; ignore zeros):
    - `x_sign_flip_count: int`
    - `y_sign_flip_count: int`
    - `e_dir_sign_flip_count: int`
- transition counters (event counts, not time-weighted):
  - `price_series_switch_count: int` (count of changes in `StateSnapshot.price_series_used`)
  - `persistence_confirm_flip_count: int` (count of changes in `StateSnapshot.persist_last_confirmed_dir_sign`)
- highlights (see §4.2)
- `quality_flags: list[str]` (bounded; see §6)

Notes:

- v1 prioritizes dense, research-friendly summaries. Additional fields may be added via decision as needs emerge.
- all shares and means are computed over the integrated `duration_ms`; if `duration_ms == 0`, emit no rollup.

### 4.2 Highlights (v1 “highest in category” carve-out)

V1 highlights exist to capture meaningful spikes without storing raw tick data.

For each interval, capture the single “highest in category” sample (by value) for:

- `max_total_effort`
- `max_halo`
- `max_abs_x`
- `max_abs_y`
- `max_abs_e_dir`
- `max_max_source_share`
- `min_gate`

Each highlight record includes:

- `ts_ms: int` (the tick time of the highlight)
- `value: float`
- `context` (small, fixed schema snapshot):
  - `x: float`, `y: float`, `dominance: float`, `e_spot_share: float`
  - `total_effort: float`, `e_dir: float`, `halo: float`, `gate: float`
  - `eff_raw: float`, `disp: float`
  - `max_source_share: float`, `top_source_id: str | None`
  - `spot_fresh: bool`, `perp_fresh: bool`
  - `price_series_used: str`
  - `persist_last_confirmed_dir_sign: int`
  - effort composition:
    - `spot_buy_effort: float`, `spot_sell_effort: float`
    - `perp_buy_effort: float`, `perp_sell_effort: float`

## 4.3 Baseline-relative control (v1)

The rollup must support “control relative to prior day”, not only relative to `x=0`.

Use the already-computed control baseline fields in `StateSnapshot`:

- `control_baseline_x`
- `control_baseline_midnight_tick_x` (may be `None`)

Compute and store:

- `mean_x_rel_control_baseline = mean(x - control_baseline_x)` (time-weighted)
- `mean_x_rel_midnight = mean(x - midnight_tick_x)` when `midnight_tick_x` exists, else `None`
- `mean_control_baseline_rel_midnight = mean(control_baseline_x - midnight_tick_x)` when `midnight_tick_x` exists,
  else `None`
- `control_baseline_drift = baseline_x_close - baseline_x_open`
- `control_baseline_range = max(baseline_x) - min(baseline_x)`

These fields allow 24h reporting phrasing like:

- “Spot dominated control by +Δ above baseline (vs prior day anchor).”

POC note (v1):

- The repository does not currently compute or expose an explicit “POC” aggregate for the lens.
- `control_baseline_x` is the closest existing control-anchor proxy and is used to support baseline-relative reporting.
- V1 rollups add an explicit `price_poc` accumulator derived from raw effort events (volume-at-price) to support POC
  research/reporting without changing the lens’s live semantics.

This produces a deterministic “what mattered most” set per interval and gives the future agent/report enough context to
describe the event.

## 5) Rolling 24h summary/report inputs (v1 initial)

### 5.1 Rolling window definition

The rolling 24h view uses the most recent **96** completed 15m rollups.

If fewer than 96 rollups exist, the 24h summary is computed over the available rollups and must surface the reduced
coverage in `coverage` fields.

### 5.2 Deterministic daily summary structure (v1)

The deterministic report should be generated from rollups as:

- **Highlights** section (top-N across the last 24h), using the same categories as §4.2:
  - top 3 intervals by `max_total_effort`
  - top 3 intervals by `max_halo`
  - top 3 intervals by `max_abs_x`
  - top 3 intervals by `max_abs_y`
  - top 3 intervals by `max_abs_e_dir`
  - top 3 intervals by `max_max_source_share`
  - top 3 intervals by `min_gate` (lowest gate)
- **Table** section (one row per 15m rollup for the last 24h) including:
  - interval end time
  - control/effect: `outcome_interval.mean_x`, `outcome_interval.mean_y`, `outcome_interval.mean_x_rel_control_baseline`,
    `outcome_interval.mean_x_rel_midnight`
  - price: `outcome_interval.log_return`, `outcome_interval.range_high`, `outcome_interval.range_low`
  - effort: `liquidity_interval.effort_total`, `liquidity_interval.effort_dir_net`, `liquidity_interval.effort_control_net`
  - composition: `liquidity_interval.effort_spot_buy`, `effort_spot_sell`, `effort_perp_buy`, `effort_perp_sell`
  - outcomes: quadrant shares + gate low share
  - concentration: `liquidity_interval.top_source_share`, `liquidity_interval.source_hhi`
  - `price_series_switch_count`, `persistence_confirm_flip_count`

The future agent layer may add free-form synthesis on top of this deterministic structure.

## 6) Quality flags (bounded; v1 initial)

The rollup observer must record quality flags (observability-only) to support correct interpretation:

- `NON_MONOTONIC_TIME` (dt_ms <= 0)
- `DT_CROSSED_BOUNDARY` (one dt crossed an interval boundary)
- `SPARSE_SAMPLING` (sample_count unusually low for the interval; threshold chosen in implementation)
- `LATE_EVENT_DROPPED` (an event timestamp fell into an already-closed interval)
- `LOW_EVENT_COUNT` (insufficient event count for a stable price POC profile; threshold chosen in implementation)

Note:

- `LOW_EVENT_COUNT` does not block rollup emission. It flags that the interval’s POC profile is likely noisy.

## 7) Persistence (v1)

Persist rollup records as append-only JSONL.

Recommended default path (v1):

- `logs/liquidity_rollup/liquidity_rollup-YYYYMMDD.jsonl`

Each line is one rollup record.

The persistence format is intentionally simple so the future agent/report pipeline can read it without infrastructure.

## 8) Configuration (v1)

Minimal configuration (v1 initial):

- `liquidity_rollup_enabled: bool` (default `false`)
- `liquidity_rollup_interval_minutes: int` (default `15`; v1 supports only `15`)
- `liquidity_rollup_out_dir: str` (default `logs/liquidity_rollup`)
- `liquidity_rollup_poc_bucket_pct: float` (example default `0.001`)
- `liquidity_rollup_xy_hist_bins: int` (example default `41`)
- `liquidity_rollup_y_deadband: float` (example default `0.02`)
- `liquidity_rollup_y_dwell_ms: int` (example default `2000`)
- `liquidity_rollup_low_event_count: int` (example default `200`)

## 9) Acceptance criteria (v1 initial)

1. Lens runtime outputs unchanged with rollups enabled/disabled.
2. Rollups are time-weighted (not point-sampled) and robust to high-frequency dot movement.
3. One rollup is emitted per completed 15m interval with deterministic boundary definition.
4. A rolling 24h summary can be produced from the last 96 rollups without accessing raw trades.
5. Rollup persistence is append-only JSONL and includes bounded quality flags for interpretation.
