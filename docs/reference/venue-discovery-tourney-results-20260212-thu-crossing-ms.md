# Venue Discovery Tourney — 2026-02-12 (Thu) “t_cross_ms” / Crossing-Time-Mode=ms

This note is a “for fun” higher-resolution re-analysis of the **same 2026-02-12 scheduled capture**, using `crossing_time_mode=ms` (a.k.a. “t_cross_ms”) to compare **intra-bucket** crossing times.

It is intended to answer: “If we already bucket prices at 200ms, does it change anything to score leads using the *effective timestamp of the crossing bucket* (ms) instead of just the bucket index?”

## Inputs

- Baseline (bucket crossing): `docs/diagnostics/venue_tournament_scheduled/20260212_thu/`
- Re-run (ms crossing): `docs/diagnostics/venue_tournament_scheduled/20260212_thu_rerun_cross_ms/`
- Timebases (same as usual):
  - `recv`: what this machine received first (operationally what the lens can be “fast” on)
  - `exchange_local`: venue time corrected by constant offset (reduces some skew artifacts)
  - `exchange`: raw venue timestamps (can reward “timestamp semantics” more than actual observed speed)

## What Changed (Mechanically)

- **Bucket mode**: if venue A crosses on bucket `i` and venue B crosses on bucket `j`, leadership is decided by `i vs j` (ties are common).
- **ms mode (“t_cross_ms”)**: crossings are still detected on the *bucketed price series*, but leadership is decided by `t_cross_ms(A) vs t_cross_ms(B)` where `t_cross_ms` is the **effective per-bucket timestamp** of the crossing bucket.

Practical consequence: many “same bucket” ties become decisive, so **absolute score levels are not directly comparable across modes** (they generally rise when ties are broken).

## BTC Results (What We Care About Most)

### BTC / perp (Top Cluster Is Stable)

Leaders by timebase in `crossing_time_mode=ms`:

- `recv`: `binance_perp` (0.208) > `bybit_perp` (0.201) > `gate_perp` (0.191) ≈ `okx_perp` (0.188)
- `exchange_local`: `bybit_perp` (0.214) > `binance_perp` (0.207) > `gate_perp` (0.195) > `okx_perp` (0.186)
- `exchange`: `bybit_perp` (0.208) > `binance_perp` (0.191) > `okx_perp` (0.180) > `gate_perp` (0.173)

Delta snapshot (bucket -> ms) for BTC/perp (avg combined):

- `exchange_local`: bybit `+0.008`, binance `+0.007`, gate `+0.006`, okx `+0.008`
- `exchange`: bybit `+0.007`, binance `+0.006`, okx `+0.007`, gate `+0.011`
- `recv`: binance `+0.003`, bybit `+0.009`, gate `+0.011`, okx `+0.002`

Interpretation:

- **Same leader set** as bucket mode (no “new” perp appears).
- `ms` mostly adjusts **ordering inside the leader cluster** by reducing same-bucket ties.
- For the lens, `recv` still says **Binance-perp is the best “what I can see first on this machine” anchor**, with Bybit/OKX/Gate as competitive backups.

### BTC / spot (Bybit Spot Looks Better In ms Mode)

Leaders by timebase in `crossing_time_mode=ms`:

- `recv`: `coinbase_spot` (0.195) > `bybit_spot` (0.186) > `binance_spot` (0.179) > `okx_spot` (0.175)
- `exchange_local`: `coinbase_spot` (0.193) > `binance_spot` (0.181) > `bybit_spot` (0.179) > `okx_spot` (0.174)
- `exchange`: `binance_spot` (0.192) ≈ `coinbase_spot` (0.191) > `bybit_spot` (0.184) > `okx_spot` (0.167)

Delta snapshot (bucket -> ms) for BTC/spot (avg combined):

- `exchange_local`: coinbase `+0.010`, binance `+0.009`, bybit `+0.013`, okx `+0.007`
- `exchange`: binance `+0.005`, coinbase `+0.009`, bybit `+0.009`, okx `+0.006`
- `recv`: coinbase `+0.010`, bybit `+0.007`, binance `+0.006`, okx `+0.009`

Interpretation:

- Spot “leader set” remains: Coinbase/Binance/Bybit/OKX are the only serious contenders.
- `ms` mode notably improves **Bybit spot’s** combined score on BTC (`exchange_local` +0.013), making it look more competitive with OKX on “venue-time-ish” scoring.

## SOL (Control Symbol) — Where ms Mode Exposed Sensitivity

Most SOL sections move a bit under `ms` (often +0.005 to +0.015), but one change is large and worth highlighting:

### SOL / spot, timebase=recv (Leadership Flips)

- Bucket mode had `bybit_spot` as #1 (avg 0.184) with a huge σ (0.123) and a single extreme block:
  - `utc_boundary_early_asia`: `bybit_spot` combined 0.474 (next best 0.182)
- ms mode removes that outlier dynamic:
  - `binance_spot` becomes #1 (avg 0.174), `coinbase_spot` #2 (0.169), `bybit_spot` drops to #3 (0.143), σ collapses.

Interpretation:

- This is exactly the regime where bucket-index scoring can be “too quantized”: if a venue repeatedly lands on the favorable side of same-bucket crossings, it can look unrealistically dominant in a block.
- `t_cross_ms` makes those same-bucket contests more decisive and appears to **reduce false dominance** for SOL/spot in `recv`.

## What This Means For The Lens

If you only take one thing from the `t_cross_ms` experiment:

- It does **not** discover a new magical venue; it mostly refines **ordering inside the same small leader cluster**.
- It can, however, flag that some bucket-mode “wins” (especially in lower-liquidity spot like SOL/spot on `recv`) are likely **bucket-quantization artifacts** rather than true “we receive them first” leadership.

Given Flow Lens’ purpose (“who is in control, is their effort effective?”), the operationally relevant timebase remains:

- `recv` for “what the lens can actually react to first on this machine”.

`exchange_local` / `exchange` remain useful as *diagnostics* (timestamp semantics and clock quality), but they shouldn’t override `recv` for latency-sensitive weighting decisions unless you have strong reasons to trust and normalize venue timestamps.

## Pointers

- ms re-run summaries:
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu_rerun_cross_ms/run_summary_tb_recv.txt`
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu_rerun_cross_ms/run_summary_tb_exchange_local.txt`
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu_rerun_cross_ms/run_summary_tb_exchange.txt`
- baseline summaries (bucket crossing):
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_recv.txt`
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_exchange_local.txt`
  - `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_exchange.txt`

