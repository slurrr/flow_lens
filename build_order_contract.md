# Flow Lens – Build Order Contract

This document defines **implementation sequence only**.  
Design, semantics, and constraints are governed by FL-XXXX decisions and AGENTS.md.

Do not change order unless a dependency requires it.

---

## Phase 0 – Constants & Models

**Goal:** Define shared primitives before logic.

1. `engine/constants.py`  
   - Δ, N, α  
   - smoothing coefficients  
   - hysteresis bands

2. `models/event.py`  
   - Event record structure

3. `models/flow_frame.py`  
   - Standardized frame for engine input

---

## Phase 1 – Rolling Window Mechanics

**Goal:** Engine can hold time-based state.

4. `engine/buffer.py`  
   - Append events  
   - Expire by timestamp  
   - Provide active window snapshot

No flow math yet.

---

## Phase 2 – Effort Aggregation

**Goal:** Convert events → effort totals.

5. `engine/aggregation.py`  
   - E_spot  
   - E_perp  
   - Per-source effort map

---

## Phase 3 – Core State Computation

**Goal:** Compute structural state variables.

6. `engine/state_engine.py`  
   - Dominance X  
   - Effectiveness Y_raw  
   - Air pocket gate  
   - Dot size magnitude

7. `engine/dispersion.py`  
   - Hill number calculation  
   - Halo asymmetry logic

---

## Phase 4 – Temporal Behavior

**Goal:** Stabilize state.

8. Smoothing (X, Y)  
9. Binning + hysteresis  
10. Lean derivation

All applied inside engine.

---

## Phase 5 – Mock Data Path

**Goal:** Validate storyboards without live feeds.

11. Mock adapter producing synthetic effort events  
12. Engine loop producing FlowFrame output

TUI should display correct regime behavior using mock.

---

## Phase 6 – TUI Layer

**Goal:** Visualization only.

13. `tui/renderer.py`  
   - Draw dot, halo, lean  
   - No flow math

14. `tui/input.py`  
   - Symbol switching  
   - Slash search

---

## Phase 7 – Real Adapters

**Goal:** Replace mock with real feeds.

15. `adapters/base.py`  
16. `adapters/binance_spot_ws.py`  
17. `adapters/binance_perp_ws.py`

Adapters only emit effort events.

---

## Phase 8 – Integration

**Goal:** Wire components.

18. `main.py`  
   - Config loading  
   - Adapter startup  
   - Engine loop  
   - TUI loop

---

## Rule of Order

A phase must be functionally complete before starting the next.  
No TUI logic may compensate for missing engine behavior.  
No adapter may contain normalization or smoothing logic.

---

## Verification Milestones

Before Phase 7, mock must visibly reproduce:

- Trap  
- Continuation  
- Squeeze  
- Air pocket  

If not, engine math is incomplete.

---

This document governs *sequence*, not design.  
All semantic authority resides in FL-XXXX decisions.
