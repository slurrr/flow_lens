# Multi‑Venue Adapter Architecture Contract (Phase 1) — 2026-02-04

Status: locked contract for Phase 1 multi‑venue plumbing. Changes require a decision record.

Decision records to create/maintain for this contract:

- `FL-0057` base_symbol contract + migration
- `FL-0058` canonical aggressor inference + diagnostics gates
- `FL-0059` filter context reset scope
- `FL-0060` multi-source price selector policy + switch logging

## 0) Purpose

Flow Lens is moving from a “prove it works” single‑venue prototype to a multi‑venue structural diagnostic.

This document defines a minimal architecture that:

- keeps **adapters dumb** (parsing + unit normalization only),
- preserves **engine semantics** (engine is the only interpreter),
- enables **multi‑venue** without hardcoding Binance assumptions, and
- keeps the door open for a future **venue filter / dropdown** in the UI without re‑refactoring.

Non‑goal: this document does not add indicators, signals, alerts, bars, or multi‑symbol dashboards.

## 1) Constraints (binding)

- Visual semantics remain orthogonal (X=control, Y=effectiveness, size=force magnitude, halo=dispersion, lean=transition).
- Adapters must not compute dominance, effectiveness, dispersion, persistence, bins, or “opinions”.
- All state derives from the active window Δ (no historical persistence beyond rolling window).
- Any semantic change to a visual channel requires a decision record.

## 2) Terminology

- **Source**: a single, named effort stream (e.g. `coinbase_spot`, `bybit_perp`).
- **Venue**: exchange / execution venue (Coinbase, Binance, CME, …).
- **Market type (for X)**: the category used by X control semantics (**spot vs perp**).
- **Instrument class**: spot, perp, dated futures, options, etc (may not map 1:1 to X market type).
- **Price selector**: chooses which source’s price to treat as the reference price series.

## 3) Architecture overview

```
WS adapters (dumb)  ->  Supervisor/Router  ->  RollingEventBuffer  ->  Engine -> Renderer
                                  |                  |
                                  |                  +-> Price selector (multi-source)
                                  +-> Source registry + Filter state (future UI)
```

Key idea: **sources are first‑class**, and the orchestration layer routes them uniformly.

## 4) Adapter contract (dumb adapters)

### 4.1 Output event schema (per print)

Adapters emit a stream of “effort events” with:

- `base_symbol` (canonical: `BTC`, `SOL`, …)  ← required to eliminate venue‑specific symbol mapping hacks
- `timestamp_ms` (exchange event time in epoch ms if available; else receive time)
- `source_id` (stable; e.g. `coinbase_spot`, `okx_perp`)
- `market_type_for_x` (`spot` | `perp`)  ← this is the meaning that drives X control semantics
- `aggressor_side` (`buy` | `sell`)  ← if the venue supplies it; otherwise see §4.3
- `price_usd` (USD≈USDT≈USDC for bootstrap; quote conversions handled explicitly)
- `effort_value` (typically `price_usd * size_base` when size exists; else defined by capability mode)

Notes:

- If we keep the current `flow_lens.models.event.Event` shape, then the adapter must populate `Event.price` as `price_usd`
  and `Event.timestamp` as `timestamp_ms`.
- `AdapterEvent.symbol` should remain the venue-native instrument id for traceability and debugging.
- `base_symbol` should be an explicit field (do not overload `symbol` with canonical meaning).

### 4.2 Quote conversion responsibility

Adapters may normalize quote currencies to USD‑equivalent **only as a unit conversion**, not as interpretation.

Acceptable initial approximation:

- `USD≈USDT≈USDC` (bootstrap) as long as diagnostics show it does not introduce switching artifacts at 0–4s horizons.

If a venue emits non‑USD quotes (e.g. KRW, EUR):

- treat it as a separate study stream unless we have explicit conversion plumbing,
- or add a dedicated quote‑rate feed (still “dumb”: rate update only) and convert in adapter.

### 4.3 Missing aggressor side (capability downgrade)

Not all venues provide aggressor side.

We must not “invent” semantics silently. Options (must be explicit per source):

