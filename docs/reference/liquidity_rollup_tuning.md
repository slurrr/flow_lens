# Liquidity Rollup Tuning (v1)

This document covers how to tune the v1 liquidity rollup parameters:

- `liquidity_rollup_poc_bucket_pct = 0.001`
- `liquidity_rollup_xy_hist_bins = 41`
- `liquidity_rollup_y_deadband = 0.02`
- `liquidity_rollup_y_dwell_ms = 2000`
- `liquidity_rollup_low_event_count = 200`

Primary spec reference: `SPEC-liquidity-rollup-layer-v1.md`.

## Ground rules

- Tune from **evidence in rollup JSONL**, not from intuition.
- Prefer changes that improve **truthfulness + interpretability** without turning the rollup into a “cute summary”.
- Use `quality_flags` rates as a guardrail (don’t ignore them; don’t let them become meaningless).

Important: “p50/p90” (or any quantiles) are **analysis methods**, not additional runtime knobs and not something the
rollup needs to persist. The rollups already log the raw counts/arrays you need; quantiles are computed offline from
the JSONL.

## Where to look

Rollups are persisted as JSONL (default): `logs/liquidity_rollup/liquidity_rollup-YYYYMMDD.jsonl`.

Key fields you’ll commonly use when tuning:

- `liquidity_state.sample_count` (sampling cadence health; drives `SPARSE_SAMPLING`)
- `liquidity_state.liquidity_interval.price_poc.*` (POC profile)
- `liquidity_state.outcome_interval.x_hist` / `y_hist` (occupancy histograms)
- `liquidity_state.outcome_interval.accept_event_count` / `reject_event_count` and time shares
- `liquidity_state.quality_flags` (especially `LOW_EVENT_COUNT`, `SPARSE_SAMPLING`, `INVALID_EVENT_PRICE`)

## Parameter-by-parameter tuning

### 1) `liquidity_rollup_poc_bucket_pct`

What it does:

- Controls log-price bucket size for `price_poc` (event-summed volume-at-price).
- Bucket id is computed as:
  - `bucket_id(price) = floor( ln(price) / ln(1 + bucket_pct) )`
- Smaller `bucket_pct` ⇒ finer buckets (more resolution, more buckets, more noise, bigger records).
- Larger `bucket_pct` ⇒ coarser buckets (less noise, less detail; can hide meaningful POC drift).

How to tell it’s too small:

- `price_poc.*.values` arrays become long (many buckets per 15m).
- POC price (`poc_price_mid`) “jitters” between adjacent buckets during visually calm periods.
- File sizes grow quickly and/or per-interval memory footprint grows unexpectedly.

How to tell it’s too large:

- POC drift is “stair-steppy” (big discrete jumps) even when price drifts smoothly.
- POC becomes too insensitive: multiple distinct auction zones collapse into one.

Practical tuning loop:

1. Compute, per interval:
   - `bucket_count = len(price_poc.notional["total"].values)`
   - `poc_jump_pct = abs(poc_price_mid_t - poc_price_mid_{t-1}) / poc_price_mid_{t-1}`
2. If bucket counts are consistently high and POC is noisy, increase `bucket_pct` (e.g. `0.001 → 0.0015`).
3. If bucket counts are low and POC feels smeared, decrease `bucket_pct` (e.g. `0.001 → 0.00075`).

Notes:

- `bucket_pct = 0.001` is “0.10%” buckets and is a reasonable BTC baseline.
- `LOW_EVENT_COUNT` (driven by `poc_event_count`) becomes more important as you shrink `bucket_pct`.

---

### 2) `liquidity_rollup_xy_hist_bins`

What it does:

- Sets the number of fixed-range occupancy bins for `x_hist` and `y_hist`.
- Histograms are dt-weighted occupancy over `[-1, 1]`.

Why `41` is a good default:

- It’s odd (a clean visual center bin around 0).
- Bin width is `2 / 41 ≈ 0.0488` — enough resolution to see structure without turning the array into noise.

How to tell it’s too low (too coarse):

- `x_hist` / `y_hist` are overly blocky; large shares pile into a few bins.
- `poc_bin` becomes less informative because bins cover too much range.

How to tell it’s too high (too fine):

- Many bins are near-zero most intervals.
- `poc_bin` hops frequently by ±1 even when the underlying state is stable.
- You’re paying record-size cost without added interpretability.

Practical tuning loop:

- If you see “empty histogram” behavior (almost all dt in 1–2 bins), increase bins (e.g. `41 → 61`).
- If you see “needle histogram” behavior (lots of near-zero bins, jumpy POC_bin), decrease bins (e.g. `41 → 31`).

---

### 3) `liquidity_rollup_y_deadband`

What it does:

