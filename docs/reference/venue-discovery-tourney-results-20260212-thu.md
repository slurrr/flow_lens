# Venue Discovery Tourney Results — 2026-02-12 (Thu) Run

This report analyzes the latest scheduled tournament run and compares the **new analysis hygiene** (stale-on-arrival filtering) vs the **old method** (no stale filtering) on the *same capture*.

Primary focus: **BTC** (SOL is treated as a control).

## Inputs

- Latest run (new method): `docs/diagnostics/venue_tournament_scheduled/20260212_thu/`
  - Uses `--drop-stale --max-wire-lag-ms 2000` (confirmed in report headers).
- Old method re-analysis (same capture, no stale drop): `docs/diagnostics/venue_tournament_scheduled/20260212_thu_rerun_nodropstale/`
- Timebases:
  - `recv`: alignment by local receive time (what the lens actually “sees” on this machine)
  - `exchange_local`: alignment by exchange time corrected by per-venue constant offset (reduces clock skew artifacts)
  - `exchange`: alignment by raw venue timestamps (can over-credit venues with favorable timestamp semantics)
- Regimes:
  - `impulse` is weighted highest in the combined score (default weights: `0.60/0.30/0.10` for impulse/transition/calm).

## What The Scores Mean (So 0.20 vs 0.17 Is Interpretable)

Each score is an **average pairwise win rate**:

- For each event window and each pair (A,B), the tournament looks for a *first crossing* on each venue’s bucketed series.
- A “win” is only awarded if the lead is:
  - larger than the jitter guard, and
  - confirmed within the 2s (impulse/transition) or 4s (calm) confirm horizon.
- Otherwise it’s a **tie** (very common in practice).

Therefore, absolute values often sit around `0.10–0.30` because **ties consume most contests**. A higher score means:

- “In head-to-head contests where both venues moved enough to be comparable, this venue more often crosses first by enough margin to be credited.”

### Margin Intuition

Treat a `0.03` combined gap (e.g., `0.20` vs `0.17`) as:

- about **+3 percentage points** more decisive wins per opponent (after ties), averaged across the opponent set, and then regime-weighted.

Whether that’s “a lot” depends on:

- does the gap show up in **impulse** (matters), or only **calm** (often noise/hygiene-sensitive),
- is it stable across blocks (low `σ`) and shows consistent top1/top2 placements.

## New Method vs Old Method (Stale Drop On vs Off)

### Summary: BTC Is Stable; SOL Calm Is Not

On this run, enabling stale filtering at `wire_lag_ms > 2000`:

- **BTC/perp and BTC/spot rankings are essentially unchanged** (deltas are ~0.000–0.004).
- **SOL/perp combined changes materially on `exchange_local`**, but the change is almost entirely from the **calm** component; **impulse stays stable**.

Interpretation:

- The new method is doing what we want: it cleans up time-hygiene pathologies (history bursts / stale prints) without changing “who wins where it counts” (impulse leadership).

### Where The Stale Filter Actually Bit (Totals Across Blocks, exchange_local)

Total stale-on-arrival drops (wire_lag > 2000ms) across all 7 blocks:

- BTC/perp:
  - `hyperliquid_perp`: 58
  - `okx_perp`: 8
  - `deribit_perp`: 3
- BTC/spot:
  - `upbit_spot`: 432
- SOL/perp:
  - `hyperliquid_perp`: 151
  - `okx_perp`: 2
- SOL/spot:
  - `upbit_spot`: 1665

This explains why BTC changed little (most “leaders” weren’t being dropped), while SOL calm behavior can swing (small series changes can strongly affect calm in chop).

### Delta Snapshot (Old -> New)

BTC deltas are negligible. Examples:

- `exchange_local` BTC/perp:
  - `binance_perp`: `0.200 -> 0.200` (Δ `+0.000`)
  - `bybit_perp`: `0.207 -> 0.206` (Δ `-0.001`)
  - `okx_perp`: `0.181 -> 0.178` (Δ `-0.003`)
- `recv` BTC/perp:
  - `binance_perp`: `0.201 -> 0.205` (Δ `+0.004`)

Largest instability (control symbol):

- `exchange_local` SOL/perp:
  - `binance_perp` calm: `0.588 -> 0.364` (Δ `-0.224`)
  - impulse: `0.328 -> 0.327` (Δ `-0.001`)

Takeaway: for venue selection, weight impulse heavily and treat calm-only swings as measurement sensitivity.

## Who Won Where (Latest Run)

Below: “wins” means “top by average combined score across blocks,” plus notes on impulse leadership and timebase meaning.

### BTC / Perp

#### `recv` (most relevant to a live lens on this machine)

Source: `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_recv.txt`

- Clear leader: `binance_perp`
  - avg combined `0.205`, impulse `0.274`
  - top1 combined `5/7`, top1 impulse `6/7`
- Next tier: `bybit_perp` (`0.192`, impulse `0.253`), `okx_perp` (`0.186`, impulse `0.252`)
- `gate_perp` is competitive but not an impulse #1 on this run (good redundancy candidate, not “the fastest”).

What this means for the lens:

- If the lens is selecting “fastest available perp price,” Binance should be treated as the primary anchor on this machine, with Bybit/OKX as strong backups.

#### `exchange_local` (best compromise between “venue time” and “what we saw”)