1) **Full**: venue provides aggressor (`buy`/`sell`) and size → normal effort + direction.
2) **Signed-by-rule**: infer aggressor via a mechanical microstructure rule (e.g., trade price vs bid/ask at that moment).
   - This is allowed only if treated as a pure mapping rule and documented as a source capability.
3) **Unsigned**: if we cannot determine aggressor reliably, the source contributes to magnitude/dispersion but not to
   direction (engine receives `effort_value` but no sign contribution).
   - Requires an engine‑side policy for “directionless effort” (likely a decision record).

For now: prefer sources with (1). If we add (2) or (3), we lock it in a decision record and record the capability in config.

#### 4.3.1 Denominator semantics risk (must be made explicit)

If unsigned effort is included in “total effort” denominators while excluded from directional numerators, it can mute
directional measures (and therefore Y/persistence inputs) in a way that is *mathematically consistent* but perceptually and
semantically surprising.

Before accepting any `aggressor_mode=none` source into the live lens, we must explicitly choose:

- **Option A (conservative):** Y is normalized by total effort including unsigned (more muted, but “effort is effort”).
- **Option B (direction-clean):** Y is normalized by directional-capable effort only (cleaner meaning for effectiveness).

This is a decision-record item, not a tuning knob.

#### 4.3.2 Canonical aggressor inference (Phase 1, if enabled)

If `aggressor_mode=inferred` is enabled for Phase 1 multi-venue, we must lock **one canonical inference method** and forbid
per-adapter custom logic.

Proposed default method (deterministic, auditable):

- Require best-bid/best-ask (BBO) context at inference time (or nearest prior BBO within a max age budget).
- Mapping:
  - trade >= ask - epsilon → buy
  - trade <= bid + epsilon → sell
  - else compare to mid:
    - trade > mid → buy
    - trade < mid → sell
    - trade == mid → tick rule fallback (price change vs last trade)
- If no valid BBO within age budget: **do not infer** (mark unknown for that print).

Phase 1 rule to avoid semantic bleed:

- trades with unknown aggressor should be treated as dropped for that source (counted + logged) rather than silently
  entering the engine as “unsigned”.

Diagnostics requirements for inference are listed in §12 and Dev Notes (3).

### 4.4 Adapters do not decide price series

Adapters emit their own price observations; they do not decide which price becomes the lens reference series.

## 5) Source registry (the “brain” inputs table)

Introduce a canonical registry (config-backed) describing each `source_id`:

- `source_id` (stable string; unique)
- `venue` (e.g. `coinbase`)
- `instrument_class` (spot, perp, futures, …)  ← informational / future routing
- `market_type_for_x` (`spot`|`perp`)  ← required; drives X semantics
- `price_eligible` (bool)  ← can be used as reference price series
- `price_priority` (int)  ← tie-breaker ordering for price selector
- `capabilities`:
  - has_size (bool)
  - has_aggressor (bool)
  - aggressor_mode (`native`|`inferred`|`none`)
  - quote_mode (`usd_like`|`converted`|`foreign`)

This registry is the “brain”: it enables orchestration without adapters gaining interpretation logic.

Registry discipline:

- Capability flags are mandatory for every source; no implicit defaults.
- A startup validator should fail fast on incomplete or contradictory entries.
- Keep explicit schema versioning so replays remain interpretable as fields evolve.

## 6) Supervisor/Router responsibilities

The supervisor/router sits between adapter streams and the buffer:

- start/stop adapters, reconnect, basic staleness stats
- merge adapter streams into a single queue
- (future) apply a **source enable/disable** mask (UI-driven) without changing adapter code
- attach diagnostics fields (recv timestamp, message counts) without polluting engine semantics

Non-goals:

- It does not compute dominance/effectiveness/dispersion.
- It does not resample or smooth prices beyond trivial bucketing for diagnostics output.

## 7) Multi-source price selector (reference series)

The lens needs a reference price series for window start/end price and “air pocket” guardrails.

With multi‑venue:

