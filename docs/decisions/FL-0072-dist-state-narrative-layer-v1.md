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

## Canonical output contract (v1)

Narrative output is a structured object:

- `narrative_state_id: str` (bounded; interpreter state, not a market “regime label”)
- `narrative_template_id: str` (bounded; maps to a short template string)
- `narrative_params: dict[str, object]` (bounded keys; scalar values only)
- `narrative_as_of_close_ms: int` (driver close time)
- `narrative_driver_tf: str` (e.g. `"15m"`)
- `narrative_started_close_ms: int` (when the current narrative state began)
- `narrative_age_closes: int` (driver closes since start)
- `narrative_reason_codes: list[str]` (bounded; why this narrative is active)
- `narrative_quality_flags: list[str]` (bounded; missingness/conflict flags; e.g. `p_missing_streak`)

Renderer output (non-canonical):

- `narrative_text_template: str` (rendered template string; single sentence)
- reserved for future: `narrative_text_agent: str | None`

## Update rule (v1)

The interpreter must be deterministic and must update on a fixed cadence:

- Evaluate narrative on each **driver timeframe close** using the latest available per-row token snapshots.
- Emit a new narrative output only when the **narrative state changes**, or when a configured “linger reminder” triggers.

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
