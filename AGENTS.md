# AGENTS.md — Flow Lens (Repo Guardrails)

## Purpose

Flow Lens is a **single-symbol market structure trading tool**.

The Lens panel exists to answer:

> **Who is in control, and is their effort effective?**

The Distribution State panel exists to answer:

> **What is happening within the distribution geometry (V/S/A/P/T)?**

Everything in this repo must preserve truthfulness.

---

## What This Repo Contains (Current)

Flow Lens is no longer “just the dot”. The TUI contains:

1) **Lens panel (core instrument)** — the dot + sacred visual channels:
   - X (control), Y (effectiveness), size (force), halo (dispersion), lean (transitional direction).

2) **Lens context layers** (orthogonal, explicitly decided):
   - **Persisted effectiveness line** `S_t` (temporal context for Y) (see `docs/decisions/FL-0050-persisted-effectiveness-line-and-opposition-gauge.md`).
   - **Dynamic control baseline line** for X (recent-normal anchor) (see `docs/decisions/FL-0062-dynamic-control-baseline-line.md`).
   - **UTC midnight control anchor tick** (prior-day anchor) (see `docs/decisions/FL-0063-utc-midnight-control-anchor-tick.md`).

3) **Distribution-state panel (dist-state)** — a separate instrument rendered in addition to the lens:
   - V/S/A/P/T metrics per timeframe row (`3m`, `15m`, `1h`, `4h`),
   - deterministic row tokens (bounded vocabulary),
   - deterministic narrative line (stack-vector classifier).

Specs/decisions for dist-state are binding:

- `docs/decisions/FL-0069-distribution-state-layer-v1.md`
- `docs/decisions/FL-0070-open-interest-sampling-contract.md`
- `docs/decisions/FL-0071-dist-state-row-tokens-v1.md`
- `docs/decisions/FL-0072-dist-state-narrative-layer-v1.md`
- `SPEC-dist-state-layer-phase1.md`
- `SPEC-dist-state-layer-phase2-tokens.md`
- `SPEC-dist-state-layer-phase3-narrative.md`

---

## Core Philosophy

Flow Lens models **market structure**, not indicators.

It visualizes:

- **Control** (spot vs perp dominance)
- **Effectiveness** (accepted vs rejected effort)
- **Force magnitude** (participation intensity)
- **Dispersion** (breadth vs concentration)
- **Direction of structural change** (transitional only)

The lens is measurement-first. Summaries (tokens/narrative/reports) must not rewrite the measurement.

---

## Visual Semantics (Do Not Violate)

Each visual channel has exactly one meaning:

| Channel | Meaning |
|---|---|
| Dot position (X) | Control (spot vs perp) |
| Dot position (Y) | Effectiveness (accepted vs rejected) |
| Dot size | Force magnitude (total effort intensity) |
| Halo | Dispersion of contributing effort |
| Lean | Direction of structural change |

No other variable may be encoded into these channels.

---

## Non-Goals

Flow Lens must NOT include:

- automated trade signals
- alerting systems / “if X then do Y” prescriptions
- indicators (RSI, MA, etc.)
- multi-symbol dashboards/scanners
- backtesting/execution components
- bar/candle logic in the **lens core**

---

## Layer Rules (Scope Matters)

### 1) Lens core (sacred)

Scope: adapters → rolling buffer → engine → lens panel.

Rules:

- **Adapters are dumb. Engine is smart.** Adapters translate trades into effort events; interpretation belongs in the engine.
- Rolling-window model only; no bar/candle construction in adapters or engine.
- No historical persistence inside the engine (no DB-like state).
- X/Y/size/halo/lean semantics are sacred.
- Context layers are allowed only when explicitly decided (persistence line, control baseline line).

### 2) Dist-state panel (separate instrument)

Scope: `src/flow_lens/dist_state/`.

Rules:

- Dist-state is rendered **in addition to** the lens; it must not gate/weight/normalize lens computation.
- Perp coherence is required for perp-native inputs (OI/funding).
- Token and narrative layers are **deterministic** and **bounded** (safe to replay and reason about).

### 3) Reporting / research / agent (observer-only; evolving)

Scope: rollups, event logs, daily reports, non-deterministic agent commentary.

Rules:

- Must not modify or gate lens/dist-state computations.
- Persistence is allowed here (e.g. JSONL) because it is observability/reporting, not engine state.

---

## Adapter Contract (Binding)

Adapters must emit one `Event` per trade/print (see `src/flow_lens/models/event.py`) including:

- `timestamp` (recv-time; canonical timebase)
- `source_id`
- `side_type` (`spot` or `perp`)
- `aggressor_side` (`buy` or `sell`)
- `effort_value` (normalized input to the engine; non-negative)
- `price`
- optional `venue_timestamp_ms`, `trade_id` (for hygiene/diagnostics)

Adapters must not:

- normalize beyond producing `effort_value`
- compute dominance/effectiveness/dispersion
- store history

---

## Design Priority Order

1. Semantic correctness
2. Visual stability
3. Cross-source consistency
4. Performance
5. Code elegance

Never sacrifice (1)–(3) for (4) or (5).

---

## Development Environment (Invariant)

- A local virtual environment at `.venv/` is mandatory
- Install in editable mode before development: `pip install -e .`

---

## Tooling & Style (Binding)

All Python code MUST adhere to:

- ruff (repo config)
- pyright (repo config)

Rules:

- Code that violates ruff or pyright is invalid.
- Do not add ignores/suppressions without explicit approval.

Execution constraint after implementation phases:

- `ruff check .`
- `scripts/pyright.sh`

---

## Change Discipline

- No speculative refactors
- No “helpful” generalizations
- No semantic interpretation without a spec + decision where required

When rules are unclear, do not guess: ask and capture a decision.

