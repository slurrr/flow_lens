# Venue Discovery Tournament Spec (Pairwise, Regime-Windowed) — 2026-02-04

## 0) Purpose

This spec defines **how Flow Lens ranks venues by price discovery influence** using:

- a **pairwise tournament** (venue A vs venue B),
- measured on the **same event windows** (shotgun / even testing ground),
- with **regime segmentation** so chop does not dominate the score.

This is a sourcing study spec. It does not add trading signals or opinions to the lens.

---

## 1) Definitions

### 1.1 “Lead” (for this project)

Venue A “leads” venue B when:

1) a meaningful move begins on A earlier than on B (by more than timestamp/latency noise), and
2) that move is followed/confirmed on B within a short horizon.

Leadership is evaluated at short horizons aligned to the system’s cadence:

- primary: 0–2 seconds
- secondary: 0–4 seconds

### 1.2 “Meaningful move”

We do not score “lead” on micro-noise. A move must exceed an event threshold defined per session:

- impulse threshold: top quantile of short-horizon absolute returns (preferred)
- transition threshold: sign flip + sufficient magnitude (preferred)
- optional micro threshold: a smaller absolute-return threshold for “chop leadership” checks (used as a control, see §6)

---

## 2) Inputs and data schema

### 2.1 Required data per venue feed

For each trade print (or agg-trade) captured:

- `venue_id` (e.g. `coinbase`, `binance`, `okx`, `bybit`, `deribit`)
- `market_type` (`spot` / `perp` / `futures`) — keep separate rankings
- `symbol` (base, e.g. `BTC`, `SOL`)
- `ts_exchange_ms` (exchange event timestamp)
- `ts_recv_ms` (local receive timestamp)
- `price` (quote in USD/USDT/USDC as captured; conversion handled separately)
- `size` (base qty if available)
- `notional` (quote notional if available; else `price * size`)

Notes:

- Prefer exchange timestamps for alignment, but log `ts_recv_ms` to estimate jitter and detect skew.
- Conversion: USD≈USDT≈USDC is acceptable for the *study bootstrap* if we also compute and report whether the
  approximation plausibly affects lead metrics (e.g., systematic basis drift should not appear at 0–4s horizons).

### 2.2 Bucketing / price series construction

Build a robust per-venue price series by time-bucketing exchange timestamps:

- bucket size: 100–250ms recommended
- per-bucket price: VWAP or last (VWAP preferred if size is available)

All comparisons are done on this bucketed series, not raw ticks.

---

## 3) Regime segmentation (required)

Each session is segmented into:

- **impulse** windows: large short-horizon moves (definition in §4)
- **transition** windows: regime flips / direction handoffs
- **calm/chop**: everything else (primarily used as a control, see §6)

Scoring priority:

1) impulse
2) transition
3) calm/chop (included but low-weight by default; see §3.1)

### 3.1 Regime weighting and filterability (required for iteration)

All results must be reported per-regime, and the *combined* Influence Score must be:

- **filterable** (include/exclude calm/chop), and
- **weightable** (so we can compare “impulse-only” vs “all-regime” rankings without re-running capture).

Recommended default weights for combined ranking:

- impulse: 0.60
- transition: 0.30
- calm/chop: 0.10

Common comparison profiles:

- impulse+transition only: set calm/chop weight to 0.00
- impulse-only: set transition and calm/chop weights to 0.00

---

## 4) Event window extraction (shotgun set)

To avoid reference bias, event windows are extracted from a **union of triggers**:

1) Build a per-bucket **composite reference price**:
   - `P_ref(t) = median(P_venue(t) across venues available at t)`
   - (median reduces one-venue dominance; it’s robust to one feed glitching)
2) Compute `r_ref(t) = log(P_ref(t)/P_ref(t-Δt))` for short horizons (e.g., 500ms–1s).
3) Define impulse events by thresholding `|r_ref|` at a session quantile (e.g., ≥ p95) and enforcing a cooldown to
   avoid overlapping windows.
4) Define transition events by sign-flip + magnitude threshold on `r_ref` or on a smoothed short-horizon return.

Each event yields an event window:

- `window = [t0 - pre_s, t0 + post_s]` (e.g., pre=2s, post=6–10s)

---

## 5) Pairwise tournament scoring

For each event window and each pair of venues (A, B):

