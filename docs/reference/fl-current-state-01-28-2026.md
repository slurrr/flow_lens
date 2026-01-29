# Flow Lens — Current System State (fl-current-state-01-28-2026)

Created: 2026-01-28 (MST / UTC−07:00)

## 0) Scope and intent

This report describes **how Flow Lens functions as currently built**, including:

- Major modules and their responsibilities
- Runtime flow (startup → ingest → buffer → compute → render)
- Current math for X, Y, size, halo, lean (and where it differs from the reference model / decisions)
- A “diff-style” summary of recent changes and remaining gaps

Guiding invariant (lens purpose):

> Convert raw spot + perp participation into a normalized, source-agnostic state model that reveals who is in control and whether their effort is being accepted or absorbed.

---

## 1) What changed since the previous current-state snapshot

### 1.1 Recent upstream refactor (committed)

The repo’s `main` branch includes a significant refactor committed on **2026-01-28**:

- `src/flow_lens/engine/buffer.py` now tracks **spot and perp prices separately**, keeps “last-before-window” prices, and can produce a **window start/end price range** for the preferred price series.
- `src/flow_lens/models/flow_frame.py` adds `price_start` so the engine can compute `p(t) - p(t-Δ)` (or equivalent) directly.
- `src/flow_lens/engine/loop.py` now constructs `FlowFrame` from the **rolling buffer snapshot** (window aggregation), and supports a `window_override_ms`.
- `src/flow_lens/symbols.py` and `src/flow_lens/adapters/binance_spot_ws.py` now support **live quote-rate updates** via additional spot streams for quote→USDT conversion.
- `src/flow_lens/main.py` adds an adaptive “TBT” (trade-by-trade) mechanism to avoid stepping the engine unnecessarily and to optionally adapt window sizing.

### 1.2 Local follow-up adjustments (uncommitted in this workspace)

`git diff` currently shows two small follow-ups:

- `src/flow_lens/engine/state_engine.py`: removed effort-gating from `X_raw` so **X encodes control only** (preserves visual channel orthogonality).
- `src/flow_lens/mock_main.py`: adjusted simulated timestamps so the storyboard harness advances by **Δ per step**, keeping the mock path consistent with the engine’s window semantics.

---

## 2) Moving parts (current architecture)

Core runtime:

- `src/flow_lens/main.py` — curses loop; adapter supervision; per-symbol engine loops; render loop; adaptive stepping/windowing (TBT)
- `config/app.toml` — configured base symbols + runtime knobs (e.g. `runtime.tbt_window_multiplier`)
- `src/flow_lens/config.py` — TOML loader and symbol normalization

Adapters (ingestion only):

- `src/flow_lens/adapters/base.py` — connection lifecycle; staleness; reconnect stats; mean TBT estimation
- `src/flow_lens/adapters/binance_spot_ws.py` — spot trades → Events; also streams quote-pairs to keep quote→USDT rates fresh
- `src/flow_lens/adapters/binance_perp_ws.py` — perp trades → Events
- `src/flow_lens/symbols.py` — resolves base symbols → (spot actuals, perp actuals), and builds quote→USDT plumbing

Engine (structural model):

- `src/flow_lens/engine/buffer.py` — rolling time window; spot/perp price tracking; window start/end price range
- `src/flow_lens/engine/aggregation.py` — window aggregation into totals + per-(source_id, side_type)
- `src/flow_lens/engine/loop.py` — integrates buffer + aggregation into a `FlowFrame` each step
- `src/flow_lens/engine/state_engine.py` — computes X, Y, size, halo, lean
- `src/flow_lens/engine/dispersion.py` — Hill-number dispersion + asymmetric halo dynamics
- `src/flow_lens/engine/constants.py` — defaults (Δ, smoothing, thresholds)

TUI:

- `src/flow_lens/tui/renderer.py` — dot/halo/lean drawing + status bar (now includes TBT)
- `src/flow_lens/tui/input.py` — symbol switching and slash-search

