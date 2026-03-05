---
title: "SPEC — Distribution State Layer (Phase 1: Binance Perp, BTC, Ribbons)"
created: 2026-03-04
status: "draft"
related:
  - "docs/reference/dist_state_layer_overview.md"
  - "docs/reference/dist_state_layer_v1_contract.md"
  - "docs/reference/dist_state_layer_binance_inputs.md"
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
---

# SPEC — Distribution State Layer (Phase 1)

Phase 1 adds a **distribution-state panel** to the TUI, computed from **Binance USDT-perp (BTCUSDT)** candles and OI.

This layer is a **separate instrument** displayed alongside the lens. It must not modify the lens channels or the lens engine.

## 0) Goals

1. Add a stable, bounded, multi-timeframe **distribution geometry** readout for `BTCUSDT`:
   - `3m`, `15m`, `1h`, `4h`
2. Make `P` (positioning pressure from OI) **first-class** and **perp-coherent** with the price series used by the layer.
3. Keep overhead negligible:
   - one WS connection (multiplexed streams),
   - OI REST sampled once per `3m` bar close (~20 calls/hour),
   - no per-trade aggregation.
4. Prepare Phase 2+ without refactors:
   - stable data models,
   - row-level readiness/missingness,
   - token/narrative fields reserved but unpopulated in Phase 1.

## 1) Non-Goals (Phase 1)

1. Do not change Flow Lens semantics:
   - no changes to `X`, `Y`, dot size, halo, lean, persistence line.
2. No runtime selector/failover across venues/sources (single-source only).
3. No per-row tokens and no cross-timeframe narrative (ribbons-only in Phase 1).
4. No multi-symbol support (this phase targets BTC only).
5. No alerts, scores, or decision logic.

## 2) Architectural constraints (binding)

See `docs/reference/dist_state_layer_v1_contract.md`.

Phase 1 contracts:

- rows are **perp-coherent**: price/OI from the same perp family.
- v1 is **single-source**: Binance USDT-margined futures.
- `P` has explicit **availability** and **bounded normalization**.
- UI labeling must make it obvious the dist panel may use a different price source than the lens.

## 3) Inputs and chosen source (Phase 1)

### 3.1 Candle source (live)

Use Binance futures kline streams for `BTCUSDT`:

- `btcusdt@kline_3m`
- `btcusdt@kline_15m`
- `btcusdt@kline_1h`
- `btcusdt@kline_4h`

Process only bar closes: `k.x == true`.

### 3.2 Candle source (warmup)

Warmup via REST klines:

`GET /fapi/v1/klines?symbol=BTCUSDT&interval=<tf>&limit=<N>`

### 3.3 Open interest source (live)

OI snapshot via REST:

`GET /fapi/v1/openInterest?symbol=BTCUSDT`

Sample cadence:

- once per `3m` bar close event, immediately after the close is observed.

Alignment rule (Phase 1):

- OI is joined to a close by the deterministic bucket rule in §6.2 (keyed by `kline_close_ms`), with a tolerance gate
  against the response `time` field (`oi_join_tolerance_ms`).
- if the OI request fails or fails the tolerance gate, `P` is unavailable for that close bucket.

### 3.4 Open interest warmup

Warmup OI history via REST:

`GET /futures/data/openInterestHist?symbol=BTCUSDT&period=<period>&limit=<N>`

We have confirmed data for:

- `5m`, `15m`, `1h`, `4h`

and that `3m` history via this endpoint is empty at time of probing.

Unit contract (binding):

- live OI uses `/fapi/v1/openInterest.openInterest` (contract units),
- warmup OI uses `openInterestHist.sumOpenInterest` (same contract units),
- do **not** use `sumOpenInterestValue` for `P` in Phase 1.

Warmup plan:

- for `15m/1h/4h`: warmup directly from matching-period history.
- for `3m`: seed normalization from `5m` history (Option A described in §6.3).

## 4) Timebase and determinism

The dist-state layer maintains its own event timeline, but follows the repo’s hygiene philosophy:

- capture `ts_recv_ms` locally upon receipt (or immediately before/after REST response),
- carry venue timestamps as metadata,
- update state deterministically based on ordered input events.

Dist-state must not call wall-clock functions inside the engine update logic other than for capturing `ts_recv_ms` at ingest.

## 5) Data model (Phase 1)

### 5.1 Input events (dist-state only)

Define distinct models from `flow_lens.models.event.Event` (do not reuse the trade `Event`).

Required input shapes:

**DistKlineCloseEvent**

