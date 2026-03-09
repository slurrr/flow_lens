---
title: "SPEC — Distribution State Layer (Phase 3: Narrative Interpreter + TUI Line)"
created: 2026-03-09
status: "draft"
related:
  - "SPEC-dist-state-layer-phase1.md"
  - "SPEC-dist-state-layer-phase2-tokens.md"
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
  - "docs/decisions/FL-0071-dist-state-row-tokens-v1.md"
  - "docs/decisions/FL-0072-dist-state-narrative-layer-v1.md"
---

# SPEC — Distribution State Layer (Phase 3: Narrative Interpreter + TUI Line)

Phase 3 adds a **narrative interpreter** that summarizes the current dist-state panel into a single short sentence,
without changing the underlying metrics or token logic.

## 0) Goals

1. Produce a deterministic, bounded narrative output from dist-state row snapshots.
2. Render the narrative as a single line in the dist-state TUI panel.
3. Preserve an agent-ready structured output contract for future free-form narrative overrides.

## 1) Non-goals

1. No Flow Lens engine changes.
2. No trading signals, advice, alerts, or scoring.
3. No free-form generation in v1 (template rendering only).
4. No consumption of display bins for inference.
5. Narrative does not feed back into any computation.

## 2) Inputs (binding)

Narrative consumes:

- per-row: `DistRowSnapshot.token`, `DistRowSnapshot.token_strength`
- per-row: `DistRowSnapshot.metrics` (optional; for parameterization only)
- per-row: `ready_core`, `ready_p`, and `P` missingness flags if present in diagnostics

Narrative must not consume:

- `DistRowSnapshot.bins` (rendering-only)

Token-first rule:

- Default rule: narrative state selection (`narrative_state_id`) is a function of token stream + readiness/quality.
- Metrics are primarily for parameters/quality flags; do not duplicate token logic inside narrative.
- If a specific narrative state truly requires metrics, it must be an explicit, bounded exception in the Phase 3 mapping
  (so replays can attribute narrative selection to a metric predicate deterministically).

## 3) Cadence and determinism

### 3.1 Driver timeframe

Narrative evaluation is triggered only on the close of a configured driver timeframe:

- `narrative_driver_tf` (default `"15m"`)

On each driver close, the interpreter reads the latest token snapshots for all enabled timeframes and computes a
narrative candidate.

### 3.2 Emit rules

Emit a new narrative output when:

- the computed `narrative_state_id` changes, OR
- a “linger reminder” fires (optional; see §6).

Otherwise narrative output is unchanged.

## 4) Output contract (binding)

Extend `DistPanelSnapshot` with a narrative payload (names may be adjusted to match code conventions; structure is
binding):

- `narrative_state_id: str | None`
- `narrative_template_id: str | None`
- `narrative_params: dict[str, object] | None`
- `narrative_as_of_close_ms: int | None`
- `narrative_driver_tf: str | None`
- `narrative_started_close_ms: int | None`
- `narrative_age_closes: int | None`
- `narrative_reason_codes: list[str] | None`
- `narrative_quality_flags: list[str] | None`
- renderer-only:
  - `narrative_text_template: str | None`
  - reserved: `narrative_text_agent: str | None`

## 5) Interpreter structure (v1)

### 5.1 State machine

The narrative interpreter is a small deterministic state machine:

- it produces one `narrative_state_id` at a time,
- it tracks when that state began and how long it has persisted (driver closes).

States are effect-oriented (not “regime labels”). Example shape (bounded set; exact ids belong in a Phase 3 mapping
decision record):

- `N_EXPANSION_ACTIVE`
- `N_COMPRESSION_COILING`
- `N_CONTINUATION_TRYING_UP` / `N_CONTINUATION_TRYING_DOWN`
- `N_EXTENSION_DECAYING_UP` / `N_EXTENSION_DECAYING_DOWN`
- `N_REVERSION_ACTIVE`
- `N_MIXED_STACK`
- `N_QUIET_NEUTRAL`

### 5.2 Template rendering

V1 narrative text is produced by mapping `narrative_template_id` + params to a single sentence.

No free-form string construction outside templates.

### 5.3 Agent-ready context (future)

The structured narrative output must be sufficient for a future agent to re-render the narrative line:

- include `narrative_age_closes`,
- include a compact per-timeframe summary in params (e.g. `stack_tokens`),
- include quality flags (e.g. OI/P issues) so an agent can comment on reliability.

## 6) Linger reminders (optional; v1)

If enabled, the interpreter may emit a reminder event when the narrative state has remained unchanged for a configured
number of driver closes:

- `narrative_linger_reminder_closes` (default `0` = disabled)

The reminder does not change `narrative_state_id`; it refreshes the output so the TUI can display “still … (N closes)”
if desired, and so a future agent can optionally generate longer-duration insights.

## 7) TUI changes (Phase 3)

Add a single narrative line to the dist-state panel:

- placed below the panel header and above row lines (or at the bottom), visually subordinate to ribbons.
- fixed-width truncation with ellipsis if needed; never wraps.

Fallback:

- if width or height is constrained, drop the narrative line before dropping any rows.

## 8) Configuration (Phase 3)

Extend `runtime.dist_state` with:

- `narrative_enabled: bool` (default `false`)
- `narrative_driver_tf: str` (default `"15m"`)
- `narrative_linger_reminder_closes: int` (default `0`)
- reserved for future:
  - `narrative_agent_enabled: bool` (default `false`)

## 9) Acceptance criteria (Phase 3)

1. Lens outputs unchanged with narrative enabled/disabled.
2. Narrative uses only tokens/metrics (never bins) and does not feed back into computation.
3. Narrative output is deterministic across replays given the same dist-state diagnostics.
4. Narrative updates only on driver closes and only when state changes (plus optional linger reminders).
5. Narrative line drops first under TUI layout constraints.