---

## 3) Runtime flow (what happens when)

### 3.1 Startup (`src/flow_lens/main.py`)

1. Load defaults (`Defaults()`): `Δ = Defaults.time_domain.update_window_seconds` (default 2.0s).
2. Load config (`config/app.toml`) including `runtime.tbt_window_multiplier`.
3. Resolve symbols:
   - spot: top-N pairs per base (N=3) + quote metadata
   - perp: top-N per base (N=1)
4. Build symbol maps including quote→USDT plumbing (`SymbolMaps.quote_pairs`, `.quote_rates`).
5. Start adapters (spot + perp) under an asyncio supervisor thread; emit `AdapterEvent`s into a queue.
6. Initialize a per-base-symbol `EngineLoop` with a `RollingEventBuffer(Δ)` and `StateEngine`.

### 3.2 Ingest (adapter → pending)

- Main thread drains queued adapter events and appends them to `pending[base_symbol]`.
- A per-symbol `last_event_ms` is tracked to support conditional stepping.

### 3.3 Tick/update (every `Δ` seconds wallclock)

At each scheduled tick:

- Build TBT-derived settings per symbol:
  - `cutoff_ms`: threshold for “keep stepping even if no new events”
  - `window_override_ms`: optional per-symbol window override (trade-frequency-based)
- For each symbol:
  - If there are new events: `loop.step(events, now_ms, window_override_ms=...)`
  - Else: only step if the last event is “recent enough” by `cutoff_ms`

This is an **operational behavior change** from “always compute every tick for every symbol.”

---

## 4) Current math: X, Y, size, halo, lean

### 4.1 Aggregation domain (rolling window)

At each step, the engine aggregates over the **rolling event buffer snapshot** (the active time window):

- `E_spot = Σ effort_value where side_type="spot"`
- `E_perp = Σ effort_value where side_type="perp"`
- `D = E_spot − E_perp`
- Per-source totals for halo: `E_source[source_id] = Σ effort_value`
- Per-key totals: `E_key[(source_id, side_type)] = Σ effort_value`

### 4.2 Reference price series (spot-preferred, window-aware)

`RollingEventBuffer.window_price_range(now)` selects a side (“spot” if fresh, else “perp” if fresh, else fallback)
and returns:

- `price_start`: earliest price for that side within the active window (or the last price just before the window)
- `price_end`: most recent price for that side

This is intended to represent `p(t-Δ)` and `p(t)` on a consistent price series.

### 4.3 X-axis (control)

Current implementation (after local follow-up change) is the canonical dominance ratio:

```text
X_raw = clamp(D / (E_spot + E_perp + ε), -1, +1)
X = smooth(X_prev, X_raw, a_x)
```

### 4.4 Y-axis (effectiveness)

Current implementation differs from `docs/reference/internal_model.md` in two ways:

1) displacement uses **log return** over the window’s price range

```text
Δp = log(price_end / price_start)
disp = sgn(D) * Δp
```

2) effectiveness divides by an **effort-normalized** denominator:

```text
effort_norm = E / median(E over last N ticks)
eff_raw = disp / (effort_norm + ε)
Y_raw = tanh(k * eff_raw)
```

Then the air-pocket gate is applied (same gate used for Y as before):

```text
E_floor = α * median(E over last N ticks)
gate = clamp(E / (E_floor + ε), 0, 1)
Y_gated = gate * Y_raw
Y = smooth(Y_prev, Y_gated, a_y)
```

### 4.5 Dot size (force magnitude)

Unchanged from the original reference model:

```text
S_raw = sqrt(|D| / (E + ε))
S_bin = bin_with_hysteresis(S_raw)
```

### 4.6 Halo (dispersion)

Uses Hill-number normalization over per-source totals (bounded [0, 1]) plus asymmetric dynamics:

