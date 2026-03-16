# Questions the Agent Layer should be able to answer

---

Status key used below:

- `Yes`: answerable from the liquidity rollup spec plus the plan's persisted cross-layer event/state log.
- `Partial`: likely answerable, but only if the plan fills a missing join/definition not nailed down in the rollup spec.
- `No`: not answerable yet from the current spec + plan because a required field/contract is missing.

### Liquidity Structure

- When **buyers dominate liquidity**, how often does price continue vs revert?
  Answerable: `Yes` — `liquidity_interval.effort_dir_net`, price returns, acceptance/rejection state, and multi-interval follow-through are available from rollups.
- Does **acceptance after aggression** lead to continuation more often than rejection?
  Answerable: `Yes` — aggression comes from event-summed effort, acceptance/rejection from `y_state` rollup counters, and continuation can be measured with subsequent interval returns.
- What happens when **aggression increases but acceptance falls**?
  Answerable: `Yes` — compare changes in effort measures versus `mean_y` / accept-share across consecutive rollups.
- Does **persistent dominance** across multiple snapshots produce larger moves?
  Answerable: `Yes` — persistence can be measured over consecutive rollups using effort dominance or `mean_x`, then joined to cumulative forward returns.
- Are **dominance flips** early indicators of regime change?
  Answerable: `Yes` — dominance flips are recoverable from rollup sequences (`mean_x`, `effort_control_net`, sign-flip counters) and can be tested against later state transitions or returns.
- How often does **force without acceptance** precede reversals?
  Answerable: `Yes` — high effort with weak/negative acceptance is directly measurable from rollups, then test against subsequent reversal windows.
- Does **balanced force** correlate with compression regimes?
  Answerable: `Partial` — balanced force is in rollups, but "compression regimes" depends on persisted dist-state token/narrative output from the higher-level plan, not the rollup spec alone.

---

### POC Behavior

- Does **POC drift direction** predict price direction?
  Answerable: `Yes` — interval `price_poc` gives POC location and drift can be compared to later interval returns.
- How often does **POC migration lead price** vs follow price?
  Answerable: `Yes` — both POC movement and interval price movement are persisted, so lead/lag tests are possible.
- Does **large POC drift** correlate with continuation?
  Answerable: `Yes` — drift magnitude is derivable from sequential POC values and can be compared to forward returns or acceptance persistence.
- Does **stable POC** correlate with compression regimes?
  Answerable: `Partial` — stable POC is covered by rollups, but "compression regimes" again requires dist-state token/narrative persistence from the agent-layer plan.
- What happens when **price moves but POC stays anchored**?
  Answerable: `Yes` — rollups preserve both price return and POC movement, so this mismatch is directly queryable.
- What happens when **POC moves but price does not**?
  Answerable: `Yes` — same as above in the opposite direction.
- Does **POC velocity** predict expansion events?
  Answerable: `Partial` — POC velocity is derivable from rollups, but "expansion events" depends on dist-state token/narrative events being logged alongside liquidity state.
- How often does **POC migration stall before reversals**?
  Answerable: `Yes` — stall can be defined from decelerating POC drift across rollups, then tested against later reversal windows.

---

### Spot vs Perp Dynamics

- Do **spot-led POC migrations** lead to more stable trends than perp-led moves?
  Answerable: `Yes` — the spec stores `price_poc` by instrument (`spot`, `perp`), making lead-source comparisons possible.
- Does **perp dominance** produce more failed continuation attempts?
  Answerable: `Yes` — perp dominance is observable from control/effort fields and failed continuation can be measured from weak acceptance plus subsequent reversal.
- Does **spot/perp divergence** precede narrative transitions?
  Answerable: `Partial` — divergence is measurable from segmented liquidity/POC data, but "narrative transitions" requires the plan's persisted narrative events and a clear join to rollups.
- What happens when **perp flow pushes price but spot POC stays flat**?
  Answerable: `Yes` — perp flow, price change, and spot-only POC movement are all represented in the planned liquidity dataset.
- Are **large perp-driven POC shifts** more likely to revert?
  Answerable: `Yes` — the segmented POC histograms support this directly.

---

### Liquidity vs Narrative Interaction

- When **continuation narrative forms**, does liquidity already show dominance?
  Answerable: `Partial` — likely yes if narrative state changes are persisted in the agent-layer event log, but the liquidity rollup spec alone does not define that combined join surface.
- How often does **liquidity dominance lead narrative changes**?
  Answerable: `Partial` — requires synchronized persistence of rollups with narrative-state events and a defined lead/lag query approach.
- How often does **narrative shift before liquidity confirmation**?
  Answerable: `Partial` — same dependency as above.
- Does **compression narrative + rising liquidity imbalance** predict expansion?
  Answerable: `Partial` — answerable only if compression/expansion states from dist-state are logged in a joinable way with rollup intervals.
- Do **expansion narratives fail when liquidity acceptance is weak**?
  Answerable: `Partial` — weak liquidity acceptance is covered, but this still depends on persisted expansion-state events from dist-state.

---

### Narrative Structure

- Which **narrative states occur most often**?
  Answerable: `Yes` — if the plan persists `narrative_state`, simple frequency analysis is enough.
- Which **narrative transitions occur most often**?
  Answerable: `Yes` — same requirement; transitions are observable from ordered narrative-state events.
- Which **transitions lead to the largest moves**?
  Answerable: `Partial` — likely yes, but only if narrative-state events are joined to rollup price outcomes or another persisted market-context return series.