- track last price per `(base_symbol, source_id)`
- track staleness per source
- choose an **active price source** per base symbol using:
  - `price_eligible` + `price_priority`,
  - staleness cutoff (time-based, not tick-based when possible),
  - hysteresis / carry-forward (avoid flip‑flop; reuse FL‑0052 intent, generalized).

Important: price selection is **orthogonal** to effort aggregation. A source can contribute effort without being the active
price series.

Auditability requirement:

- Log every price-source switch with a reason (`stale`, `recovered`, `priority`, `manual_override` if ever added).

Policy-pluggable requirement (Phase 1):

- Implement the selector behind a policy interface so we can change behavior without refactoring buffer/engine plumbing.
- Phase 1 default policy: `priority_sticky` (explicit priority + staleness + hysteresis).
- Phase 2 candidate policy: `leader_sticky` (tourney/leader-informed target with stickiness) without changing diagnostics
  fields or switch logging.

## 8) Venue filtering (future UI feature)

Desired future behavior:

- user selects one or multiple venues/sources to include/exclude
- the lens recomputes structure on the same rolling window Δ using only the selected sources
- this is a *lens choice*, not an alert/signal

To make this possible without refactoring later:

- keep the rolling buffer as a superset store (all events),
- apply the filter mask at aggregation time (or snapshot time), not at adapter ingest time.

Open question to decide later:

- “render-only” filtering vs “engine” filtering.
  - Render-only: state stays full; UI just hides contributions (less truthful for “what if we exclude X”).
  - Engine filtering: state recomputes from subset (truthful, but state changes when filter changes).
  - For Flow Lens’s intent (“who is in control”), **engine filtering** is the truthful option.

Filter-change hygiene requirement:

- When filter masks change, treat it as a context change in diagnostics.
- Avoid making a filter toggle *look like* a market regime shift:
  - either reset/settle state explicitly (decision needed on what resets),
  - or clearly mark a short “re-initialize” period in the UI/diagnostics.

## 9) Future proofing for non-crypto-native venues (CME / institutional feeds)

Constraints we should assume:

- no public unauthenticated WS; subscription/auth likely required
- session hours may differ from 24/7 crypto
- feeds may be conflated (e.g. 250–500ms snapshots) vs tick prints
- symbols may be dated futures or product codes, not simple `BTCUSDT`
- aggressor side may require inference

Contract implications:

1) keep `instrument_class` separate from `market_type_for_x` so we can represent “futures” without breaking X semantics
   (decision needed when we actually add CME).
2) capability downgrade must be explicit (§4.3). If CME is “unsigned” or “signed-by-rule”, lock it before integration.
3) time hygiene must be logged for every source (exchange ts vs recv ts).
4) session gaps must not corrupt rolling-window assumptions (no backfilling; silence is silence).

## 10) Integration checklist (per new source)

Before merging a new source into the live lens:

- adapter emits base symbols correctly (no venue-specific mapping hacks in the app)
- effort_value unit checked (USD-like) and consistent with existing sources
- aggressor mode documented (native / inferred / none)
- staleness + reconnect behavior acceptable (no silent stalls)
- replay/tuning diagnostics updated to include the new `source_id` in per-source tables
- manual UI sanity pass on BTC + SOL in a volatile window

## 11) Decisions needed (before implementation)

The architecture is mostly clear; the remaining ambiguity is policy defaults.

Open items to resolve before coding:

1) Aggressor inference: what is the canonical “signed-by-rule” method (trade vs BBO, mid-tick rule, etc) and what
   diagnostics prove it is not fabricating direction?
2) Filter reset semantics: if we choose reset mode, what exactly resets per symbol (buffer, smoothing, persistence,
   selector state), and how is the context switch surfaced in diagnostics/UI?
3) Price selector priority policy: how do we set `price_priority` across multi-spot and multi-perp sources while preserving
   “spot preferred” intent without churn?
4) Instrument taxonomy beyond spot/perp: when adding futures (CME/Deribit dated), what is the contract mapping to
   `market_type_for_x` (if any), and does it require a decision record?
5) Quote conversion for non-USD-like feeds: do we postpone entirely, add conversion plumbing, or treat them as separate
   study streams?