- `ts_recv_ms: int`
- `symbol: str` (e.g. `"BTC"`, base symbol)
- `source_id: str` (e.g. `"binance_perp"`)
- `tf: Literal["3m","15m","1h","4h"]`
- `kline_open_ms: int`
- `kline_close_ms: int`
- `open: float`
- `high: float`
- `low: float`
- `close: float`

**DistOiSnapshotEvent**

- `ts_recv_ms: int`
- `symbol: str`
- `source_id: str`
- `oi: float` (contract units)
- `venue_time_ms: int | None` (from Binance response `time`)

### 5.2 Output snapshot

Per timeframe row:

**DistRowSnapshot**

- `tf: str`
- `ready_core: bool` (warmup satisfied for core metrics; see §5.3)
- `ready_p: bool` (OI/P normalization initialized; missingness rules still apply; see §5.3)
- `last_close_ms: int | None`
- `metrics: DistRowMetrics` (bounded floats; see §7)
- `bins: DistRowBins` (coarse levels 0..K for rendering)
- reserved fields (Phase 1: always `None`):
  - `token: str | None`
  - `token_strength: str | None`
  - `narrative_hint: str | None`

Panel-level:

**DistPanelSnapshot**

- `symbol: str` (base symbol; v1 `"BTC"`)
- `source_id: str` (v1 `"binance_perp"`)
- `rows: dict[tf, DistRowSnapshot]`
- `last_oi_ts_recv_ms: int | None`
- `last_oi_value: float | None`

### 5.3 Readiness contract (redline)

Readiness is deterministic and row-local. It does not depend on wall-clock timing.

Define per-row counters/state:

- `bars_seen`: count of processed bar closes for the row (after idempotency filtering)
- `oi_deltas_seen`: count of processed `ΔOI` updates for the row
- `oi_var_initialized`: whether the row’s `ΔOI` variance state is initialized (via warmup seed or live accumulation)

Phase 1 readiness gates (locked):

- `ready_core == True` iff:
  - `bars_seen >= ready_core_min_bars`, and
  - the V-scale deque has at least `v_scale_min_samples` samples (so `sigma_scale` is percentile-based, not median-only), and
  - the long ATR state has seen at least `ready_core_min_bars` updates (same bar closes; no separate counter).
- `ready_p == True` iff:
  - `oi_var_initialized == True`, and
  - `oi_deltas_seen >= ready_p_min_deltas`.

Notes:

- A row can be `ready_core` while `P` is still unavailable for a specific bucket (OI missing/tolerance failure); readiness
  only means the normalization state is initialized and eligible.

## 6) Engine update rules (Phase 1)

Maintain one row-engine per timeframe.

### 6.0 Idempotency + out-of-order handling (redline)

Bar-close processing must be deterministic across reconnects and duplicate deliveries.

For each `(source_id, tf)` maintain:

- `last_processed_close_ms: int | None`
- `processed_close_keys: set[(source_id, tf, kline_close_ms)]` (bounded; evict oldest by `kline_close_ms` if needed)

Rules:

1. Key each close event by `(source_id, tf, kline_close_ms)`.
2. **Process once**: if key is already present, ignore repeats.
3. **Reject older-than-last**: if `last_processed_close_ms` is set and `kline_close_ms < last_processed_close_ms`, ignore.
4. Otherwise process, then set:
   - `last_processed_close_ms = kline_close_ms`
   - add key to `processed_close_keys`

Warmup boundary:

- After warmup, `last_processed_close_ms` for each row is set to the final warmup close time so late duplicates cannot
  perturb live state.

### 6.1 Bar close update

On `DistKlineCloseEvent` for timeframe `tf`:

1. compute return `r_t = ln(close / prev_close)` if prev_close exists else `0`.
2. update volatility estimator (EWMA variance of `r_t`).
3. update ATR estimators from true range:
   - `tr_t = max(high-low, abs(high-prev_close), abs(low-prev_close))`
4. update autocorrelation/persistence estimator from sign agreement between `r_t` and `r_{t-1}`.
5. update stretch estimator (see §7.2).
6. if an OI sample has been captured for this close tick (see §6.2), update `P` for this row using `ΔOI`.
7. compute bounded metrics and bins for rendering.

### 6.2 OI sampling on 3m close

We join OI deterministically by close bucket id (redline).

Definitions:

- close bucket id: `bucket_id = (source_id, kline_close_ms)`

Rules:

1. On each processed `3m` close (after §6.0 idempotency checks), perform one REST request:
   - `/fapi/v1/openInterest?symbol=BTCUSDT`
