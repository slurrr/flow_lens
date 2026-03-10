---
id: FL-0072
title: "Dist-State Narrative Layer (V1): Structured Interpreter Output, Optional Agent Override"
status: Proposed
created: 2026-03-09
related:
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
  - "docs/decisions/FL-0071-dist-state-row-tokens-v1.md"
  - "SPEC-dist-state-layer-phase2-tokens.md"
---

# FL-0072 — Dist-State Narrative Layer (V1): Structured Interpreter Output, Optional Agent Override

## Decision

Add an optional **narrative interpretation layer** for the dist-state panel.

This layer is a **structured interpreter** over already-computed dist-state outputs. It produces a single short
“narrative sentence” for the operator, but its canonical output is **structured data** (template id + params + state
metadata) so a future agent can reason over it deterministically.

The narrative layer:

- consumes **row tokens first** for narrative state selection, and may consume **row metrics** only to
  parameterize/explain and attach quality flags (never bins),
- is output-only (does not feed back into any metric or the Flow Lens engine),
- uses a bounded vocabulary (no free-form text generation in v1),
- updates only when the narrative state changes (plus optional “linger” reminders).

V1 guardrail (token-first, metric-light):

- Default rule: `narrative_state_id` is selected from the token stack + readiness/quality.
- Metrics are primarily for params/flags; avoid duplicating token logic inside narrative.
- If a specific narrative state truly requires metrics (rare), it must be an explicit, bounded exception in the Phase 3
  mapping and must be called out as metric-influenced (so it is auditable in replays).

Stack-vector aggregation (v1):

- V1 narrative selection summarizes the stack via a weighted class vector:
  - primary class = argmax(weighted stack vector)
  - secondary class (runner-up) is always recorded to surface emerging shifts
- This prevents higher-timeframe “dominance” from hiding lower-timeframe attempts while remaining deterministic.

Runner-up surfacing (v1):

- If the runner-up is sufficiently strong relative to the primary class, v1 template rendering may include an explicit
  secondary clause (e.g., “Compression coiling … with expansion attempts.”) to surface emerging shifts without an agent.
- Runner-up is always recorded structurally (e.g. `secondary_class`, `secondary_score`) even when the secondary clause is
  not rendered.

Metric exception attribution (binding):

- Any metric-influenced narrative-state selection must add a reason code `METRIC_INFLUENCED_STATE` and include the metric
  predicate name in `narrative_reason_codes`.

## Canonical output contract (v1)

Narrative output is a structured object:

- `narrative_state_id: str | None` (bounded; interpreter state, not a market “regime label”; `None` means no narrative)
- `narrative_template_id: str | None` (bounded; maps to a short template string; `None` when no narrative)
- `narrative_params: dict[str, NarrativeParamValue]` (bounded keys; values are bounded and typed)
- `narrative_as_of_close_ms: int | None` (driver close time; `None` until first driver evaluation)
- `narrative_driver_tf: str | None` (e.g. `"15m"`; `None` until configured/enabled)
- `narrative_started_close_ms: int | None` (when the current narrative state began; `None` when no narrative)
- `narrative_age_closes: int | None` (driver closes since start; `None` when no narrative)
- `narrative_reason_codes: list[str]` (bounded; why this narrative is active)
- `narrative_quality_flags: list[str]` (bounded; missingness/conflict flags; e.g. `p_missing_streak`)

Param value types (binding):

- `NarrativeScalar = str | int | float | bool | None`
- `NarrativeParamValue = NarrativeScalar | list[str] | dict[str, float]`

Structured params (binding):

- Phase 3 explicitly permits small structured values in `narrative_params` when the mapping requires them (e.g.
  `support_tfs: list[str]`, `stack_vector: dict[str, float]`).

Renderer output (non-canonical):

- `narrative_text_template: str | None` (rendered template string; single sentence; `None` when no narrative)
- reserved for future: `narrative_text_agent: str | None`

## Update rule (v1)

The interpreter must be deterministic and must update on a fixed cadence:

- Evaluate narrative on each **driver timeframe close** using the latest available per-row token snapshots.
- Advance `narrative_as_of_close_ms` (and derived `narrative_age_closes`) on each driver close evaluation.
- Emit/log a new narrative output only when the **narrative state changes**, or when a configured “linger reminder”
  triggers.

## Agent-ready extension point (future)

V1 must preserve a stable structured narrative schema so a future agent can:

- consume the structured narrative output + per-row token/metric context,
- emit an alternate single-sentence narrative (`narrative_text_agent`) while keeping the structured fields intact,
- optionally incorporate “linger duration” into its message.

V1 must not require an agent to be useful; template rendering must stand alone.

## Rationale

- Tokens reduce per-row translation load; narrative reduces cross-row translation load.
- A structured template id + params is deterministic, replayable, and safe to evolve via decisions.
- Keeping metrics optional for parameterization avoids coupling narrative to rendering bins while still allowing richer
  explanation later.