## 12) Phase 1 defaults (proposed; to reduce re-litigation)

These are working defaults for the first multi-venue implementation. They are not a decision record, but they should be
treated as “locked unless changed with intent”.

- `AdapterEvent.symbol` remains venue-native instrument id; `base_symbol` is required and used for routing.
- Supported aggressor modes: `native` and `inferred`.
- `aggressor_mode=none` is disabled by default (explicitly rejected unless a decision enables it).
  - If `none` is later enabled, default denominator policy is **Option B** (§4.3.1).
- Canonical inference (if `inferred` enabled): trade vs BBO within a max age budget, with mid/tick fallbacks; if no valid
  BBO, do not infer and treat that print as dropped (logged). No per-adapter custom inference rules.
- Price selector is conservative/sticky and logs all source switches with reason.
- Filter toggles use **reset mode** (explicit context switch), not pseudo-continuous blending.
- Migration safety: add `base_symbol: str | None` first + temporary fallback routing only when `base_symbol` is missing;
  remove fallback after all adapters populate `base_symbol`.

Must-have inference diagnostics (per source, per capture/replay):

- `aggressor_mode` (`native|inferred|none`)
- `% inferred_with_bbo`
- `% inferred_mid_fallback`
- `% inferred_tick_rule_fallback`
- `% unknown_side`
- `bbo_age_ms_p50/p95` at inference time

If `% unknown_side` is high or `bbo_age_ms_p95` is stale, the source fails the Phase 1 gate.

## Dev Notes

### A) `AdapterEvent.symbol` vs `base_symbol` field

Recommendation: add `base_symbol` explicitly and keep `symbol` as venue-native instrument id.

Why this is safer long-term:

- avoids overloading one field with two meanings (native id vs canonical id),
- keeps logs/debugging/replay traceable to actual venue symbols,
- removes hidden mapping assumptions when adding dated futures/options/CME codes,
- makes router/buffer/engine contracts clearer and less fragile.

Minimal compatibility path:

- add `base_symbol` to `AdapterEvent`,
- keep `symbol` unchanged for now,
- route on `base_symbol` immediately,
- deprecate any old mapping helpers once all adapters populate `base_symbol`.

### B) Policy for unsigned sources

Strong recommendation: do not silently merge unsigned effort into directional semantics.

Proposed policy tiers:

1) `native` signed sources: full participation in all metrics.
2) `inferred` signed sources: full participation, but capability-tagged and monitored.
3) `none` (unsigned) sources: contribute only to channels that do not require side direction.

Practical engine policy if `aggressor_mode=none`:

- include in `E_total` and size channel (force intensity),
- include in halo dispersion (participation breadth),
- include in X only if `market_type_for_x` is valid (spot/perp) and we accept unsigned control contribution,
- exclude from `E_dir` and any direction-dependent internals (Y direction chain, provenance color inputs).

Important risk to lock before coding:

- if unsigned effort is included in `E_rate` denominator for Y while excluded from `E_dir`, Y can be artificially muted.
- this must be explicit by decision:
  - either “Y is per total effort (including unsigned)” (conservative but muted),
  - or “Y is per directional-capable effort” (cleaner directionally, but different denominator meaning).

### C) Source registry concerns / improvements

- Make capability flags mandatory for every source (`has_aggressor`, `aggressor_mode`, `quote_mode`, `price_eligible`).
- Add a startup validator that fails fast on incomplete/contradictory source registry entries.
- Add explicit versioning for registry schema to keep replay compatibility as fields evolve.

### D) Price selector risks

- Multi-source selector can create hidden regime artifacts if source switching is too eager.
- Keep hysteresis and sticky selection by default; prefer source continuity over micro-optimizing freshness.
- Log every source switch with reason (`stale`, `priority`, `recovered`) so replay can audit selector behavior.

### E) Filter architecture risk

- Engine filtering is the truthful mode, but symbol/source toggles can look like regime shifts if state is not reset carefully.
- When filter masks change, treat it as a structural context change and clearly log/filter-state in diagnostics.

### F) Recommended near-term implementation order