2. Build a `DistOiSnapshotEvent`:
   - `oi` parsed from response `openInterest` (contract units),
   - `venue_time_ms` from response `time` (ms) when present.
3. Missing venue-time policy (redline; strict for Phase 1):
   - if `venue_time_ms` is missing/None, discard the sample for this bucket and treat `P` as unavailable for the bucket.
4. Tolerance gate (redline):
   - if `abs(venue_time_ms - kline_close_ms) > oi_join_tolerance_ms`, discard the sample for this bucket.
5. Bucket lifecycle + immutability (redline):
   - store accepted samples in a read-only, multi-consumer bucket map keyed by `bucket_id`.
   - first write wins: if a bucket key already exists, ignore subsequent writes (do not mutate).
   - eviction is deterministic and keyed off kline close time:
     - retain a bucket until `kline_close_ms + oi_join_tolerance_ms`
     - evict when processing any later close where `current_close_ms > bucket_close_ms + oi_join_tolerance_ms`
6. Consume semantics (redline):
   - any row close event may read the bucket; never pop on first consume.
   - if a row close has no bucket entry for its `bucket_id`, `P` is unavailable for that row close (no retries, no
     carry-forward, no imputation).

### 6.3 3m `P` warmup (Option A: variance seeding)

Because `openInterestHist(period=3m)` is not available:

1. Fetch `openInterestHist(period=5m)` for `oi_seed_points` points.
2. Use `sumOpenInterest` (contract units) and compute `ΔOI_5m` between consecutive **completed** 5m buckets.
3. Compute EWMA variance over `ΔOI_5m` using the locked half-life mapping:
   - configured: `hl_oi_bars_3m`
   - derived: `hl_oi_bars_5m = hl_oi_bars_3m * (3.0 / 5.0)`
   - `λ_5m = exp(-ln(2) / hl_oi_bars_5m)`
   - update: `var_oi_5m = λ_5m * var_oi_5m + (1-λ_5m) * ΔOI_5m^2`
4. Locked conversion factor (redline):
   - `var_oi_3m_seed = var_oi_5m_last * (3.0 / 5.0)`
5. Initialize the 3m row’s `var_oi` state to `var_oi_3m_seed` and set:
   - `oi_var_initialized = True` only if at least `oi_seed_min_points` completed 5m deltas were processed.
6. Thereafter, compute true `ΔOI_3m` live from OI snapshots sampled at each 3m close.

This seeding affects only early behavior; after enough live 3m closes, the live `ΔOI_3m` series dominates.

## 7) Metric definitions (bounded; Phase 1)

All outputs used by the TUI must be **dimensionless and bounded** before binning.

Phase 1 exposes five metrics per row:

- `V` (volatility state)
- `S` (stretch)
- `A` (autocorrelation / persistence bias)
- `P` (positioning pressure; may be unavailable during warmup/failures)
- `T` (transition pressure)

### 7.1 V — volatility state

Compute EWMA variance of returns:

- `var_r[t] = λ * var_r[t-1] + (1-λ) * r_t^2`
- `sigma_r = sqrt(var_r)`

Normalize to a bounded value:

Phase 1 locks V to `[0,1]` (redline: no symmetric mapping):

- maintain a bounded deque of the last `v_scale_window_bars` positive `sigma_r` samples,
- define `sigma_scale = percentile(deque, v_scale_percentile)` once `>= v_scale_min_samples`, else `median(deque)`,
- define `v_norm = sigma_r / (sigma_scale + ε)`,
- bound: `V = v_norm / (1 + v_norm)` in `[0,1)`.

### 7.2 S — stretch

Stretch measures extension of log price relative to its recent distribution:

- `x_t = ln(close)`
- EWMA mean: `mu_x[t] = λ * mu_x[t-1] + (1-λ) * x_t`
- innovation: `dx_t = x_t - mu_x[t-1]`
- EWMA variance of innovation: `var_dx[t] = λ * var_dx[t-1] + (1-λ) * dx_t^2`
- `sigma_x = sqrt(var_dx)`
- raw stretch: `s_raw = (x_t - mu_x[t]) / (sigma_x + ε)`
- bound: `S = tanh(k_s * s_raw)` in `[-1,1]`

### 7.3 A — autocorrelation / persistence bias

Phase 1 uses a stable sign-agreement estimator:

- `same = 1` if `sign(r_t) == sign(r_{t-1})` and both non-zero else `0`
- EWMA of `same`: `p_same[t] = λ * p_same[t-1] + (1-λ) * same`
- map to `[-1,1]`: `A = clamp(2 * (p_same - 0.5), -1, 1)`

