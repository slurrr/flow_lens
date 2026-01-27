# AGENTS.md – Flow Lens

## Purpose

Flow Lens is a **single-symbol structural flow diagnostic**.  
It is not a dashboard, signal generator, scanner, or trading system.

The system exists to answer one question:

> **Who is in control, and is their effort effective?**

All design, code, and contributions must preserve this constraint.

---

## Core Philosophy

Flow Lens models **market structure**, not indicators.

It visualizes:

- **Control** (spot vs perp dominance)
- **Effectiveness** (is effort moving price)
- **Force magnitude**
- **Dispersion of effort**
- **Direction of structural change**

The system is a *lens*, not an opinion engine.

---

## Visual Semantics (Do Not Violate)

Each visual channel has exactly one meaning.

| Channel | Meaning |
|--------|--------|
| Dot position (X) | Control (spot vs perp) |
| Dot position (Y) | Effectiveness (accepted vs rejected) |
| Dot size | Force magnitude (dominance) |
| Halo | Dispersion of contributing effort |
| Lean | Direction of structural change |

No other variable may be encoded in these channels.

---

## Non-Goals

Flow Lens must NOT include:

- Scores
- Trade signals
- Alerts
- Indicators (RSI, MA, etc.)
- Multi-symbol dashboards
- Historical chart overlays
- Candle/bar logic

If a feature resembles a trading system component, it does not belong here.

---

## Architectural Principles

1. **Adapters are dumb. Engine is smart.**  
   Adapters ingest data and output effort events. All interpretation occurs in the engine.

2. **Rolling window model.**  
   All state derives from the active window Δ. No bar-based logic.

3. **Normalization before visualization.**  
   All variables must be dimensionless and bounded before smoothing.

4. **Orthogonality.**  
   No visual channel may encode more than one semantic dimension.

5. **Perceptual stability over precision.**  
   The lens favors stable regimes over micro-fluctuations.

---

## Invariants

These are not tunables unless explicitly changed by a decision record:

- Position is sacred (state only)
- Dot size = normalized dominance
- Halo = dispersion (not volume, not agreement)
- Halo growth is slower than halo contraction
- Effectiveness is directional and effort-normalized
- Air pocket guardrail must exist
- Lean is transitional only
- Coarse visual binning with hysteresis
- No bars constructed in adapters
- Engine holds no historical persistence

---

## Contribution Rules

Any change must answer:

1. Does this improve the lens’s ability to show structural flow?
2. Does this introduce interpretation or decision logic?
3. Does this overload a visual channel?

If (2) or (3) are true, the change likely violates system intent.

All non-trivial changes require a new `FL-XXXX` decision record.

---

## Adapter Contract

Adapters must output:

- `symbol`
- `timestamp`
- `price`
- `efforts[] = {source_id, side_type, effort_value}`

Adapters must not:
- normalize data
- compute dominance
- compute effectiveness
- compute dispersion

---

## Design Priority Order

1. Semantic correctness
2. Visual stability
3. Cross-symbol consistency
4. Performance
5. Code elegance

Never sacrifice (1)–(3) for (4) or (5).

---

## Guiding Principle

> **Flow Lens shows how force propagates through market structure.**

If a feature does not serve that principle, it does not belong in this system.

## Development Environment (Invariant)

- A local virtual environment at `.venv/` is mandatory
- All tooling must run inside the active `.venv`
- Install the package in editable mode before development:
  - `pip install -e .`

Do not modify `sys.path` or bypass the environment.

## Tooling & Style (Binding)

All Python code MUST adhere to:
- ruff (using repo ruff.toml)
- pyright (default settings unless overridden)

Rules:
- Code that violates ruff or pyright is considered INVALID
- Fixes must be made at implementation time, not deferred
- Do NOT add noqa, type: ignore, or suppressions without explicit approval
- Imports must satisfy isort rules
- Types must be explicit where required by pyright

Execution Constraint:
- After any implementation phase, `ruff check .` and `pyright` MUST pass
- If uncertain how to satisfy a rule, STOP and ASK

Pyright note:
- Use `scripts/pyright.sh` (runs the bundled Node CLI) to avoid pyright-python wrapper hangs.
- If you run via Python, use `python scripts/pyright.py`.

## Change Discipline

- No speculative refactors
- No “helpful” generalizations
- No semantic interpretation without a spec change

When rules are unclear, do not guess.

---

## Final Rule

This file sets **guardrails, not logic**.

All domain rules are captured in `docs/decisions`