1) Add `base_symbol` + registry schema + validator (no behavior change).
2) Move routing to `base_symbol` and keep old mapping as temporary fallback.
3) Generalize price selector with explicit source reasons + switch logs.
4) Only then add first non-Binance venue and replay-gate it before UI filter work.

## Quant Notes

The Dev Notes are directionally correct and solve the two “sharp edges” that will otherwise cause repeated instability
debates: symbol identity and denominator meaning.

### 1) Lock `base_symbol` explicitly (agree)

I agree we should add `base_symbol` to `AdapterEvent` and keep `symbol` as venue-native.

Reason: once we add dated futures / institutional codes / options, “symbol” is not a safe carrier for canonical meaning.
Preserving venue-native ids also keeps diagnostics and tournament traces debuggable.

### 2) Unsigned sources: support, but treat as “advanced / later”

I agree with the tiering concept (`native` / `inferred` / `none`), but we should treat `none` as postponed unless there is
a clear venue we *must* integrate that lacks aggressor and cannot be made directional by a mechanical rule.

If we do allow `aggressor_mode=none`, the biggest semantic landmine is §4.3.1:

- Option A (include unsigned in denominators) will mute Y/persistence in precisely the regimes where options/TradFi flows
  might be most relevant (large magnitude, unclear direction).
- Option B (direction-capable denominator) keeps Y semantically aligned to the signed direction chain, but changes the
  meaning of “effort-normalized” to “directional-effort-normalized”.

Recommendation for Flow Lens truth/UX coherence: default to **Option B** if we ever enable unsigned sources, and report
`E_total` vs `E_dir_capable_total` (diagnostics-only) so we can audit whether we are “hiding” magnitude.

### 3) Price selector must be “boring” and explainable

Multi-source price selection is a hidden lever that can create phantom regime shifts.

Requirements I’d add to harden the selector:

- switching uses time-based staleness and conservative hysteresis
- switching is observable: emit switch logs + include “active price source_id” in per-tick diagnostics
- switching is decoupled from effort inclusion (a source can be excluded from price but included in effort, and vice versa)

### 4) Filter changes: truthful but potentially destabilizing

Engine filtering is the correct truth mode, but toggling filters will change:

- rolling window contents used for aggregation,
- smoothing/persistence internal state (if those are computed incrementally).

We need to decide a consistent “context switch” behavior before implementing UI filtering:

- **Reset mode:** clear rolling buffer + reset smoothing/persistence for that symbol when filter mask changes.
- **Settle mode:** keep buffer but mark a short settle period where we expect state to re-converge.

My preference: **Reset mode** for truth and interpretability (it’s more honest to say “new lens context” than to pretend
continuity). If reset is too visually jarring, the UI can annotate it, but the state should not masquerade as continuous.

### 5) Future proofing beyond WS aggTrades

We should assume some “important” sources will not provide ideal trade prints:

- conflated feeds (CME-like)
- missing aggressor
- partial size fields
- session gaps

The registry capability model is the right future-proofing mechanism: we accept capability downgrades explicitly rather
than forcing every venue into the same “aggTrade” mold.

### 6) Additional items to lock before coding

- Diagnostics schema: add `base_symbol`, `source_id`, `aggressor_mode`, `active_price_source_id`, and `price_switch_reason`.
- Validation gates for new sources: “can’t merge unless it passes replay gates on BTC+SOL and doesn’t break tuning metrics”.

## Dev Notes (2)

Quant notes look right. The remaining ambiguity is mostly policy-defaults, not architecture shape.

### 1) Proposed default policy set (to reduce future re-litigation)

If we want a clean first multi-venue implementation without semantic drift, lock these defaults now:

- `AdapterEvent.symbol` remains venue-native id; `base_symbol` is required and used for routing.
- Supported aggressor modes for Phase 1 multi-venue: `native` and `inferred`.
- `aggressor_mode=none` is disabled by default (explicitly rejected unless a decision enables it).
- If `none` is later enabled, default denominator policy is **Option B** (direction-capable effort denominator for Y).
- Price selector is conservative/sticky and must log all source switches with reason.
- Filter toggles use **reset mode** (explicit context switch), not pseudo-continuous blending.

