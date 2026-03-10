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
- per-row: `ready_core`, `ready_p`, and current `metrics.p` (for quality flags)

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

Define `driver_close_ms` as the `kline_close_ms` timestamp of the driver timeframe close event that triggered the
evaluation.

On each driver close, the interpreter reads the latest token snapshots for all enabled timeframes and computes a
narrative candidate.

### 3.2 Emit rules

On each driver close evaluation, the interpreter must:

- recompute the stack-vector classifier from the latest per-row snapshots, and
- set `narrative_as_of_close_ms = driver_close_ms`.

Emit (for observability and/or downstream consumers that only react to changes) a new narrative output when:

- the computed `narrative_state_id` changes, OR
- a “linger reminder” fires (optional; see §6).

Otherwise, `narrative_state_id`, `narrative_template_id`, `narrative_text_template`, and `narrative_started_close_ms`
are unchanged. `narrative_age_closes` still advances deterministically as-of `narrative_as_of_close_ms`.

## 4) Output contract (binding)

Extend `DistPanelSnapshot` with the following exact narrative fields (binding names):

- `narrative_state_id: str | None`
- `narrative_template_id: str | None`
- `narrative_params: dict[str, NarrativeParamValue]` (empty dict when present; never `None`)
- `narrative_as_of_close_ms: int | None`
- `narrative_driver_tf: DistTimeframe | None`
- `narrative_started_close_ms: int | None`
- `narrative_age_closes: int | None`
- `narrative_reason_codes: list[str]` (empty list when present; never `None`)
- `narrative_quality_flags: list[str]` (empty list when present; never `None`)
- `narrative_text_template: str | None`
- reserved for future:
  - `narrative_text_agent: str | None`

Scalar type (binding):

- `NarrativeScalar = str | int | float | bool | None`
- `NarrativeParamValue = NarrativeScalar | list[str] | dict[str, float]`

## 5) Interpreter structure (v1)

### 5.1 State machine

The narrative interpreter is a small deterministic state machine:

- it produces one `narrative_state_id` at a time,
- it tracks when that state began and how long it has persisted (driver closes).

States are effect-oriented (not “regime labels”). V1 uses a bounded set:

- `N_EXPANSION_ACTIVE`
- `N_COMPRESSION_COILING`
- `N_CONTINUATION_TRYING_UP` / `N_CONTINUATION_TRYING_DOWN`
- `N_EXTENSION_DECAYING_UP` / `N_EXTENSION_DECAYING_DOWN`
- `N_REVERSION_ACTIVE`
- `N_QUIET_NEUTRAL`

### 5.2 Template rendering

V1 narrative text is produced by mapping `narrative_template_id` + params to a single sentence.

No free-form string construction outside templates.

### 5.3 Agent-ready context (future)

The structured narrative output must be sufficient for a future agent to re-render the narrative line:

- include `narrative_age_closes`,
- include a compact per-timeframe summary in params (e.g. `stack_tokens`),
- include quality flags (e.g. OI/P issues) so an agent can comment on reliability.

### 5.4 V1 stack-vector classifier (binding)

Narrative is evaluated on each driver close. Inputs are the *current* token stack at that moment.

Definitions:

- `stack(tf) = (token, token_strength)` for each enabled `DistTimeframe`.
- `present(token)` means token is not `None`.
- narrative classes (v1; bounded): `EXP`, `EXH`, `CONT`, `REVERT`, `COMP`, `NEUT`
- timeframe weights (v1; fixed constants for replay comparability):
  - `w_3m = 1.0`, `w_15m = 1.5`, `w_1h = 2.0`, `w_4h = 2.5`
- strength multipliers (v1 defaults; derived from `token_strength`):
  - base (no `+`): `m = 1.0`
  - `+`: `m = 1.5`
  - `++`: `m = 2.0`
  - `!` does not affect magnitude; it is an instability flag only.
  - if `token_strength` contains `+!` or `++!`, use the `+`/`++` multiplier and record `instability_present=true`.
- class mapping from token:
  - `EXP` → `EXP`
  - `EXH↑/EXH↓` → `EXH`
  - `CONT↑/CONT↓` → `CONT`
  - `REVERT` → `REVERT`
  - `COMP` → `COMP`
  - `NEUT` → `NEUT`

Compute the stack vector (deterministic):

For each timeframe `tf`:

1. If `token(tf)` is `None`: contribute nothing.
2. Else compute:
   - `class(tf)` from token mapping above,
   - `weight(tf) = w_tf * m_tf`
