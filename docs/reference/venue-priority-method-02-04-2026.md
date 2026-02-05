# Venue Priority Method (Price Discovery First) — 2026-02-04

## 0) Purpose

Flow Lens becomes materially more useful as a structural diagnostic when its sources represent **real price discovery**.

This document defines a repeatable method to:

1) determine **which venues matter most** (ranked by influence on price), and
2) separate the “ideal influence ranking” from the “implementation order” (which may be constrained by API/keys).

This is meant to prevent venue-selection re-litigation.

Non-goal: this method is not a trading strategy, signal, alert, or prediction engine. It is a sourcing methodology for a structural lens.

---

## 0.1 Halo intent (constraint on venue selection)

Halo must represent **breadth of participation**, not “spot vs perp agreement”.

With only two sources (spot/perp), halo cannot reliably express breadth; it collapses into a two-way balance proxy.
Therefore, source selection should prefer venues that add **independent participation structure** (i.e., genuine additional
sources), not just more volume from followers.

This makes multi-venue expansion a semantic requirement for halo to fulfill its purpose.

---

## 1) Core principle (do not compromise)

**Venue influence over price is the top priority.**

Operational feasibility (keys, WS limits, implementation effort) can affect *staging*, but it must not redefine “importance”.

Therefore we maintain two lists:

- **Influence Order (truth-first):** “who moves price first”
- **Implementation Order (pragmatic):** “what we can ship next without breaking stability”

If Implementation Order differs from Influence Order, we treat it explicitly as a temporary staging compromise.

---

## 2) What “influence” means (for this system)

“Influence” is defined at the system’s timebase (currently Δ≈2s) as:

- **lead/lag contribution to short-horizon returns** (0–4s lags),
- especially during **impulse / regime transition** segments (where discovery matters most).

Volume alone is insufficient. A venue can be huge but lagging.

---

## 3) Measurement design (rank influence empirically)

### 3.0 Candidate narrowing (permissive, ranked pre-filter)

Before running the full pairwise discovery tournament, we do a **permissive, ranked pre-filter** using top-of-book
(L1) quality metrics.

Intent:

- This is **not** the Influence Order and does not “decide who leads”.
- It answers a narrower question: “which venues are even plausible leaders at 0–2s horizons?”
- The output is a **ranked list** plus a “keep set”. We keep broadly (permissive) and only demote obvious non-candidates.

Why L1 helps:

- Venues with consistently stale/wide/low-activity L1 are structurally unlikely to be first movers in short-horizon moves.

What L1 cannot do:

- It cannot prove discovery leadership (arbitrage mirroring can look very “active”).
- It must not be used as the final decision; the pairwise tournament is required.

Recommended L1 capture and metrics (BTC and SOL separately):

- Capture window: 15–30 minutes per session, across multiple sessions (US/EU/Asia).
- Per venue feed:
  - `l1_updates_per_s`
  - `median_spread_bps` and `p95_spread_bps`
  - `staleness_rate` (fraction of gaps > 1s and > 2s)
  - optional: `mid_move_latency_ms` relative to a robust composite mid (used carefully; can be contaminated by local latency)

Ranking output:

- “Discovery capacity” rank per venue (higher is “more plausible leader”), plus raw metric table.
- The keep set should remain broad (permissive): default to “keep all” unless a venue is clearly stale/illiquid.

### 3.1 Primary score: price discovery lead score

For each candidate venue V, symbol S (BTC, SOL), and sampling window W:

1) Build a trade-derived price series for V:
   - bucket prints at 100–250ms (or similar), compute a robust per-bucket price (VWAP or last).
2) Build a reference composite price series R:
   - initially: “best available multi-venue composite” (as we add sources),
   - initially (bootstrap): a chosen temporary reference (e.g. Binance spot+perp) with the understanding this can bias results.
3) Compute lead metrics over lags L ∈ {−4s … +4s}:
   - cross-correlation peak lag (where correlation is maximized),
   - fraction of intervals where Δp_V precedes Δp_R within [0s, 2s],
   - conditional lead score during impulse segments (see §3.3).