### 2) AdapterEvent migration detail (compatibility safety)

To avoid breakage while adapters migrate:

- add `base_symbol: str | None` first,
- router behavior:
  - use `base_symbol` when present,
  - temporary fallback to legacy symbol mapping only if `base_symbol` missing,
- after all adapters populate `base_symbol`, remove fallback path.

This keeps rollout safe without freezing development.

### 3) Unsigned-source guardrails (if enabled later)

If `aggressor_mode=none` is ever allowed, make it impossible to silently bias semantics:

- require per-source explicit capability in registry (`aggressor_mode=none`),
- log both:
  - `E_total` (all effort),
  - `E_dir_capable_total` (direction-capable effort),
- expose denominator policy in diagnostics header/meta (`y_denominator_policy=A|B`),
- replay gate must include a “Y muting regression” check versus signed-only baseline.

### 4) Price selector lock recommendations

To keep selector behavior explainable and replay-auditable:

- include `active_price_source_id` in every per-tick diagnostic row,
- log switch rows with:
  - `from_source_id`, `to_source_id`,
  - `reason`,
  - `staleness_from_ms`, `staleness_to_ms`,
  - `priority_from`, `priority_to`,
- prefer source continuity unless stale threshold is breached.

This prevents “phantom regime shifts” caused by hidden selector churn.

### 5) Filter context semantics

Reset mode is most truthful and least ambiguous for Flow Lens intent.

Recommended behavior on filter change:

- clear rolling buffer for affected symbol(s),
- reset smoothing/persistence state for affected symbol(s),
- emit a structured diagnostics event (`filter_context_reset`) with old/new mask + timestamp,
- optionally render a short UI settle annotation.

### 6) Extra pre-implementation checks worth adding

- Registry validator should fail on:
  - duplicate `source_id`,
  - missing `market_type_for_x`,
  - invalid capability combinations (e.g., `has_aggressor=false` + `aggressor_mode=native`).
- Add a “source capability table” to replay report header so every run is auditable by source mode.

### 7) Suggested sequence refinement

Before adding first new venue adapter, do a no-new-source plumbing checkpoint:

1) `base_symbol` field added + fallback router path + validator.
2) Diagnostics schema extended (`active_price_source_id`, switch reasons, capability tags).
3) Replay pipeline updated to read/write new fields.
4) Only then integrate venue #2.

## Quant Notes (2)

Dev Notes (2) essentially finish the “policy defaults” layer. I agree with adopting them as the Phase 1 baseline (now
captured in §12) because they eliminate the two biggest future failure modes:

- silent semantic drift (unsigned / denominator ambiguity), and
- hidden selector/filter churn that masquerades as regime change.

Remaining concerns / clarifications to lock before implementation:

1) **Aggressor inference must be standardized and audited.**
   “Inferred” is acceptable only if we lock a single mechanical method and prove its error behavior in diagnostics.
   Otherwise we’ll reintroduce “math says fine, looks wrong” debates via fabricated direction.

2) **Reset mode needs an explicit reset list.**
   Resetting the rolling buffer but not persistence (or vice versa) can create confusing half-resets. We should define a
   canonical “symbol context reset” action:
   - clear buffer window Δ for that symbol
   - reset smoothing state
   - reset persistence state
   - reset price selector active source state
   - emit one structured diagnostics event with old/new filter mask

3) **Price priority needs a stable rule, not ad-hoc per venue.**
   A proposed default rule that is explainable:
   - prefer spot sources for price when fresh
   - within spot, prefer highest `price_priority` (manual ordering) and maintain stickiness
   - fail over to perp only on staleness thresholds + hysteresis
   This keeps behavior boring and consistent with FL-0052’s intent.

4) **Make the “capability table” part of every replay/capture header.**
   This is low effort and prevents post-hoc confusion (“why was Y muted today?” → answer is usually capability/denominator).

If we agree on (1)–(3), the document is “implementation-ready” for Phase 1 multi-venue plumbing.

This catches architecture regressions before they are confounded by feed differences.