3. Add:
   - `stack_vector[class(tf)] += weight(tf)`

Primary/secondary selection (deterministic):

- `primary_score = max(stack_vector.values())`
- if `primary_score == 0` (all scores 0): emit no narrative and set:
  - `narrative_state_id=None`
  - `narrative_template_id=None`
  - `narrative_text_template=None`
  - `narrative_params={}`
  - `narrative_reason_codes=[]`
  - `narrative_quality_flags=[]`
  - `narrative_as_of_close_ms=driver_close_ms` (the driver close that triggered this evaluation)
  - `narrative_driver_tf=<configured narrative_driver_tf>`
  - `narrative_started_close_ms=None`
  - `narrative_age_closes=None`
  - `secondary_class=None`
  - `secondary_score=0.0`
- else:
  - `primary_class = argmax(stack_vector)`
  - `secondary_class = argmax(stack_vector \ {primary_class})`
- tie-break (equal score): use class precedence (highest wins):
  - `EXP > EXH > CONT > REVERT > COMP > NEUT`

Representative TF (deterministic):

- `representative_tf` is the `tf` in `primary_class` with the highest `weight(tf)`.
- tie-break: larger timeframe wins (`4h > 1h > 15m > 3m`).

Support TFs:

- `support_tfs` are all `tf` where `class(tf) == primary_class`, sorted largest-to-smallest TF.

Direction (for directional primary classes only; deterministic):

- Directional classes: `CONT`, `EXH`.
- Compute over TFs contributing to the primary directional class:
  - `dir_sum = Σ(dir_sign(tf) * weight(tf))`
  - `total_directional_weight = Σ(weight(tf))`
  - `dir_ratio = abs(dir_sum) / total_directional_weight`
- If `total_directional_weight == 0`: `direction=None`.
- Else if `dir_ratio < θ`: `direction=None`.
- Else `direction = sign(dir_sum)`.
- v1 default: `θ = 0.20` (tuning note: `0.15–0.25` may be explored if direction is too quiet/noisy).
  - config name: `narrative_dir_ratio_min` (default `0.20`)

Direction sign mapping:

- `CONT↑` and `EXH↑`: `dir_sign=+1`
- `CONT↓` and `EXH↓`: `dir_sign=-1`

Confidence (dominance margin; deterministic):

- if `primary_score == 0`: `confidence=0.0`
- else: `confidence = (primary_score - secondary_score) / primary_score`

Instability penalty (deterministic):

- Compute `instability_weight = Σ(weight(tf))` over TFs where `token_strength` contains `!`.
- Let `denom = primary_score + secondary_score`; if `denom > 0`:
  - `instability_ratio = clamp(instability_weight / denom, 0, 1)`
  - `confidence *= (1 - 0.2 * instability_ratio)`
- Else no penalty.

Directional conflict flag (observability; not a separate narrative class in v1):

- If the stack contains both `CONT↑` and `CONT↓`, set quality flag `DIR_CONFLICT_CONT`.
- If the stack contains both `EXH↑` and `EXH↓`, set quality flag `DIR_CONFLICT_EXH`.

Mapping:

| Narrative state id | Template id | When (predicate over stack-vector result) | Required params keys |
|---|---|---|---|
| `N_EXPANSION_ACTIVE` | `TPL_EXPANSION_ACTIVE` | `primary_class == EXP` | `representative_tf` |
| `N_EXTENSION_DECAYING_UP` | `TPL_EXTENSION_DECAYING_UP` | `primary_class == EXH` and `direction == UP` | `representative_tf` |
| `N_EXTENSION_DECAYING_DOWN` | `TPL_EXTENSION_DECAYING_DOWN` | `primary_class == EXH` and `direction == DOWN` | `representative_tf` |
| `N_EXTENSION_DECAYING` | `TPL_EXTENSION_DECAYING` | `primary_class == EXH` and `direction == None` | `representative_tf` |
| `N_CONTINUATION_TRYING_UP` | `TPL_CONTINUATION_TRYING_UP` | `primary_class == CONT` and `direction == UP` | `representative_tf` |
| `N_CONTINUATION_TRYING_DOWN` | `TPL_CONTINUATION_TRYING_DOWN` | `primary_class == CONT` and `direction == DOWN` | `representative_tf` |
| `N_CONTINUATION_TRYING` | `TPL_CONTINUATION_TRYING` | `primary_class == CONT` and `direction == None` | `representative_tf` |
| `N_REVERSION_ACTIVE` | `TPL_REVERSION_ACTIVE` | `primary_class == REVERT` | `representative_tf` |
| `N_COMPRESSION_COILING` | `TPL_COMPRESSION_COILING` | `primary_class == COMP` | `representative_tf` |
| `N_QUIET_NEUTRAL` | `TPL_QUIET_NEUTRAL` | `primary_class == NEUT` | `representative_tf` |

