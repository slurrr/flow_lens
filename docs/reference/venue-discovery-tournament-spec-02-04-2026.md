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
3) calm/chop (control-quality and sanity checks; do not let it dominate the ranking)

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

- calm/chop windows are used primarily as a **sanity/control check**:
  - if one venue “leads” consistently during calm, suspect timestamp skew or ingestion artifacts.
- calm results can be reported but should have **low weight** in the final Influence Order.

---

## 7) Study design (efficient but defensible)

### 7.1 Minimal viable study

- Symbols: BTC first, then SOL
- Sessions: 6 windows total (30–60 min each)
  - spread across: US active, EU, Asia (2 each)
  - ensure at least one high-vol session for impulse events

### 7.2 Candidate shotgun set (initial)

Spot candidates:

- Coinbase
- Binance
- OKX (or Bybit, pick one to start)

Perp/futures candidates:

- Binance
- OKX
- Bybit
- Deribit

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