## Dev Notes (3)

Quant Notes (2) are on point. I think we are very close to spec-lock. The architecture is ready; we just need to lock a
small set of implementation defaults so teams do not interpret them differently.

### A) Spec-lock recommendation

Recommendation: treat this as implementation-ready once the following are explicitly locked (decision records), then start
Phase 1 plumbing.

1) `base_symbol` contract + migration fallback window.
2) Aggressor inference method + diagnostics acceptance criteria.
3) Filter reset canonical action (full per-symbol context reset list).
4) Price selector priority/hysteresis defaults + switch-reason schema.

### B) Aggressor inference (proposed default method)

To keep “inferred” deterministic and auditable, lock one method for Phase 1:

- Primary: trade vs BBO at event time (or nearest prior BBO within max age budget).
- Mapping:
  - trade >= ask - epsilon -> buy
  - trade <= bid + epsilon -> sell
  - else trade vs mid:
    - trade > mid -> buy
    - trade < mid -> sell
    - trade == mid -> tick rule fallback
- If no valid BBO in age budget -> do not infer (mark unknown for that print; source remains inferred-capable overall).

Do not allow per-adapter custom inference rules in Phase 1.

### C) Inference diagnostics (must-have)

Add per-source diagnostics so we can falsify inference quality quickly:

- `aggressor_mode` (`native|inferred|none`)
- `% inferred_with_bbo`
- `% inferred_mid_fallback`
- `% inferred_tick_rule_fallback`
- `% unknown_side`
- `bbo_age_ms_p50/p95` at inference time

If `% unknown_side` is high or BBO age is stale, source should fail Phase 1 gate.

### D) Filter reset canonical action (exact list)

Lock this as one atomic per-symbol action when filter mask changes:

1) clear rolling buffer window for symbol,
2) reset smoothing state (`X`, `Y` smoothers),
3) reset persistence states (`S_eff`, `S_dir` and controller counters),
4) reset active price source latch/hysteresis state,
5) emit structured `filter_context_reset` diagnostic with old/new mask.

This avoids half-resets that produce misleading transitions.

### E) Price selector default policy (explicit)

Default priority behavior (unless overridden by source registry):

1) prefer fresh spot-eligible sources,
2) within eligible group, use highest `price_priority`,
3) maintain stickiness until staleness threshold breached,
4) fail over to perp only on stale breach + hysteresis,
5) recover back to preferred source only after recovery hysteresis.

Every source switch should emit a reason row and be present in per-tick diagnostics via `active_price_source_id`.

### F) Final call

My view: once A-E are locked as decisions, this doc is good to freeze as Phase 1 spec and begin implementation.

## Quant Notes (3)

Agree with Dev Notes (3): the remaining work is “lock the defaults as decisions”, not invent more architecture.

Two clarifications to keep us honest before we start coding:

1) **BBO dependency is real for inferred aggressor.**
   If we lock the canonical inference method as “trade vs BBO within age budget”, then any `inferred` source must also
   supply BBO updates (directly or via a paired L1 channel) so inference isn’t operating blind. That’s still “dumb” adapter
   work, but it is an explicit workload and connectivity requirement we should budget for.

2) **Unknown-side handling must not quietly become unsigned semantics.**
   Dropping unknown-side prints (and logging them) is the cleanest Phase 1 behavior. If we later decide we want to keep
   their magnitude contribution, that’s a semantic expansion toward “unsigned effort” and should be treated as a decision
   record item (because it touches denominators and Y coherence).

If these two are accepted, the doc is functionally “spec-ready” once the four decision items in Dev Notes (3) are written.

## Dev Notes (4)

I agree with Quant Notes (3). This is spec-ready in structure. Remaining work is locking exact defaults/thresholds so
implementation is deterministic and replay-auditable.

### 1) Owner inputs needed before implementation

These are the only high-impact values I still need you to confirm when we write the decisions:

1) Inference age budget:
   - `bbo_max_age_ms` (max BBO staleness allowed for inference)
2) Inference tie epsilon:
   - whether epsilon is tick-size-based (recommended) or a fixed absolute value