1) Define an event direction `dir` from the composite move (sign of `ΔP_ref` over the event horizon).
2) For each venue V ∈ {A, B}, compute the **first-crossing time**:
   - find earliest `t` in the window where cumulative move on V reaches a fraction of the composite move
     (e.g. 30–50%), in the event direction.
3) Compare:
   - A wins if `t_A + jitter_guard < t_B` and B confirms within the horizon (0–2s primary, 0–4s secondary).
   - B wins by the symmetric condition.
   - Otherwise: tie / no-contest (insufficient movement, missing data, or ambiguous timing).

### 5.1 Jitter guard (required)

Use a jitter guard to avoid awarding “lead” due to timestamp noise:

- default: 200–300ms
- compute per-venue jitter estimate from `ts_recv_ms - ts_exchange_ms` and adapt if necessary.

### 5.2 Outputs per pair

For each pair (A,B), report:

- win rate (A wins / valid contests)
- tie/no-contest rate
- median lead time when A wins
- breakdown by regime (impulse vs transition vs calm)

---

## 6) Chop/calm role (control, not signal)

Chop leadership is easy to hallucinate because the “moves” are near the noise floor.

Therefore:

- calm/chop windows are always reported, but treated as:
  - a **sanity/control check** (e.g., consistent “calm leadership” can indicate timestamp skew), and
  - an **optional, low-weight component** in the final combined score (see §3.1).

If calm/chop materially changes the ranking while impulse/transition do not, treat that as a measurement warning.

---

## 7) Study design (efficient but defensible)

### 7.0 Optional pre-filter (permissive, ranked)

Before running the full tournament, an optional step is to run a **permissive L1 top-of-book pre-filter** across a wide
set of candidate venues.

This pre-filter:

- produces a **ranked plausibility list** (“discovery capacity”), and
- helps de-prioritize venues that are structurally unlikely to lead due to stale/wide/low-activity books.

It must not be used as the final decision. The tournament remains the source of truth.

See also: `docs/reference/venue-priority-method-02-04-2026.md` (§3.0).

### 7.1 Minimal viable study

- Symbols: BTC first, then SOL
- Sessions: 6 windows total (30–60 min each)
  - spread across: US active, EU, Asia (2 each)
  - ensure at least one high-vol session for impulse events

### 7.1.1 Extensive capture plan (recommended; extensive, not overkill)

Status: superseded by the locked M/W/F + Sunday plan in §7.1.2.

Goal: collect enough **high-activity** and **cross-session** evidence to avoid “leader-by-sample” outcomes, without
capturing all day.

Recommended study length:

- 3 separate days (preferably: 2 weekdays + 1 weekend day)
- 5 windows/day (≈6 hours/day total)

Recommended windows (MST, UTC−7) with UTC equivalents:

1) **EU open ramp**: 00:45–01:45 MST (07:45–08:45 UTC)
2) **EU active**: 03:30–04:30 MST (10:30–11:30 UTC)
   - if you routinely see activity 03:00–05:00 MST, expand to 03:00–05:00 MST (10:00–12:00 UTC)
3) **US data + cash open / overlap**: 06:30–08:30 MST (13:30–15:30 UTC)
   - if you routinely see activity 06:00–09:00 MST, expand to 06:00–09:00 MST (13:00–16:00 UTC)
4) **US close**: 13:30–14:30 MST (20:30–21:30 UTC)
   - if you routinely see activity around ~15:00 MST, expand to 13:30–15:00 MST (20:30–22:00 UTC)
5) **UTC boundary / day roll**: 16:30–17:30 MST (23:30–00:30 UTC)

Optional add-on window (only if rankings look session-unstable):

- **Asia prime**: 19:00–20:00 MST (02:00–03:00 UTC)

Operational notes:

- Capture BTC and SOL simultaneously for all venues that support them. If a venue only meaningfully supports BTC, keep it
  in BTC-only rather than forcing a low-quality SOL proxy.
- Run the tournament analysis for all three timebases (`exchange`, `recv`, `exchange_local`) on the *same* capture so we
  can distinguish “true lead” from “latency artifact” without recapturing.

### 7.1.2 Capture plan (final; M/W/F + Sunday, “extensive not overkill”)

This is the locked capture plan for venue discovery. It is designed to:

- cover the user’s observed high-activity windows,
- capture weekly open/close dynamics (via Sunday + UTC boundary coverage), and
- be analyzable as both “all-up blocks” and hour-sliced bookends (§7.1.3).

Target sessions (what we care about):