Template strings (v1; binding, single sentence):

- `TPL_EXPANSION_ACTIVE`: `"Expansion active ({representative_tf})."`
- `TPL_EXTENSION_DECAYING_UP`: `"Extension decaying ↑ ({representative_tf})."`
- `TPL_EXTENSION_DECAYING_DOWN`: `"Extension decaying ↓ ({representative_tf})."`
- `TPL_EXTENSION_DECAYING`: `"Extension decaying ({representative_tf})."`
- `TPL_CONTINUATION_TRYING_UP`: `"Continuation bias ↑ ({representative_tf})."`
- `TPL_CONTINUATION_TRYING_DOWN`: `"Continuation bias ↓ ({representative_tf})."`
- `TPL_CONTINUATION_TRYING`: `"Continuation bias ({representative_tf})."`
- `TPL_REVERSION_ACTIVE`: `"Reversion active ({representative_tf})."`
- `TPL_COMPRESSION_COILING`: `"Compression coiling ({representative_tf})."`
- `TPL_QUIET_NEUTRAL`: `"Quiet neutral."`

### 5.5 Runner-up surfacing rule (binding)

Narrative must surface **emerging attempts** in addition to dominant structure.

Define:

- `secondary_ratio = secondary_score / primary_score` when `primary_score > 0`, else `0`

Secondary clause inclusion (deterministic):

- include a secondary clause iff all are true:
  - `primary_score > 0`
  - `secondary_score > 0`
  - `secondary_class != "NEUT"`
  - `secondary_ratio >= narrative_secondary_min_ratio`

When included, the renderer must produce a sentence of the form:

- `"{primary_sentence} with {secondary_phrase}."`

Secondary phrase (bounded; deterministic from secondary class + direction when directional):

- `EXP` → `"expansion attempts"`
- `COMP` → `"compression pressure"`
- `REVERT` → `"reversion pressure"`
- `EXH`:
  - `UP` → `"extension decay ↑"`
  - `DOWN` → `"extension decay ↓"`
  - `None` → `"extension decay"`
- `CONT`:
  - `UP` → `"continuation pressure ↑"`
  - `DOWN` → `"continuation pressure ↓"`
  - `None` → `"continuation pressure"`

Secondary direction (deterministic):

- If `secondary_class` is directional (`CONT` or `EXH`), compute `secondary_direction` using the same `dir_ratio` rule as
  for primary (same `θ`), but over TFs contributing to the secondary class.
- Otherwise `secondary_direction=None`.

Template id selection (deterministic):

- If secondary clause is not included, use the base template id from §5.4.
- If secondary clause is included, use the corresponding `_WITH_SECONDARY` template id:
  - `TPL_EXPANSION_ACTIVE_WITH_SECONDARY`: `"Expansion active ({representative_tf}) with {secondary_phrase}."`
  - `TPL_EXTENSION_DECAYING_UP_WITH_SECONDARY`: `"Extension decaying ↑ ({representative_tf}) with {secondary_phrase}."`
  - `TPL_EXTENSION_DECAYING_DOWN_WITH_SECONDARY`: `"Extension decaying ↓ ({representative_tf}) with {secondary_phrase}."`
  - `TPL_EXTENSION_DECAYING_WITH_SECONDARY`: `"Extension decaying ({representative_tf}) with {secondary_phrase}."`
  - `TPL_CONTINUATION_TRYING_UP_WITH_SECONDARY`: `"Continuation bias ↑ ({representative_tf}) with {secondary_phrase}."`
  - `TPL_CONTINUATION_TRYING_DOWN_WITH_SECONDARY`: `"Continuation bias ↓ ({representative_tf}) with {secondary_phrase}."`
  - `TPL_CONTINUATION_TRYING_WITH_SECONDARY`: `"Continuation bias ({representative_tf}) with {secondary_phrase}."`
  - `TPL_REVERSION_ACTIVE_WITH_SECONDARY`: `"Reversion active ({representative_tf}) with {secondary_phrase}."`
  - `TPL_COMPRESSION_COILING_WITH_SECONDARY`: `"Compression coiling ({representative_tf}) with {secondary_phrase}."`
  - `TPL_QUIET_NEUTRAL_WITH_SECONDARY` is not used (NEUT cannot include secondary).