3) Inference fail gate:
   - max acceptable `% unknown_side`
   - max acceptable `bbo_age_ms_p95`
4) Price selector thresholds:
   - stale threshold ms
   - recovery threshold ms / hysteresis requirement
5) Filter reset scope:
   - confirm full reset includes normalization windows and visual state caches (see below)

### 2) One important reset clarification

On filter context reset, do not only reset buffer/smoothing/persistence.
We should also reset per-symbol normalization/visual state to avoid cross-context contamination:

- recent normalization windows (`recent_effort`, `recent_disp_rate`, cached scales),
- halo state/bin hysteresis,
- size-bin hysteresis,
- lean transitional state,
- price selector latch state.

If these are not reset, the first post-toggle windows can look valid but be semantically mixed.

### 3) Determinism note for selector priorities

When multiple sources share equal `price_priority`, lock a deterministic tie-break rule (e.g., lexical `source_id`).
This avoids non-reproducible behavior between live and replay.

### 4) Final readiness call

With the four decisions from Dev Notes (3) plus the numeric defaults in section (1), I consider this ready to freeze as a
Phase 1 implementation spec.

## Dev Notes (5) — Phase 1 Lock Proposal (Draft)

This captures the current owner/dev alignment from the latest review thread.

**owner note:** acknowledged and approved.

### 1) Phase 1 stability over inference breadth

- Prioritize sources with `aggressor_mode=native`.
- `aggressor_mode=inferred` is allowed only when BBO dependency and inference diagnostics are in place.
- Sources without reliable side (`none` or inference failing gate) must not participate in directional calculations.

Directional exclusion means:

- excluded from `E_dir` contribution and any Y/provenance directional chain input,
- may remain observable in diagnostics intake,
- not treated as directional truth until side quality is proven.

### 2) Price selector architecture should be policy-pluggable now

Implement selector behind a policy interface so we can change behavior without refactoring engine/buffer:

- `priority_sticky` (Phase 1 default): explicit priority + staleness + hysteresis.
- `leader_sticky` (Phase 2 candidate): uses tourney/leader scores as preferred target with stickiness.

Do not hardcode one selector strategy in data plumbing.

### 3) Phase 1 selector default behavior

- Keep selector “boring”: sticky, hysteretic, switch-reason logged.
- Preserve current spot-preferred intent unless stale failover conditions are met.
- Add diagnostics fields required for future policy swap:
  - `selector_policy`,
  - `active_price_source_id`,
  - `price_switch_reason`,
  - optional candidate score snapshot when available.

### 4) Future leader mode without lock-in

Leader-based latching is explicitly a later policy, not a refactor:

- once pairwise-tourney evidence is stable, enable `leader_sticky` policy,
- retain hysteresis/cooldown to prevent score-churn flip noise,
- keep replay parity and per-tick selector diagnostics unchanged across policies.

### 5) Readiness statement

If this section matches owner intent, this WIP can be frozen as Phase 1 spec once converted into decision records.

### 6) Proposed numeric defaults (Phase 1)

These values are the recommended Phase 1 defaults to lock in decisions.

#### Inference defaults

- `bbo_max_age_ms = 500`
- `inference_epsilon = 0.5 * tick_size`
- inference gate:
  - `%unknown_side <= 5%` (warn at `3%`)
  - `bbo_age_ms_p95 <= 500ms` (warn at `300ms`)

#### Price selector defaults (`priority_sticky`)

- `stale_failover_ms = 6000`
- `recovery_confirm_cycles = 2`
- deterministic tie-break: `price_priority`, then lexical `source_id`
- switch cooldown guard: `1 update cycle`

#### Filter reset defaults

Filter mask changes trigger full per-symbol context reset:

- rolling buffer window,
- normalization windows/cached scales,
- smoothing states,
- persistence states and controller counters,
- active price source latch/hysteresis state,
- visual bin/hysteresis caches (halo/size/lean transitional state).

#### Unsigned-source defaults

- `aggressor_mode=none` disabled in Phase 1.
- if enabled in future, default denominator policy is Option B (§4.3.1).
