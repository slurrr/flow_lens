# Flow Lens Tuning Targets (Temporary)

## Current Baseline (2026-02-01)

Target: **BTC + SOL high‑end responsiveness without frequent saturation.**

Baseline config:
- `tanh_k = 0.16`
- `tbt_window_multiplier = 4.0`
- `scale_window_seconds = 420.0`
- `disp_scale_multiplier = 0.08`
- `disp_scale_percentile = 0.45`
- `disp_scale_min_samples = 20`
- `effort_scale_percentile = 0.8`
- `effort_scale_min_samples = 20`
- `effort_floor_multiplier = 0.3`
- `effort_floor_ticks = 60`

Observed (BTC/SOL, scenario summaries):
- BTC `Y_raw` saturation stays **≤ 0.03**
- SOL `Y_raw` saturation stays **≤ 0.01**

If you need a slightly safer margin with similar feel, reduce `tanh_k` to **0.14**.

---

## Calibration

Use these **during clean trend legs** (not chop). Status bar is temporary for stabilization.

### k Calibration

Set `k` from data using a target `|Y_raw|` percentile:

- `k = atanh(target) / p95(|eff_rel|)`

Recommended:
- `target = 0.7` (acceptable 0.6–0.8)

Notes:
- `atanh(x) = 0.5 * ln((1+x)/(1-x))`
- Use **p95** of `|eff_rel|` over a clean trend segment.

### Live Metrics Targets

- `p95|Y_raw|`: **0.6–0.8**
- `p99|Y_raw|`: **< 0.9**
- `flip_rate_Y_raw`: **3–8 / min**
- `flip_rate_Y` (smoothed): **1–4 / min**
- `deadband_active_rate`: **0.25–0.55**
- `|disp_rate| / disp_scale` (current or median): **0.8–2.0**
- `E_dir_sign_persistence` (consecutive updates): **3–10**
- `price_series_switch_rate`: **< 1 / min** majors, **< 3 / min** smalls
- `air_pocket_active_rate`: **< 0.2** during active markets

## When To Act (Rules of Thumb)

- If `p95|Y_raw| < 0.5` **and** `p99|Y_raw| < 0.7` during a clean trend:
  - Increase `k` by **+10–20%**.
- If `p95|Y_raw| > 0.85` **or** `p99|Y_raw| > 0.95`:
  - Decrease `k` by **−10–20%**.

- If `flip_rate_Y_raw > 10 / min` **and** `deadband_active_rate < 0.25`:
  - Increase deadband threshold `m` slightly (e.g., +0.05 to +0.1).
- If `deadband_active_rate > 0.6` for 10+ minutes **and** `p95|Y_raw| < 0.5`:
  - Decrease deadband `m` or speed up `disp_scale` update.

- If `flip_rate_Y` (smoothed) > 4 / min during a trend:
  - Add light direction hysteresis on `E_dir` sign (require persistence for N updates).

- If `price_series_switch_rate` is high on majors (> 1/min):
  - Increase stickiness / preference for the dominant price series.
- If `price_series_switch_rate` is high on smalls (> 3/min):
  - Prefer spot price, or add a minimum‑duration latch.

- If `air_pocket_active_rate > 0.2` during active markets:
  - Revisit effort floor or scale normalization (may be suppressing Y too much).

---

## Persistence Quick Cheat Sheet (`S`, `dS/s`)

- `Y_raw` = instant push, `Y_s` = smoothed dot state, `S` = persisted acceptance state, `dS/s` = persistence acceleration.
- If dot reacts but `S` barely moves in trends: lower `persist_tau_build_s` (e.g. `90 -> 60`).
- If `S` feels too sticky after regime flips: lower `persist_tau_decay_s` (e.g. `20 -> 12`).
- If `S` is noisy / jittery in chop: raise `persist_tau_build_s` and `persist_tau_decay_s` together (+20–40%).
- Healthy trend feel: `Y_raw` pulses, `Y_s` holds direction, `S` drifts steadily, `dS/s` mostly same sign as `Y_raw`.
- Healthy chop feel: `Y_raw` flips often, `Y_s` near zero, `S` decays toward zero, `dS/s` oscillates around zero.
