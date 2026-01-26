# Flow Lens – Contributor Guide

Flow Lens is a **market structure diagnostic lens** for crypto trading.

It does **not** produce signals, scores, alerts, or trade advice.  
It visualizes **flow physics**: who is pushing and whether that effort is working.

This document explains how to contribute without breaking the system’s core semantics.

---

## What Flow Lens Is

Flow Lens is a **single-symbol structural state instrument**.

At any moment it shows:

| Question | Visual Channel |
|----------|----------------|
| Who is in control? | Dot X position |
| Is the effort working? | Dot Y position |
| How strong is the push? | Dot size |
| How widely distributed is participation? | Halo |
| Is the state improving or degrading? | Dot lean |

Everything in the system exists to support these readings.

---

## What Flow Lens Is Not

Do not turn Flow Lens into:

- A dashboard
- A signal engine
- A backtester
- A scanner
- A charting platform
- An indicator collection

If a feature helps make a trade decision directly, it does not belong here.

---

## Mental Model

Flow Lens models **force interacting with resistance**.

- **Force** = normalized dominance of effort
- **Effectiveness** = displacement per unit effort
- **Dispersion** = how concentrated vs distributed that effort is

The lens does not predict. It describes **structural state**.

---

## System Architecture

```
Market Data (WS trades)
        ↓
Adapters (dumb)
        ↓
Effort Events
        ↓
Rolling Event Buffer
        ↓
Engine (normalization + smoothing)
        ↓
State Variables (X, Y, Size, Halo)
        ↓
TUI Renderer
```

Adapters only translate raw trades into effort contributions.  
All market logic lives in the engine.

---

## Core Invariants

These rules define Flow Lens. Breaking them breaks the tool.

### Visual invariants

- Dot position = state only
- Dot size = force magnitude only
- Halo = dispersion only
- Lean = direction of change only

No visual channel may encode multiple meanings.

---

### Structural invariants

- No bar/candle construction
- Rolling window model only
- Variables must be normalized and bounded
- Coarse visual bins with hysteresis
- Halo growth slower than contraction
- Air pocket guardrail required

---

## Adapter Rules

Adapters must:

- Stream trade-level data
- Output `{source_id, side_type, effort_value}`
- Provide a reference price
- Be declared in `app.toml`

Adapters must NOT:

- Compute indicators
- Normalize data
- Infer dominance or effectiveness
- Store historical data

---

## How to Add a New Adapter

1. Add adapter config in `app.toml`
2. Implement translation from feed → effort events
3. Ensure:
   - effort_value ≥ 0
   - timestamps are accurate
   - source_id is stable

No engine changes should be required.

---

## How to Add a Feature (Checklist)

Before proposing a change:

- Does it alter how X, Y, size, halo, or lean are defined?
- Does it introduce interpretation beyond structure?
- Does it add numeric readouts?

If yes, it likely violates system intent.

All structural changes require a new decision record `FL-XXXX`.

---

## What Good Contributions Look Like

- Improving normalization robustness
- Reducing visual noise
- Enhancing adapter reliability
- Making dispersion estimation more accurate
- Performance improvements without semantic change

---

## What Bad Contributions Look Like

- Adding overlays or extra panels
- Adding alerts or thresholds
- Adding historical plots
- Encoding multiple meanings in one visual channel
- Feature creep toward trading tools

---

## Guiding Question for Contributors

> Does this make the lens more truthful about how force moves through the market?

If not, it probably does not belong.

---

## Project Ethos

Flow Lens is a **physics instrument for market structure**.

Clarity > Complexity  
Structure > Indicators  
Truthful representation > feature count