`narrative_params` key set (v1; bounded):

- Unless `narrative_state_id is None`, `narrative_params` must include all of:
  - `primary_class`, `secondary_class`, `primary_score`, `secondary_score`, `confidence`, `direction`,
    `secondary_direction`, `secondary_phrase`, `representative_tf`, `support_tfs`, `stack_vector`.
- For `N_QUIET_NEUTRAL`, `representative_tf` may be `None` and `support_tfs` may be empty.

- `primary_class: str` (one of `EXP|EXH|CONT|REVERT|COMP|NEUT`)
- `secondary_class: str | None` (same set; `None` when `primary_score == 0`)
- `primary_score: float`
- `secondary_score: float`
- `confidence: float`
- `direction: str | None` (`"UP"|"DOWN"|None`)
- `secondary_direction: str | None` (`"UP"|"DOWN"|None`)
- `secondary_phrase: str | None` (one of the bounded phrases in §5.5; `None` if not included)
- `representative_tf: str | None` (one of the enabled timeframes; may be `None` for `NEUT`)
- `support_tfs: list[str]` (supporting TFs, stable ordering: largest-to-smallest)
- `stack_vector: dict[str, float]` (keys are the bounded class set; missing keys must be present with `0.0`)

Quality flags (v1; derived from runtime snapshot only):

- add `P_MISSING_DRIVER` if driver row `ready_p == true` and `metrics.p is None`.
- add `P_MISSING_ANY` if any row has `ready_p == true` and `metrics.p is None`.
- add `DIR_CONFLICT_CONT` / `DIR_CONFLICT_EXH` per conflict detection above.

Metric exception rule:

- if any v1 mapping predicate reads metrics (beyond quality flags/params), it must:
  - add a reason code `METRIC_INFLUENCED_STATE`, and
  - list the metric predicate name in `narrative_reason_codes`.

 

## 6) Linger reminders (optional; v1)

If enabled, the interpreter may emit a reminder event when the narrative state has remained unchanged for a configured
number of driver closes:

- `narrative_linger_reminder_closes` (default `0` = disabled)

Trigger schedule (binding):

- if `narrative_linger_reminder_closes = N > 0`, emit a reminder when
  `narrative_age_closes > 0` and `narrative_age_closes % N == 0`.
- reminders repeat every `N` driver closes until the state changes.

The reminder does not change `narrative_state_id`; it refreshes the output so the TUI (and a future agent) can
incorporate “linger duration” deterministically.

## 7) TUI changes (Phase 3)

Add a single narrative line to the dist-state panel:

- placed directly below the panel header, above the per-row table header, visually subordinate to ribbons.
- fixed-width truncation with ellipsis if needed; never wraps.

Fallback:

- if width or height is constrained, drop the narrative line before dropping any rows.

Deterministic truncation:

- cap rendered narrative to `min(narrative_max_chars, available_width)` characters.
- if truncation is required, replace the final 3 chars with `"..."`.

## 8) Configuration (Phase 3)

Extend `runtime.dist_state` with:

- `narrative_enabled: bool` (default `false`)
- `narrative_driver_tf: str` (default `"15m"`)
- `narrative_linger_reminder_closes: int` (default `0`)
- `narrative_max_chars: int` (default `72`)
- `narrative_secondary_min_ratio: float` (default `0.50`)
- `narrative_dir_ratio_min: float` (default `0.20`)
- reserved for future:
  - `narrative_agent_enabled: bool` (default `false`)

Config invariants (binding; fail-fast):

- `narrative_driver_tf` must be one of the enabled `runtime.dist_state.timeframes`.
- `narrative_linger_reminder_closes >= 0`
- `narrative_max_chars >= 16`
- `0 <= narrative_secondary_min_ratio <= 1`
- `0 <= narrative_dir_ratio_min <= 1`

## 9) Acceptance criteria (Phase 3)

1. Lens outputs unchanged with narrative enabled/disabled.
2. Narrative uses only tokens/metrics (never bins) and does not feed back into computation.
3. Narrative output is deterministic given the same sequence of dist-state snapshots on driver closes.
4. Diagnostics (observability-only) must record narrative emissions as `dist_state_narrative` events including:
   - the structured narrative output fields, and
   - the input token stack used to compute it (so replay attribution is complete).
5. Narrative is evaluated only on driver closes; `narrative_as_of_close_ms` (and derived `narrative_age_closes`)
   advances on each driver close, while `narrative_state_id`/template selection changes only on state changes (plus
   optional linger reminders).
6. Narrative line drops first under TUI layout constraints.