Source: `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_exchange_local.txt`

- Leader group is tight:
  - `bybit_perp`: avg `0.206` (top1 3/7)
  - `binance_perp`: avg `0.200` (top1 3/7)
  - `gate_perp`: avg `0.189` (top1 1/7)
  - `okx_perp`: avg `0.178`
- Impulse view:
  - `binance_perp` impulse `0.264` slightly above `bybit_perp` `0.261`

What this means for the lens:

- Leadership is not “one venue wins everywhere.” It rotates by block. The top set is consistent; ordering inside it is close.
- `exchange_local` supports using **Binance + Bybit + OKX** as the core perp roster; Gate is a plausible 4th for redundancy.

#### `exchange` (raw venue timestamps; least trustworthy for live selection)

Source: `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_exchange.txt`

- `bybit_perp` dominates: avg `0.201`, top1 `6/7`.
- Binance is never top1 on combined in this timebase for this run.

Interpretation:

- This is exactly the pattern that warns against using `exchange` rankings directly for the live lens: venues can look “best” on their own timestamps while not being best on `recv`.

### BTC / Spot

Spot is structurally harder: less uniform liquidity, and USD-vs-stablecoin quoting differences.

#### `recv`

Source: `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_recv.txt`

- Leader: `coinbase_spot` avg `0.185`, impulse `0.242`
- Very strong: `bybit_spot` impulse `0.248` (often top2), `binance_spot` impulse `0.233`
- `okx_spot` is competitive as an additional spot venue.
- `gate_spot` is present and time-clean, but not a leader on BTC spot in this run.

What this means for the lens:

- Coinbase remains the best “BTC spot leader” candidate in this dataset.
- Bybit/Binance spot provide important redundancy and often show up early on impulse windows.

#### `exchange_local`

Source: `docs/diagnostics/venue_tournament_scheduled/20260212_thu/run_summary_tb_exchange_local.txt`

- `coinbase_spot` avg `0.183` (top1 3/7)
- `binance_spot` avg `0.172` (top1 2/7)
- `okx_spot` `0.167`, `bybit_spot` `0.166`

Interpretation:

- Spot leadership rotates more than perp. The top 4 (Coinbase/Binance/OKX/Bybit) are all plausible “good spot coverage.”

## Time Hygiene (Why Some Venues Don’t Score Well)

From the per-block `exchange_local` time hygiene tables, median jitter (p95 around the per-venue median offset) across blocks for BTC/perp:

- `gate_perp`: ~120ms
- `okx_perp`: ~135ms
- `bybit_perp`: ~129ms
- `binance_perp`: ~181ms
- `hyperliquid_perp`: ~260ms

Given `bucket_ms=200` and `jitter_guard_ms=250`, venues with materially higher jitter are less likely to be credited with “clean wins” even if they sometimes move early.

## What Matters “Where It Counts”

For building a **fast, accurate lens**, prioritize:

1. `recv` impulse leadership (what arrives first to the lens)
2. `exchange_local` impulse leadership (reduces timestamp artifacts, still tied to venue event times)
3. Avoid overweighting `exchange` (raw timestamps) when it contradicts `recv`.

On this run, the consistently strong set on BTC/perp (impulse, recv + exchange_local) is:

- `binance_perp`
- `bybit_perp`
- `okx_perp` (often top2; occasionally top1)

BTC/spot “coverage leaders” (impulse, recv + exchange_local) are:

- `coinbase_spot`
- `binance_spot`
- `bybit_spot`
- `okx_spot` (supporting)

## Roster Recommendation (High Quality + Balanced Coverage)

Venue-count balance is a secondary constraint; semantic correctness and “fast where it counts” are primary. That said, a practical, balanced roster that keeps redundancy without bloating:

### Recommended Core (Implement / Keep)

- Perp (3): `binance_perp`, `bybit_perp`, `okx_perp`
- Spot (3): `coinbase_spot`, `binance_spot`, `bybit_spot`

### Next Add (To Improve Balance + Quality)

- Add `okx_spot` (makes spot 4 deep with a credible venue; helps halo/dispersion when spot participates broadly)

### Conditional Adds (Needs More Evidence)

- `gate_perp`: looks competitive in `exchange_local` combined and can place top2, but is not an impulse leader on `recv` in this run.
  - Add if you want more perp redundancy and it stays time-clean across more runs.
- `gate_spot`: present and time-clean here but not yet a BTC spot leader.
  - Re-run more days; if it becomes a consistent top2/top3 on BTC/spot impulse (recv + exchange_local), then add (and pair with gate_perp for balance).
- `hyperliquid_perp`: not competitive for BTC leadership on `recv`/`exchange_local` here and has higher jitter; defer for now unless/ until transport/timestamp hygiene changes.

## Notes On “Balance”

Equal venue counts (spot vs perp) are not the goal by itself. The lens’s X-axis is computed from **summed effort** per side. Adding more perp venues can pin X negative simply by adding more measured perp effort. A balanced roster should therefore mean:

- enough spot venues to detect “spot stepped in” as a real structural change (not a single-feed artifact),
- enough perp venues to reflect the dominant venue where it is actually leading on `recv`/`exchange_local`,
- minimal inclusion of low-quality feeds that increase dispersion without adding truth.