- How often does **COMP → EXP → CONT** occur?
  Answerable: `Yes` — provided the token/narrative state stream is actually persisted.
- How often does **EXP fail and revert to COMP**?
  Answerable: `Yes` — same as above.
- Which narratives have the **highest continuation probability**?
  Answerable: `Partial` — requires a defined continuation outcome measure joined to narrative-state intervals; feasible, but not fully specified in the plan.

---

### Narrative Confidence

- Do **low-confidence narratives** precede regime shifts?
  Answerable: `No` — the plan references `confidence`, but the current rollup spec and the narrative decision/specs shown here do not define a canonical confidence field or how it is persisted.
- Does **confidence expansion** correlate with price expansion?
  Answerable: `No` — same gap: no locked confidence metric/output contract.
- Do **low-confidence states** correlate with chop?
  Answerable: `No` — same gap.
- Are **large moves preceded by falling confidence**?
  Answerable: `No` — same gap.

---

### Narrative Drift

- Does **secondary\_class gaining weight** precede regime flips?
  Answerable: `Yes` — the plan explicitly calls for persisting `stack_vector`, `primary_class`, and `secondary_class`, which is enough for this analysis.
- How often does **runner-up class become dominant**?
  Answerable: `Yes` — same data surface as above.
- Does **vector drift speed** predict expansion?
  Answerable: `Partial` — drift speed can be derived from sequential `stack_vector` snapshots, but "expansion" still depends on a consistent event/state join to dist-state outcomes.
- How long does **narrative drift typically last before resolution**?
  Answerable: `Partial` — likely answerable if narrative drift/resolution are formally defined in analysis; the plan describes drift conceptually but does not define a canonical drift episode contract.

---

### Liquidity + Value Interaction

- What happens when **POC drifts up but liquidity rejects price**?
  Answerable: `Yes` — POC drift and rejection/negative effectiveness are both present in the rollups.
- What happens when **POC and liquidity both move in the same direction**?
  Answerable: `Yes` — same.
- Does **value migration without liquidity dominance** fail more often?
  Answerable: `Yes` — POC migration and weak dominance can be combined directly from rollups.
- Does **liquidity dominance without value migration** stall price?
  Answerable: `Yes` — same.

---

### Structural Timing

- How long does **compression typically last before expansion**?
  Answerable: `Partial` — requires persisted compression/expansion state from dist-state and a definition of episode boundaries; feasible, but outside the rollup spec itself.
- How long does **continuation typically persist**?
  Answerable: `Partial` — same dependency if "continuation" is a narrative/token state; `Yes` only if using liquidity acceptance instead of dist-state continuation.
- What is the **average lifetime of each narrative state**?
  Answerable: `Yes` — if `narrative_state` changes are persisted with timestamps.
- Which **states resolve fastest**?
  Answerable: `Yes` — same.
- Which **states persist longest**?
  Answerable: `Yes` — same.

---

### Regime Sequences

- Which **three-state sequences** occur most often?
  Answerable: `Yes` — provided the narrative/token stream is logged.

Examples:

- COMP → EXP → CONT
- COMP → EXP → REVERT
- CONT → EXH → REVERT

Questions:

- Which sequences produce the **largest directional moves**?
  Answerable: `Partial` — needs sequence extraction from narrative logs plus a defined move outcome joined from rollups or market context.
- Which sequences **fail most often**?
  Answerable: `Partial` — same; "fail" needs an explicit research definition even though the data should be sufficient.

---

### Liquidity Exhaustion

- Does **rising aggression with falling acceptance** predict exhaustion?
  Answerable: `Yes` — directly supported by rollup effort and acceptance fields.
- Does **falling volume during continuation** predict reversal?
  Answerable: `Partial` — falling "volume" can be proxied by effort and reversal by later returns, but "continuation" depends on whether you mean liquidity acceptance or dist-state continuation state.
- Does **POC migration slowing during continuation** precede exhaustion?
  Answerable: `Partial` — POC slowing is available, but "during continuation" again depends on a clean narrative/token join and a formal exhaustion outcome.

---

### Agent-Specific Research Questions

Questions an agent can attempt to answer:

- What **structural condition usually precedes major moves**?
  Answerable: `Yes` — the combined dataset should support this, assuming "major move" is defined in research.
- What **liquidity pattern precedes narrative transitions**?
  Answerable: `Partial` — requires the cross-layer joined persistence surface; feasible, but not fully specified in the rollup spec.
- Which **structural configurations are unusual**?
  Answerable: `Partial` — the stored data is likely sufficient, but "unusual" requires an explicit rarity/scoring method not defined in the current plan.
- When does **structure disagree with price**?
  Answerable: `Yes` — rollups contain both structure and price outcomes.
- What **conditions precede regime instability**?
  Answerable: `Partial` — possible in principle, but "regime instability" is not yet a locked deterministic construct and likely depends on missing confidence/drift definitions.

---

### Higher-Level System Questions

Once sufficient history exists:

- What **market structures precede volatility expansion**?
  Answerable: `Partial` — answerable if dist-state expansion states or another volatility-expansion label are persisted in a joinable way.
- What **structures precede directional trends**?
  Answerable: `Yes` — directional trend can be defined from forward returns using the planned rollup history.
- What **structures produce the most false signals**?
  Answerable: `Partial` — feasible, but "signal" is not a native system concept here, so this requires a research definition of candidate structure calls and failure.
- Which **structural signals lead price the most consistently**?
  Answerable: `Partial` — feasible only after defining the candidate structural lead conditions; the data should be enough, but the concept is not specified yet.