Interpretation:

- If V consistently leads R by ~0–2s in impulse segments, it is a price discovery venue for our purposes.
- If V consistently lags, it is a follower venue (still useful as effort source, but not a discovery anchor).

Implementation note:

- For the concrete pairwise + regime-windowed tournament design, see
  `docs/reference/venue-discovery-tournament-spec-02-04-2026.md`.

### 3.2 Secondary score: independent source contribution (dispersion value)

Even if a venue does not lead, it may add independent participation structure.

Measure:

- correlation of effort notional with other sources (redundancy vs independence),
- incremental improvement to halo dispersion behavior after adding V (qualitative + replay diagnostics).

### 3.3 Regime segmentation (required)

Discovery leadership is regime-dependent. All scoring must be reported by segment:

- **calm:** low realized volatility
- **impulse:** high realized volatility (sudden displacement bursts)
- **transition:** sign flips / regime handoffs (where persistence pivot behavior is active)

This avoids averaging away the only segments we care about.

### 3.4 Timing hygiene (required)

To compare venues fairly:

- use exchange timestamps when available,
- estimate and log ingestion latency/jitter (wallclock - exchange timestamp),
- prefer lag measures robust to micro-jitter (i.e., do not treat 50–150ms jitter as meaningful leadership).

---

## 4) Practical constraints (second priority, but real)

Constraints do not redefine importance, but they affect staging:

- keys / authentication requirements
- websocket rate limits
- data completeness (trade prints vs aggregated, missing fields)
- stable symbol mapping (spot vs perp vs dated futures)
- quote currency handling (USD/USDT/USDC)

Guideline:

- We can begin with USD≈USDT≈USDC as an operational approximation *only if* diagnostics show it does not distort switching/gating.
- If it distorts, we must implement proper conversion plumbing (already exists for some non-USDT quoted spot pairs).

---

## 5) Outputs (what we lock after running this process)

### 5.1 Influence Order (truth-first)

A ranked list by (symbol, market type):

- Perps discovery venues (BTC, SOL): V1 > V2 > V3 …
- Spot discovery venues (BTC, SOL): V1 > V2 > V3 …

This list is *not* “what we can implement first”; it is “what we should have, eventually”.

### 5.2 Implementation Order (staging)

A staged sequence that preserves stability:

1) Add one new venue for BTC+SOL only
2) Verify stability gates (replays + manual UI)
3) Expand symbols
4) Repeat

If a high-influence venue is blocked (keys), we document the blocker and move to the next feasible venue *without changing the Influence Order*.

---

## 6) Recommended first experiment set (bootstrap)

Because current sources are limited, bootstrap influence measurement should start with:

- BTC, SOL only
- short capture window(s): 30–120 minutes
- separate calm vs impulse scoring

Candidate venues to evaluate (initial):

- Spot: Coinbase, Binance, OKX, Bybit
- Perps/Futures: Binance, OKX, Bybit, Deribit

Notes:

- Deribit is prioritized for relevance to “volume that matters”, even if implementation is harder.
- Hyperliquid can be evaluated as a candidate if its lead score is non-trivial on BTC/SOL at 0–4s horizons.
- TradFi (watchlist): CME (BTC/ETH futures; and planned 24/7 products) is under consideration for US-hours leadership
  relevance, but postponed for now due to access/subscription constraints and session differences vs crypto venues.

---

## 7) Decision discipline

- Adding a new venue is an adapter change + source_id expansion; it must not introduce indicators, signals, or bars.
- If adding a venue changes any visual-channel semantics (X/Y/size/halo/lean/persistence), it requires a decision record.
- If the Influence Order is updated, record the evidence:
  - capture window(s), segment definitions, and lead metrics summary.

---

## 8) Open questions (to resolve when locking the first Influence Order)

1) What is the canonical “reference composite” R once we have ≥2 venues?
2) What sampling/bucketing rate is considered “good enough” vs too noisy?
3) Do we treat spot and perp influence separately (recommended), or merge into a unified “venue influence”?
