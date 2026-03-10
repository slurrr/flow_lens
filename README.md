## Flow Lens

Flow Lens is a **single-symbol market structure trading tool**.

It ingests spot + perp trade prints across venues, converts them into a common “effort” representation, and renders a
live structural read of:

- **control** (who is in control),
- **effectiveness** (is effort being accepted or rejected),
- **force** (how intense participation is),
- **dispersion** (how broad participation is),
- **direction of change** (transitional only).

This repository also contains a **distribution-state panel** (dist-state) for *effects-only return distribution
geometry* (V/S/A/P/T), including a deterministic **token layer** and a deterministic cross-timeframe **narrative line**.

The tool is meant to reduce cognitive load for discretionary execution by making structure legible and reviewable.

---

## What You See In The TUI (Current)

### 1) Lens panel (core instrument)

The lens is the dot + channels you already know:

| Question | Visual channel |
|---|---|
| Who is in control? | Dot X |
| Is effort working? | Dot Y |
| How strong is the push? | Dot size |
| How widely distributed is participation? | Halo |
| Is state improving/degrading? | Lean |

Additional orthogonal context layers now present in the lens panel:

- **Persisted effectiveness line** `S_t`: a leaky integrator of instantaneous effectiveness (`Y_raw`) rendered as a
  horizontal line (see `docs/decisions/FL-0050-persisted-effectiveness-line-and-opposition-gauge.md`).
- **Dynamic control baseline line**: breakout-gated, two-speed baseline for X (see
  `docs/decisions/FL-0062-dynamic-control-baseline-line.md`).
- **UTC midnight control anchor tick**: a prior-day anchor for control comparisons (see
  `docs/decisions/FL-0063-utc-midnight-control-anchor-tick.md`).

### 2) Dist-state panel (distribution-state instrument)

Dist-state is a separate panel rendered alongside the lens. It does **not** change lens semantics.

It computes a bounded metric set per timeframe row (currently `3m`, `15m`, `1h`, `4h`) for a single perp-coherent
instrument family (v1: Binance USDT-margined perp):

- `V`: volatility state
- `S`: stretch (z-extension)
- `A`: autocorrelation / short-horizon bias
- `P`: positioning pressure (OI-derived)
- `T`: transition pressure

On top of the metrics, dist-state includes:

- a deterministic **row token** layer (e.g. `COMP`, `EXP`, `CONT↑/↓`, `EXH↑/↓`, `REVERT`, `NEUT`, with `+`, `++`, `!`)
  (`docs/decisions/FL-0071-dist-state-row-tokens-v1.md`),
- a deterministic cross-timeframe **narrative** line that summarizes stack structure via a weighted vector classifier
  (`docs/decisions/FL-0072-dist-state-narrative-layer-v1.md`).

Specs: `SPEC-dist-state-layer-phase1.md`, `SPEC-dist-state-layer-phase2-tokens.md`,
`SPEC-dist-state-layer-phase3-narrative.md`.

---

## Architecture (Current)

### Lens core

```
Live market data (WS trades)
          ↓
Adapters (translate only)
          ↓
Effort events (spot/perp × buy/sell; per-source)
          ↓
Rolling event buffer (window Δ)
          ↓
Engine (normalization + smoothing)
          ↓
Lens state (X/Y/size/halo/lean + persistence + control baseline)
          ↓
TUI renderer
```

### Dist-state panel

```
Perp market data (klines + OI + funding where used)
          ↓
Streaming dist metrics per timeframe row (V/S/A/P/T)
          ↓
Deterministic row tokens
          ↓
Deterministic narrative line
          ↓
TUI renderer (separate panel)
```

Entry point: `src/flow_lens/main.py`.

---

## Running

Create and activate `.venv/`, then:

- install editable: `pip install -e .`
- run: `python -m flow_lens.main --config config/app.toml`

Diagnostics JSONL:

- lens diagnostics: `--dia` → `logs/flow_lens_diagnostics.jsonl`
- dist-state diagnostics: `--dist-dia` → `docs/diagnostics/dist_state_diagnostics.jsonl`

---

## Repo Map

- `src/flow_lens/adapters/`: venue websocket adapters (trade → Event translation)
- `src/flow_lens/engine/`: rolling window buffer + state engine (lens core)
- `src/flow_lens/tui/`: renderer + panels
- `src/flow_lens/dist_state/`: dist-state engine/feed/models
- `config/`: app configs (adapters, symbols, runtime defaults)
- `docs/decisions/`: decision records (binding semantics)
- `SPEC-*.md`: implementation specs
- `docs/reference/`: working notes and internal references

---

## Contribution Contract (Short)

Flow Lens is intentionally narrow. Changes must preserve:

- **single-symbol focus**
- **one meaning per visual channel**
- **rolling window lens core** (no bars/candles in lens adapters/engine)
- **no feedback from dist-state into lens**

All non-trivial semantic changes require a new `docs/decisions/FL-XXXX-*.md`.

## Guiding Question for Contributors

> Does this make the lens more truthful about how force moves through the market?

If not, it probably does not belong.

---

## Project Ethos

Flow Lens is a **physics instrument for market structure**.

Clarity > Complexity  
Structure > Indicators  
Truthful representation > feature count

### Precision and stability (important)

Flow Lens is intended to be a **professional-grade structural lens**.

- **Precision is still the goal**, but precision is meaningless if it cannot be perceived.
- Therefore, **perceptual stability is a prerequisite for precision** (it is not an excuse to avoid precision).
- The project’s correct framing is:
  1) semantic correctness (truth),
  2) perceptual stability (readability of truth),
  3) cross-symbol consistency,
  4) then pursue higher precision without reintroducing instability.
