# Flow Lens — Diagnostic Logging Plan

## Goal

Capture the minimum set of **raw + derived per-tick state inputs** needed to explain (with numbers) why the lens is showing:

- `Y` not moving (effectiveness stuck near 0)
- `X` drifting/perma-leaning perp (control skew)
- halo not growing (dispersion not increasing)

This plan is designed so you can tune **once**: after you collect logs, you can replay/compute “what if we changed k / gating / normalization” offline.

---

## When to log (frequency)

Log **once per engine compute** (per symbol).

- Nominally: every `Δ` seconds (default 2.0s) per symbol.
- If conditional stepping/window overrides are enabled: log only on ticks where the engine actually runs for that symbol.

Recommended capture duration:

- 20–60 minutes for 3–5 high-liquidity symbols (e.g. BTC, ETH, SOL) and 1–2 thinner symbols.

---

## Log format

Use structured **JSONL** (one JSON object per line) so it can be aggregated later.

Suggested file:

- `logs/flow_lens_diagnostics.jsonl`

---

## What to log (per symbol, per compute)

### A) Timing / window context

- `ts_wall_ms` (local wallclock at compute)
- `now_ms` (the `now_timestamp` passed into the loop/engine)
- `symbol` (base symbol)
- `window_ms` (effective window used; include override value if applicable)
- `buffer_event_count` (total events in the active window snapshot)

Answers:

- “Are we actually stepping at the cadence we think?”
- “Are window overrides changing semantics per symbol?”

---

### B) Feed freshness / price-series selection

Log the facts needed to detect stale or mismatched price:

- `price_series_used` ∈ `{spot, perp, spot_fallback, perp_fallback}`
- `spot_fresh` (bool) and `perp_fresh` (bool) for the window
- `last_spot_event_ts` and `last_perp_event_ts` (ms) (last known, not necessarily in-window)
- `spot_event_count_window`, `perp_event_count_window` (counts in the window)

Answers:

- “Is spot actually inactive/stale when X drifts perp?”
- “Is price derived from perp when spot is available (or vice versa)?”
- “Is the chosen price series updating frequently enough to support meaningful `disp`?”

---

### C) Price displacement (raw)

- `price_start`
- `price_end`
- `log_return = log(price_end / price_start)` (or `0` when invalid)
- `delta_price = price_end - price_start` (optional; helps intuition)

Answers:

- “Is Y flat because `disp` is tiny (price barely moves over Δ)?”
- “Are there air-pocket-like jumps (large `log_return` with low effort)?”

---

### D) Effort totals and dominance (raw)

From the active window aggregation:

- `E_spot`
- `E_perp`
- `E_total`
- `D = E_spot - E_perp`
- `E_spot_share = E_spot / (E_total + ε)` (or log this ratio directly)

Answers:

- “Is perp dominance real (`E_perp` structurally bigger), or a staleness artifact?”
- “Does `E_spot / E_total` look ‘reasonable’ for the symbols you care about?”

---

### E) X and size (derived)

- `X_raw`
- `X` (smoothed)
- `size_raw`
- `size_bin`

Answers:

- “Is X saturating because `E_total` is near zero (ratio spikes)?”
- “Does size behave independently of X (force vs control separation)?”

---

### F) Effectiveness chain (derived)

Log the whole chain so you can isolate which stage collapses Y:

- `disp = sign(D) * log_return` (the directional displacement actually used)
- `effort_floor` (computed floor value)
- `gate` (effort-floor gate value)
- `effort_median` (median of recent effort history used as baseline)
- `effort_norm` (if used; e.g. `E_total / effort_median`)
- `eff_raw` (the exact value before `tanh`)
- `Y_raw`
- `Y_gated`
- `Y` (smoothed)

Answers:

- “Is `eff_raw` always near 0?” → scaling problem (adjust displacement scaling or `tanh_k`)
- “Is `gate` usually small?” → air-pocket guardrail is suppressing Y (either correct or mis-calibrated)
- “Is `effort_norm` inflating/deflating effectiveness?” → normalization choice may be masking dynamics

---

### G) Halo / dispersion (raw + derived)

At minimum:

- `halo_raw`
- `halo` (post asymmetry dynamics)
- `halo_bin`

And to understand why halo can’t move:

- `source_count_active` (number of `source_id`s with `E_i > 0` in-window)
- `max_source_share = max(E_i) / (ΣE_i + ε)`
- `top_source_id` (the max contributor)
- `top_source_effort`
- (optional) `source_ids_active` (list; can be large—consider truncation)

Answers:

- “Is `source_count_active` ever > 1? > 2?” (today: probably `{binance_spot, binance_perp}`)
- “Is halo low because one source dominates (`max_source_share ~ 1.0`)?”
- “Does asymmetry behave as expected (slow rise, fast drop)?”

---

## How to use the logs (questions → fields)

1) **Why is Y not moving?**
- Check: `log_return`, `disp`, `eff_raw`, `Y_raw`, `gate`, `Y_gated`, `Y`.

2) **Is Y being suppressed by the air-pocket guardrail?**
- Check: `gate`, `effort_floor`, `E_total`, `effort_median`.

3) **Is effectiveness scaling wrong (dynamic range)?**
- Check: distribution of `eff_raw` and `Y_raw` vs your expected storyboard regimes.

4) **Why does X drift perp?**
- Check: `E_spot`, `E_perp`, `E_spot_share`, plus `spot_fresh/perp_fresh` and event counts.

5) **Is halo not growing because K is too small?**
- Check: `source_count_active`, `max_source_share`, `top_source_id`.

6) **Is the price series stale or mismatched?**
- Check: `price_series_used`, `spot_fresh/perp_fresh`, `last_spot_event_ts/last_perp_event_ts`,
  `price_start/price_end`.

---

## Optional: sampling controls (to keep logs usable)

To avoid huge files:

- Log only a configured set of symbols (e.g. BTC/ETH/SOL + one thin symbol).
- Log at compute-time only (not every render frame).
- Keep each record single-line JSON (no nested blobs unless necessary).

