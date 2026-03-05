---
title: "Distribution State Layer (TUI Notes)"
created: 2026-03-04
status: "notes"
source_plan: "docs/reference/dist_state_layer_plan.md"
---

# Distribution State Layer (TUI Notes)

This document captures UI-facing ideas for adding a distribution-state layer to the curses TUI **in addition to** the lens.

The goal is to keep the lens visually and semantically sacred while giving an extra readout of distribution geometry.

## A) Placement and scope

- Single symbol only: show distribution rows for the currently selected symbol.
- Render as a **separate panel** (below the lens), not as overlays on the lens plot.
- Do not reuse lens visual channels (dot x/y/size/halo/lean). This is a second instrument.
- Label the dist panel’s configured source (v1 is single-source; lens price source may differ).

## B) Row structure (multi-timeframe “ribbons”)

The plan proposes 3–4 rows (timeframes). Candidate stack:

- `3m` (micro-intraday)
- `15m` (intraday)
- `1h` (bridge; optional if vertical space is tight)
- `4h` (anchor)

Each row shows the same metric order:

`V | S | A | P | T`

Where `V/S/A/P/T` correspond to:

- V: volatility state (ideally percentile/bounded)
- S: stretch (bounded z-extension)
- A: autocorrelation/persistence bias (bounded)
- P: positioning pressure (bounded; may be missing)
- T: transition pressure (bounded; compression/expansion impulse)

## C) Glyph encoding principles (stability first)

To keep scanability and avoid turning this into a “numbers dashboard”:

- Use coarse bins (3–5 levels) for each metric.
- Apply hysteresis where needed to avoid flicker (same philosophy as the lens).
- Keep encoding identical across rows so the brain learns the pattern.

Example rendering style (illustrative, not binding):

- each metric rendered as 1–2 glyphs of “level” (`▁▂▃▄▅▆▇█`-style) or a compact bar.
- no colors for the ribbon itself (reserve color for semantic tokens, if used).

## D) Row token (optional translation)

The plan suggests an optional per-row token to reduce cognitive translation cost.

Token constraints:

- deterministic mapping from the row metrics (no free-form text),
- small bounded vocabulary,
- visually subordinate to the ribbon metrics.

Proposed token set (per timeframe):

- `COMP`, `EXP`, `CONT↑`, `CONT↓`, `EXH↑`, `EXH↓`, `REVERT`, `NEUT`

Optional modifiers:

- `++` strong
- `!` transition risk

UI rule of thumb:

- ribbon = nuance (measurement)
- token = quick read (translation)

## E) Narrative line (optional, strictly subordinate)

The plan also sketches one global narrative line derived from the row tokens.

To avoid “storytelling drift”:

- narrative must be chosen from a small set of templates,
- it must not contradict row tokens,
- it must be visually muted (e.g., dim color, smaller footprint, placed under rows).

Example template class (illustrative):

- “Local continuation inside higher-TF compression”
- “Expansion attempt propagating upward”
- “Local exhaustion; higher TF intact”
- “Lower TF fighting higher TF”

## F) Update cadence expectations

- Rows update on bar closes per timeframe, not every 30 FPS frame.
- UI should display “last updated” recency per row (optional) to avoid confusing stale higher-TF rows with “frozen” state.

## G) Layout constraints (curses reality)

Because the lens already needs vertical space:

- The distribution panel should degrade gracefully:
  - show fewer rows first (drop `1h` before dropping `4h`),
  - fall back to “metrics only” if tokens/narrative do not fit.

## H) Debug affordances (optional, not default)

If we need tuning visibility without polluting the UI:

- gated “debug view” toggle could display raw numeric values for a single selected row.
- default view remains glyph/bins only (stability + scanability).
