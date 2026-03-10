---
id: FL-0071
title: "Dist-State Row Tokens (V1): Bounded Vocabulary, Deterministic Mapping, Subordinate to Ribbons"
status: Proposed
created: 2026-03-06
related:
  - "docs/decisions/FL-0069-distribution-state-layer-v1.md"
  - "docs/decisions/FL-0070-open-interest-sampling-contract.md"
  - "SPEC-dist-state-layer-phase1.md"
---

# FL-0071 — Dist-State Row Tokens (V1): Bounded Vocabulary, Deterministic Mapping, Subordinate to Ribbons

## Decision

Add an optional **per-row token** for the dist-state panel that acts as a **bounded translation layer** on top of the
existing `V/S/A/P/T` ribbons.

Tokens are not a score, signal, or alert. They are a deterministic re-encoding of the same already-displayed metrics.

The token layer must remain visually subordinate to the ribbon glyphs and must not feed back into the Flow Lens engine.

## Vocabulary (v1; locked)

Per timeframe row, token must be one of:

- `COMP` (compression)
- `EXP` (expansion)
- `CONT↑`, `CONT↓` (continuation bias, directional)
- `EXH↑`, `EXH↓` (exhaustion / decay risk at extension, directional)
- `REVERT` (mean reversion bias)
- `NEUT` (explicit quiet-neutral)

Optional modifiers (v1; locked):

- strength: `+`, `++`
- transition risk: `!`

Representation contract:

- `token: str | None` contains the base token (one of the set above) or `None`.
- `token_strength: str | None` contains either `None`, `"+"`, `"++"`, `"!"`, `"+!"`, or `"++!"`.
- Vocabulary guard: unknown token strings must be prevented at generation time; controlled by a runtime flag (see Phase 2
  spec) to either fail-fast or map to `None` and log.

NEUT vs `None` canonical meaning:

- `NEUT` means “explicit quiet-neutral state” (an explicit neutral predicate is true).
- `None` means “no token predicate matched” (leave the row unlabelled; ribbons remain the nuance).

## Deterministic mapping contract (v1)

Inputs:

- mapping uses continuous, bounded row metrics (`DistRowMetrics`) as inputs:
  - `V` in `[0,1]`
  - `S`, `A`, `T`, `P` in `[-1,1]` (when present)
- display bins are for rendering only; token classification must not be performed from display bins.
- token logic may use minimal per-row state:
  - previous metric values (prior close),
  - previous token and a hold counter (for dwell / hysteresis).

Output cadence:

- token updates only on row close updates (same cadence as the row metrics).

Missingness:

- Structural token selection must not depend on `P` availability (because `P` can be missing under OI tolerance breaches).
- `P` may contribute only as a modifier when present; it must never block token assignment.
- if `ready_core == false`, token is `None`.
- if `ready_core == true` and no token predicate matches, emit `token = None` (no token).
- `NEUT` is reserved for an explicit neutral predicate (defined below); it must not be used as a catch-all default.

Mode clarity:

- `strict` vs `continuous` OI mode does not change token math. OI affects only whether `P` is present for modifiers.

## Mapping rules (v1; locked starter)

This starter mapping is intentionally conservative and stable; it is designed to be revised by a later decision record if
it proves unhelpful.

Token precedence (binding; first match wins):

1. `EXP`
2. `EXH↑/EXH↓`
3. `CONT↑/CONT↓`
4. `REVERT`
5. `COMP`
6. `NEUT`

State inputs (structural; `P` is excluded):

- Structural state is determined from `V`, `S`, `A`, `T` only.
- `P` is modifier-only (confirmation/divergence), never state-determining.

Directional inference:

- `S_dir` is derived from `S` with a neutral deadband:
  - up if `S >= s_dir_deadband`
  - down if `S <= -s_dir_deadband`
  - neutral otherwise

Stability controls (required):

- Use hysteresis for state predicates (enter vs exit thresholds).
- Enforce a minimum dwell (`token_min_hold_bars`) before flipping tokens, except when a higher-precedence token becomes
  true (EXP/EXH may override immediately).

V1 starter predicates (conceptual; numeric thresholds belong in the Phase 2 spec):

- `EXP`: expansion impulse is present (high `T`).
- `EXH↑/↓`: extension is extreme (high `|S|`) and instability is present (reversion bias or compression impulse), not in
  `EXP`.
- `CONT↑/↓`: persistence is strong (high `A`), direction is known, not extended, not in `EXP`.
- `REVERT`: reversion bias is strong (low `A`) with some stretch present (not flat noise), not in `EXP`.
- `COMP`: compression impulse (low `T`) and low volatility state (low `V`), not in `EXP`.
- `NEUT`: explicit “quiet neutral” state (all core metrics within neutral bands; see Phase 2 spec).
- else no token (`token=None`).

Directional token clarity:

- A row token is always a single string. It is never “up and down at the same time”.
- Notation like `CONT↑/↓` in this document means “one of `CONT↑` or `CONT↓` depending on `S_dir`”.

Modifier rules:

- Apply modifiers after structural token selection.
- Strength (`+` / `++`) is derived from distance-from-neutral of the controlling structural metrics for the chosen token.
- `P` modifies strength/risk only when present:
  - for `CONT`/`EXP`, `P` confirming the direction strengthens; `P` diverging adds `!`.
  - for `EXH`, `P` diverging strengthens exhaustion; `P` confirming adds `!` (risk of re-acceleration).
- `!` may also be applied for structural transition risk based on 1-step deltas (e.g., `T_delta` rising out of compression;
  exact predicate in Phase 2 spec).

Modifier merge / assembly (deterministic):

- `token_strength` is assembled as strength first, then risk: `"+"`, `"++"`, `"!"`, `"+!"`, `"++!"`.
- `!` must not be duplicated.

Order rationale (v1; binding intent):

- The token is a dominant effect callout. Ribbons remain the nuance.
- Token precedence is information-hierarchical: volatility expansion overrides directional logic; extreme stretch overrides
  continuation; continuation overrides mild reversion; structural environments come after active behavior.

## Rationale

- Bounded tokens reduce cognitive translation load without converting Flow Lens into a narrative/signal engine.
- Consuming continuous bounded metrics (not display bins) matches the intent: bins are for rendering; tokens read the
  smoothed state and then apply hysteresis/dwell to prevent churn.
- A conservative starter mapping prevents “storytelling drift”; the ribbon remains the truth source.