- US-session leadership on Monday, Wednesday, Friday
- Weekly boundary behavior on Sunday (weekly open), plus Monday coverage via the Sunday-start pass

Operational note (important):

- This scheduler is anchored at **16:00 MST**. If you start it at ~15:45–15:55 MST, it will sleep until 16:00 and then
  run the full 24h plan.
- Therefore, to capture a given day’s US windows (06:00–16:00 MST), you must start the scheduler the **prior afternoon**:
  - Saturday start → Sunday US windows
  - Sunday start → Monday US windows
  - Tuesday start → Wednesday US windows
  - Thursday start → Friday US windows

Run cadence:

- Run **one scheduler pass** per capture day.
- Each pass covers a single 24h capture window anchored at **16:00 MST** (UTC−7).

Scheduler start time (required):

- Start the scheduler between **15:45–15:55 MST** so it can queue the first block at 16:00 MST.
  - Run: `./.venv/bin/python scripts/venue_tournament_scheduler_24h.py --gzip`
  - Preview schedule: `./.venv/bin/python scripts/venue_tournament_scheduler_24h.py --dry-run`
  - Default allowed start days are aligned to the mapping above: `sat,sun,tue,thu`

Blocks captured per 24h pass (MST, UTC−7) with UTC equivalents:

- **UTC boundary + early Asia**: 16:00–18:00 MST (23:00–01:00 UTC)
- **Asia prime**: 19:00–21:00 MST (02:00–04:00 UTC)
- **EU open ramp**: 00:45–01:45 MST (07:45–08:45 UTC)
- **EU active / pre-US**: 02:30–05:30 MST (09:30–12:30 UTC)
- **Morning impulse / overlap**: 06:00–09:00 MST (13:00–16:00 UTC)
- **Late morning**: 09:00–12:00 MST (16:00–19:00 UTC)
- **US afternoon**: 13:00–16:00 MST (20:00–23:00 UTC)

Notes:

- This is intentionally “one-day extended”: it covers most of the US day plus the sessions that frequently seed it.
- The Sunday run is for weekly boundary behavior. If forced to choose one day to never skip, prioritize Sunday.
- Reference implementation: `scripts/venue_tournament_scheduler_24h.py` (runs the blocks + emits per-block all-up and
  hour-sliced reports into timestamped folders; no cleanup required).

### 7.1.3 Analysis slicing (“bookends”) for long blocks (required for interpretability)

Long blocks must not be reported only as an all-up aggregate. For each block, produce **both**:

1) **All-up block report** (the aggregate within the block window)
2) **Hour-sized slices** aligned to the block clock with overlap, so leadership rotation becomes visible.

Default slicing policy:

- slice size: 60 minutes
- step: 30 minutes (50% overlap)

Examples:

- For 06:00–09:00 MST:
  - hour slices: 06:00–07:00, 07:00–08:00, 08:00–09:00
  - overlapped slices: 06:30–07:30, 07:30–08:30
- For 02:30–05:30 MST:
  - hour slices: 02:30–03:30, 03:30–04:30, 04:30–05:30
  - overlapped slices: 03:00–04:00, 04:00–05:00

Implementation note:

- The slicing can be achieved either by (a) running capture in back-to-back chunks that match the slice policy, or (b)
  running a single capture per block and filtering by time range during analysis to emit per-slice reports.

### 7.2 Candidate shotgun set (initial)

Spot candidates:

- Coinbase
- Binance
- OKX
- Bybit
- Upbit (regional signal; especially relevant for “sometimes leads” hypotheses)

Perp/futures candidates:

- Binance
- OKX
- Bybit
- Deribit
- Hyperliquid (wildcard; include if we believe it can lead in some regimes)

TradFi watchlist (not in the initial shotgun set):

- CME (BTC/ETH futures; and planned 24/7 products) is under consideration for US-hours leadership relevance, but postponed
  for now due to access/subscription constraints and session differences vs crypto venues.

### 7.3 Deliverable: Influence Order v1

For each (symbol, market_type):

- ranked venues by impulse+transition scores
- include the evidence summary (event counts, jitter estimates, and per-pair win tables)

---

## 8) Known bootstrapping caveat

When the system has only one venue integrated, any “reference” can bias results.

This spec explicitly reduces bias by:

- using a median composite reference built from the candidate set itself,
- scoring in a pairwise tournament,
- downgrading calm/chop scores to control-only.