```text
w_i = E_i / ΣE_i
H = 1 / Σ(w_i^2)
halo_raw = (H - 1) / (K - 1)
halo = asymmetric_update(halo_prev, halo_raw)
halo_bin = bin_with_hysteresis(halo)
```

### 4.7 Lean

Lean derives from sign of the **smoothed** deltas `(X - X_prev, Y - Y_prev)` and is displayed briefly.

---

## 5) Conformance to `docs/reference/internal_model.md` and FL decisions

### 5.1 Improvements / mismatches resolved vs earlier implementation

- **Aggregation from buffer** is now true for X/Y/size/halo (previously mixed tick-vs-window responsibilities).
- **Reference price continuity** and spot/prep preference are handled explicitly by the buffer.
- The system now has an explicit notion of `price_start` and `price_end`, which is closer to the model’s `p(t)` and `p(t-Δ)`.

### 5.2 Remaining mismatches (docs vs code)

These are the current “gaps” between docs/decisions and implementation:

1) `docs/reference/internal_model.md` specifies raw `Δp = p(t) - p(t-Δ)`, but code uses **log return**.
2) `docs/reference/internal_model.md` and `docs/decisions/FL-0020-effectiveness-normalization.md` specify dividing by `E`,
   but code divides by **effort_norm = E / median(E)** (effectively scaling by a rolling baseline).
3) `docs/decisions/FL-0014-system-constants.md` and the reference model assume a fixed `Δ`, but `src/flow_lens/main.py`
   can apply a **per-symbol window override** driven by trade frequency (TBT).
4) `docs/reference/internal_model.md` describes adapter outputs “per tick,” but the runtime now also depends on:
   - conditional stepping (no step if no events and outside cutoff)
   - dynamically-updated quote conversion rates for spot

If the new behavior is intended, the doc set needs to be updated and a new FL decision should formalize:

- log-return displacement choice
- effort normalization choice
- whether adaptive Δ is acceptable or should be removed

If the new behavior is not intended, the implementation should be reverted to match the locked invariants.

---

## 6) What’s still limiting the lens in practice (even if math is “correct”)

These are structural constraints that can make the live lens look “wrong” while still adhering to its semantics:

- **Halo with 2 sources**: with only `binance_spot` and `binance_perp` as sources, halo is effectively a “balance”
  measure between those two, not a true dispersion proxy for broad participation.
- **Directional interpretation depends on the model assumption**:
  the effectiveness sign convention treats spot-dominance as “up works” and perp-dominance as “down works.”
  If the market regime has perp dominance during an uptrend (e.g. perp activity is net-long), the lens will read
  that as “effort not working” unless the effort proxy captures directionality inside perp/spot.

---

## 7) Suggested improvements (within current invariants)

- Keep X ungated (already applied in this workspace) so dot position does not encode effort magnitude.
- If the log-return + effort-baseline normalization is desired, add a decision record and update:
  - `docs/reference/internal_model.md`
  - `docs/decisions/FL-0020-effectiveness-normalization.md`
  - `docs/decisions/FL-0014-system-constants.md` (if adaptive Δ remains)
- Treat `runtime.tbt_window_multiplier` and adaptive windowing as explicitly “operational,” or remove it to preserve
  a single fixed semantic definition of Δ.
- Add a lightweight debug mode (logs only, no UI) to sample distributions of `disp`, `E`, `eff_raw`, and `gate` to
  calibrate `k` and verify Y dynamics without turning the lens into a signal engine.

---

## 8) Suggested improvements (would require a spec/decision change)

- Add **directionality** to effort (buy vs sell aggressor) inside the adapter contract; this would let Y measure
  “working” in a way that doesn’t assume spot=up and perp=down. This is a major semantic change.
- Expand halo semantics by adding more independent `source_id`s (venues, feeds, participant classes) so dispersion
  reflects breadth rather than just “spot vs perp balance.”

