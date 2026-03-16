# Non-deterministic Agent Layer

---
title: "Branch · Narrative Precedence Structure"
source: "https://chatgpt.com/c/69af8bce-f010-832b-8a86-e18223542f66"
author:
  - "[[ChatGPT]]"
published:
created: 2026-03-09
description: agent implementation conversation summary
tags:
  - "clippings"
---

## Agent Layer (non-deterministic)

Agent consumes deterministic outputs:

tokens  
stack\_vector  
narrative\_state  
liquidity\_state

Agent does **not modify deterministic state**.

Roles:

### 1\. Structural synthesis

Combine:

liquidity\_state + narrative\_state

Example interpretation:

Continuation pressure building while liquidity rejects downside force.

Deterministic system cannot synthesize cross-layer relationships cleanly.

---

### 2\. Narrative drift detection

Use time evolution of:

stack\_vector  
primary\_class  
secondary\_class  
confidence

Agent identifies emerging regime shifts:

COMP → EXP attempts increasing  
secondary\_class gaining weight  
confidence falling

Output:

Compression dominant but expansion pressure building.

---

### 3\. Structural transition detection

Detect sequences across time:

COMP → EXP → CONT  
CONT → EXH → REVERT  
failed EXP attempts

Useful for real-time alerts or reports.

---

### 4\. Daily structural reports

Agent reads event logs and generates summaries:

dominant regime  
key transitions  
failed attempts  
liquidity control shifts

Example:

Session dominated by compression until late expansion acceptance.  
Buyers controlled liquidity into close.

---

### 5\. Pattern recognition

Agent identifies recurring structural patterns:

multi-TF continuation alignment  
expansion from long compression  
failed expansion → reversion

---

### 6\. Attention filtering

Agent highlights meaningful conditions:

low narrative confidence  
rapid vector drift  
liquidity dominance flip

Note:

- `confidence` here is a **non-deterministic agent concept**, not a locked deterministic system field.
- The agent may infer confidence from raw bounded dist-state metrics, token structure, stack-vector shape, and later
  research findings.
- We should preserve the deterministic inputs needed to support that inference, but we do not need to freeze a
  canonical confidence formula yet.

---

## Research Capability

Once structural state is logged, the system becomes a **research dataset**.

Questions become possible:

Which structures precede large moves?  
How often does EXP after COMP lead to CONT?  
Which narrative transitions fail?

This requires storing **state events**, not raw flow.

---

## Persistence Strategy

Initial implementation:

append-only JSONL logs

Example file:

data/events/YYYY-MM-DD.jsonl

Suggested persisted shape (informal; free to evolve):

- `ts_ms`
- `symbol`
- `interval_start_ms`
- `interval_end_ms`
- `liquidity_state`
  - full 15m liquidity rollup object
- `dist_state` (optional)
  - aligned dist-state snapshot for the same 15m close when available
  - may include row metrics, row tokens, stack vector, primary/secondary class, and narrative fields
- `agent_inputs` (optional)
  - convenience block if we want a prompt-friendly subset or lightly transformed mirrors of deterministic fields
- `context` (optional)
  - loose additional market/session context; may be omitted or left sparse in early implementation

Working assumptions:

- This is a **logging shape**, not a locked database schema.
- No schema version field is required yet.
- Keys can be added or revised as implementation reveals what is actually useful.
- The important constraint is boundary alignment: the dist-state snapshot, when present, should line up with the same
  15m boundary as the liquidity rollup.

Advantages:

simple  
replayable  
agent-readable  
no infrastructure

Database only needed later for cross-session analytics.

Recommended future option:

DuckDB

because it can query JSONL directly.

---

## Liquidity State Problem

Current system:

streaming trades  
rolling windows  
TUI rendering

This produces **instantaneous readings**, not persistent state.

Missing piece:

interval accumulation

Needed to convert streaming flow into a state object.

---

## Liquidity Accumulation Model

Introduce a **liquidity accumulator** between stream and snapshot.

Pipeline:

trade stream  
  ↓  
rolling metrics  
  ↓  
liquidity accumulator  
  ↓  
snapshot (e.g. 15m)  
  ↓  
liquidity\_state object

Accumulator maintains cumulative metrics over the interval.

Example internal aggregates:

buy\_aggression  
sell\_aggression  
  
spot\_buy  
spot\_sell  
perp\_buy  
perp\_sell  
  
acceptance\_events  
rejection\_events  
  
poc\_sum  
poc\_samples

Updates occur on each trade event.

---

## Snapshot Emission

At cadence (e.g. 15m):

Compute structural liquidity summary:

dominance  
force\_balance  
spot\_vs\_perp\_pressure  
acceptance\_rate  
poc\_drift

Example emitted object:

liquidity\_state:  
  dominance  
  force\_balance  
  acceptance\_rate  
  spot\_vs\_perp\_pressure  
  poc\_drift

Then reset accumulator.

---

## Reset Strategy

For v1:

hard reset after snapshot

Alternative later:

exponential decay

to create overlapping memory.

---

## Persisted Event

Combined snapshot intent:

- one append-only JSON object per 15m boundary,
- liquidity rollup is the anchor object,
- dist-state and other context blocks are attached when aligned/available,
- the exact shape is expected to evolve during implementation.

Volume:

~100–300 events/day

Sufficient for research and agent reasoning.

---

## System Outcome

The system evolves from:

live flow visualization

to:

market structure intelligence engine

Components:

liquidity structure detector  
+  
distribution state classifier  
+  
agent interpretation layer  
+  
structural event history

Deterministic core remains the **source of truth**.  
Agent layer provides **interpretation, synthesis, and research capability**.