- Drives the acceptance/rejection state machine target:
  - `ACCEPT` if `y > +deadband`
  - `REJECT` if `y < -deadband`
  - `NEUT` otherwise
- It is explicitly about preventing “accept/reject” labeling when `y` is just noise around 0.

How to tell it’s too low:

- `accept_event_count + reject_event_count` is high in calm markets (flicker).
- `accept_time_share` / `reject_time_share` are large even when `mean_y` is near 0.
- `y_sign_flip_count` is high and accept/reject events are dominated by short runs.

How to tell it’s too high:

- You almost never see `ACCEPT`/`REJECT` time share even in clearly directional periods.
- `y_hist` shows mass away from 0 but the state machine stays mostly `NEUT`.

Practical tuning loop:

- Start at `0.02` and tune to keep accept/reject interpretable:
  - raise to reduce noise (`0.02 → 0.03`)
  - lower to capture subtle acceptance/rejection (`0.02 → 0.015`)

---

### 4) `liquidity_rollup_y_dwell_ms`

What it does:

- Adds a dwell requirement before switching the acceptance/rejection state:
  - the candidate target must persist for `y_dwell_ms` before the switch occurs.
- This is the “flicker suppressor”.

How to tell it’s too low:

- Many ACCEPT↔REJECT transitions with very short `longest_*_run_ms`.
- Accept/reject event counts are high relative to interval length.

How to tell it’s too high:

- `accept_event_count` / `reject_event_count` are suspiciously low in periods where `y` clearly spends time above/below
  `±deadband`.
- `accept_time_share` / `reject_time_share` under-represent what you see in `y_hist`.

Practical tuning loop:

- Use the longest-run fields as the simplest check:
  - If `longest_accept_run_ms`/`longest_reject_run_ms` are usually barely above 0, dwell is too high (or deadband too high).
  - If they’re frequently tiny (hundreds of ms) and event counts are huge, dwell is too low.

---

### 5) `liquidity_rollup_low_event_count`

What it does:

- Drives two quality flags:
  - `SPARSE_SAMPLING` if `sample_count < low_event_count`
  - `LOW_EVENT_COUNT` if `poc_event_count < low_event_count`
- It does **not** block emission; it marks “this interval’s profile is likely noisy”.

How to tune it (principle):

- Set it so the flag is meaningful:
  - not “always on” (becomes noise),
  - not “never on” (doesn’t protect you from garbage intervals).

Practical tuning loop:

1. Collect a day of rollups and compute distributions of:
   - `sample_count`
   - `poc_event_count`
2. Choose `low_event_count` around a “bad tail” threshold (commonly ~p1–p5), then validate visually:
   - when `LOW_EVENT_COUNT` fires, does POC look unreliable / sparse?
   - when it doesn’t fire, does POC look stable enough to reason about?

Note:

- If you tighten `poc_bucket_pct` (smaller bins), you generally want `LOW_EVENT_COUNT` to become stricter (higher) or you
  accept noisier POC.

## Quantile checks (optional, recommended)

These are quick “is this sane?” checks you can run over a day/week of JSONL. They require **no new runtime config**.

- For `liquidity_rollup_low_event_count`:
  - Compute p50/p90 of `liquidity_state.sample_count` and `liquidity_state.liquidity_interval.poc_event_count`.
  - Target: `LOW_EVENT_COUNT` and `SPARSE_SAMPLING` should be rare-but-real (roughly a low single-digit % rate).
- For `liquidity_rollup_poc_bucket_pct`:
  - Compute p50/p90 of `len(price_poc.notional.total.values)` (bucket span per interval).
  - If p90 is “too big”, you’re paying record-size cost and likely adding noise → increase `bucket_pct`.
- For `liquidity_rollup_y_deadband` / `liquidity_rollup_y_dwell_ms`:
  - Compute p50/p90 of `accept_event_count + reject_event_count` and `longest_accept_run_ms/longest_reject_run_ms`.
  - If event counts are huge and longest runs are tiny → too sensitive (raise deadband and/or dwell).
  - If event counts are ~0 even on volatile days → too insensitive (lower deadband and/or dwell).

## Quick “what should I change?” cheat sheet

- POC too noisy / jittery ⇒ increase `poc_bucket_pct` OR increase `low_event_count`.
- POC too smeared / uninformative ⇒ decrease `poc_bucket_pct` (and watch bucket counts / file size).
- Accept/reject flicker ⇒ increase `y_deadband` and/or `y_dwell_ms`.
- Accept/reject never triggers ⇒ decrease `y_deadband` and/or `y_dwell_ms`.
- x/y hist feels too blocky ⇒ increase `xy_hist_bins`.
- x/y hist feels too spiky / POC_bin jumpy ⇒ decrease `xy_hist_bins`.