### 7.4 P — positioning pressure (OI)

At bar close:

- observe `OI_t` (snapshot),
- compute `ΔOI = OI_t - OI_{t-1}` (if `OI_{t-1}` exists; else unavailable)
- update EWMA variance of `ΔOI`:
  - `var_oi[t] = λ * var_oi[t-1] + (1-λ) * ΔOI^2`
  - `sigma_oi = sqrt(var_oi)`
- normalize and align by return direction:
  - `p_raw = (ΔOI / (sigma_oi + ε)) * sign(r_t)`
- bound:
  - `P = tanh(k_p * p_raw)` in `[-1,1]`

Availability rules:

- if OI snapshot missing or `sigma_oi` uninitialized, mark `P` unavailable for that row close.

### 7.5 T — transition pressure (ATR short/long ratio)

Maintain two ATR EWMAs (short and long), both updated from true range:

- `atr_s[t] = λ_s * atr_s[t-1] + (1-λ_s) * tr_t`
- `atr_l[t] = λ_l * atr_l[t-1] + (1-λ_l) * tr_t`
- ratio: `t_raw = atr_s / (atr_l + ε)`

Normalize to a bounded value:

- `T = tanh(k_t * ln(t_raw))` in `[-1,1]`

## 8) Binning and rendering contract (Phase 1)

Renderer does not consume raw metrics directly. It consumes **bins**.

Per metric, define:

- symmetric metrics (`S`, `A`, `P`, `T`): bins from `[-1,1]` into `K` levels (e.g., 7 or 9).
- non-symmetric metric (`V`): use `[0,1]` bins (no symmetric mapping in Phase 1).

Add hysteresis per metric bin if flicker is observed (same philosophy as lens).

Missingness:

- if `P` unavailable, render a stable placeholder glyph (no flicker).
- if a row is not `ready_core`, dim the row (or show a warmup marker).
- if any metric is unavailable for a row close, render it as unavailable (do not impute/carry-forward previous values).

## 9) TUI panel (Phase 1)

Add a new panel below the lens:

- Header includes: `DIST BTC (binance_perp)` and last-update recency.
- Rows: one per timeframe: `3m`, `15m`, `1h`, `4h`
- Columns: `V S A P T` rendered as binned glyphs.
- No tokens and no narrative in Phase 1.

Layout fallback:

If terminal height is constrained, drop rows in this order (redline; preserve intraday continuity):

1. drop `4h` first,
2. then `1h`,
3. then `3m` (keep `15m` as the last remaining row).

## 10) Configuration (Phase 1)

Add a new config block:

`runtime.dist_state`

Minimum required keys:

- `enabled: bool` (default false until intentionally enabled)
- `symbol: str` (default `BTC`)
- `source_id: str` (default `binance_perp`)
- `timeframes: list[str]` (default `["3m","15m","1h","4h"]`)
- `warmup_kline_bars: int` (e.g. 200)
- `warmup_oi_hist_points: int` (e.g. 200)
- readiness gates (locked in §5.3):
  - `ready_core_min_bars: int` (e.g. 30)
  - `ready_p_min_deltas: int` (e.g. 10)
- OI join + seeding (redlines):
  - `oi_join_tolerance_ms: int` (e.g. 15000)
  - `oi_seed_points: int` (default = `warmup_oi_hist_points`)
  - `oi_seed_min_points: int` (e.g. 30)
- V scaling (locked in §7.1):
  - `v_scale_window_bars: int` (e.g. 200)
  - `v_scale_percentile: float` (e.g. 0.80)
  - `v_scale_min_samples: int` (e.g. 30)
- EWMA half-lives (per timeframe or expressed in bars):
  - `hl_vol_bars`, `hl_stretch_bars`, `hl_oi_bars`, `hl_atr_short_bars`, `hl_atr_long_bars`, `hl_a_bars`
- bounding gains:
  - `k_s`, `k_p`, `k_t`

## 11) Acceptance criteria (Phase 1)

1. Lens outputs are unchanged with dist-state enabled vs disabled (no coupling).
2. Dist-state panel:
   - updates only on bar closes,
   - shows stable glyphs (bounded, no NaNs),
   - labels its source (`binance_perp`) clearly.
3. OI overhead:
   - at most one `openInterest` REST call per 3m bar close (~20/hour).
4. Warmup:
   - `15m/1h/4h` `P` is eligible to become available immediately after warmup (normal missingness rules still apply),
   - `3m` `P` is eligible to become available quickly (variance seeded from 5m history, then live deltas; missingness rules
     still apply).
