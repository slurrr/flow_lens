# Flow Lens Tuning Targets (Temporary)

Use these **during clean trend legs** (not chop). Status bar is temporary for stabilization.

## k Calibration

Set `k` from data using a target `|Y_raw|` percentile:

- `k = atanh(target) / p95(|eff_rel|)`

Recommended:
- `target = 0.7` (acceptable 0.6–0.8)

Notes:
- `atanh(x) = 0.5 * ln((1+x)/(1-x))`
- Use **p95** of `|eff_rel|` over a clean trend segment.

## Live Metrics Targets

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
