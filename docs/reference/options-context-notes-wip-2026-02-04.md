# Options Context Notes (WIP) — 2026-02-04

Status: informal reference notes (not a spec, not a decision record).

## 0) Why options matter (without changing the lens)

Options often influence short‑horizon price behavior **indirectly** via hedging, positioning constraints, and expiry
mechanics. That impact can:

- amplify or dampen spot/perp moves,
- change whether persistence “sticks” or decays,
- make absorption look different at the same apparent effort.

However, options trade prints do not map cleanly onto Flow Lens’s effort semantics (spot/perp aggressor effort) without
inventing interpretation (delta/vega/gamma, opening/closing, spreads, dealer vs client). Therefore:

- treat options as **context/regime** for interpreting what the lens already shows,
- keep options out of the core X/Y/size/halo/lean channels unless we later create a separate orthogonal view.

## 1) What we want options context to answer (at a glance)

Given what Flow Lens is showing (control, effectiveness, persistence, dispersion), options context should answer:

1) Are we likely in a **stabilizing** regime (moves dampened, mean-reverting) or **destabilizing** regime (moves extend)?
2) Are we near an expiry/roll boundary that can **flip** the regime quickly?
3) Are there nearby strike clusters that can cause **pinning** or “magnet” behavior?
4) Is risk transfer happening in the front (0D/1D/weekly) vs longer tenors (month/quarter)?

These are regime descriptors, not trade signals.

## 2) Highest-signal inputs (state + constraints)

These are the best “truth-preserving” options inputs because they are stateful constraints rather than interpretations of
individual trades.

### 2.1 Open interest concentration by strike/expiry

Compute and display:

- top OI strikes near current spot (e.g., within ±2% and ±5%)
- OI by expiry bucket (0D, 1D, weekly, monthly, quarterly)
- simple “concentration index” (e.g., top-N share)

Interpretation (context only):

- high near-spot OI clusters can correlate with pin risk and “stalling” behavior where persistence builds but price
  refuses to travel far.

### 2.2 Coarse gamma regime proxy (“GEX-like”)

A coarse gamma regime indicator is often more useful than options trade flow for explaining price behavior:

- positive gamma regimes tend to dampen moves (reversion, chop)
- negative gamma regimes can extend moves (trendiness, whip)

Notes:

- treat any gamma measure as approximate unless we have full surface + dealer positioning assumptions.
- consider reporting it as a **bucketed/qualitative** regime label with confidence rather than a precise number.

### 2.3 Expiry calendar and boundary effects

Track:

- next significant expiry time(s) (weekly, monthly)
- time-to-expiry for the dominant OI bucket

Why it matters:

- as expiry approaches, hedging sensitivity can change fast; “normal” persistence behavior can become misleading.

## 3) Secondary context inputs (flow-like, but easy to over-interpret)

These can be helpful but should be framed as “supporting evidence,” not as primary truth.

### 3.1 Options volume bursts by tenor

- volume by expiry bucket (0D/weekly/monthly)
- bursts relative to a rolling baseline (percentile)

Use:

- flag “stress / regime transition likely” periods where lens may become more reactive.

### 3.2 Implied volatility (IV) and term structure

- front vs back IV (e.g., 7D vs 30D)
- changes in IV (ΔIV) during the same windows we analyze venue lead

Use:

- explain “price moving without effort” or “effort not moving price” episodes as possibly vol repricing driven.

### 3.3 Skew / put-call balance (with caution)

- put/call volume ratio
- skew changes (e.g., 25d risk reversal proxy)

Caution:

- these can become “indicator-ish” and are easy to misread. Keep them descriptive.

## 4) What *not* to do (to preserve truth)

- Do not map “buy call / sell put” directly to bullish effort.
- Do not treat options prints as interchangeable with perp/spot aggressor effort.
- Do not overload existing visual channels (X/Y/size/halo/lean) with options information.

If we ever incorporate options as an input source, it likely becomes a separate subsystem or view with its own semantics.

## 5) Candidate venues / data sources (pragmatic)

Most relevant (crypto-native):

- Deribit (options + perp; institutional-ish crypto derivatives)

TradFi relevance:

- CME (futures/options) — high US-hours relevance, but access/licensing/subscription likely required.

ETF flow relevance (contextual):

- IBIT/ETFs impact price discovery and inventory dynamics, but are not a clean “effort event” source at 0–2s horizons.
  Treat as macro context, not a feed into the lens.

## 6) A minimal “Options Context Report” (informal outline)

This is a text/diagnostic artifact that can be produced alongside Flow Lens replays/captures.

Per symbol (BTC, ETH, SOL if available):

1) **Expiry calendar**
   - next weekly/monthly expiry timestamps
   - time-to-expiry for the dominant bucket
2) **OI concentration**
   - top strikes near spot (±2%, ±5%)
   - top expiries by total OI
   - concentration index
3) **Gamma regime (coarse)**
   - label: positive / negative / mixed
   - bucket: front-week vs back-month dominance
   - optional confidence score (internal only; do not display as a “signal”)
4) **Activity**
   - volume by tenor bucket (percentile vs baseline)
   - IV change summary (front vs back)

## 7) Integration with venue discovery tournament (future)

When running the venue discovery tournament, we can optionally annotate each capture window with:

- “near expiry” / “post-expiry” flags
- gamma regime label
- OI concentration warnings

This helps interpret leadership changes across sessions without turning options into a trading signal.

## 8) Open questions (for later, if we decide to formalize)

1) What level of approximation is acceptable for gamma regime (surface completeness, dealer positioning assumptions)?
2) Do we want ETH included as a control asset even if Flow Lens focus is BTC/SOL?
3) How do we define and store baselines (OI/volume percentiles) while honoring “engine holds no historical persistence”?
   - likely answer: diagnostics tooling maintains baselines; engine stays stateless.

