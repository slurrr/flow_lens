# Flow Lens — Current System State (fl-current-state-01-27-2026)

Created: 2026-01-28 (file name requested as 01-27-2026)

## 0) Scope and intent

This document is a **current-state engineering report** for the Flow Lens repo.

It covers:

- The moving parts (modules, data structures, runtime loop)
- What happens **when** (startup → ingest → tick → render)
- The exact **math** used to compute visual channels (X, Y, size, halo, lean)
- Notable gaps between **decision records** and **current implementation**

It does **not** propose new semantics or add “signals”; it describes how the lens currently functions.

---

## 1) System in one paragraph

Flow Lens is a curses TUI that, for each configured symbol, ingests trade-level data from Binance
(spot and perp websockets), converts trades into non-negative “effort” events, maintains a rolling
time window buffer, and on a fixed cadence computes a bounded, smoothed structural state:

- **X**: spot vs perp dominance (control)
- **Y**: directional displacement per unit effort (effectiveness)
- **Dot size**: magnitude of dominance (force)
- **Halo**: dispersion of effort across sources
- **Lean**: short-lived direction of change of the smoothed state

---

## 2) Repo layout (primary moving parts)

Core runtime:

- `src/flow_lens/main.py` — curses app entrypoint; adapter supervision; per-symbol loops; render loop
- `config/app.toml` — configured base symbols per adapter
- `src/flow_lens/config.py` — TOML loader and symbol normalization

Adapters (ingestion; “dumb” translation to events):

- `src/flow_lens/adapters/base.py` — connection loop, status/staleness tracking, stats
- `src/flow_lens/adapters/binance_spot_ws.py` — Binance spot `@aggTrade` stream → Events
- `src/flow_lens/adapters/binance_perp_ws.py` — Binance perp `@aggTrade` stream → Events
- `src/flow_lens/symbols.py` — resolves configured bases to concrete Binance symbols + quote→USDT rates

Engine (all structural logic):

- `src/flow_lens/engine/buffer.py` — `RollingEventBuffer` (time-based append/expire)
- `src/flow_lens/engine/aggregation.py` — aggregates buffer events (spot/perp totals + per-source)
- `src/flow_lens/engine/state_engine.py` — computes X, Y, size, halo, lean (incl. smoothing + hysteresis)
- `src/flow_lens/engine/dispersion.py` — Hill-number dispersion + asymmetric halo dynamics
- `src/flow_lens/engine/constants.py` — tunable defaults (Δ, smoothing, thresholds, etc.)
- `src/flow_lens/engine/loop.py` — integration “step”: buffer update + FlowFrame creation + engine compute

TUI:

- `src/flow_lens/tui/renderer.py` — maps normalized state into a grid; draws dot/halo/lean and status bar
- `src/flow_lens/tui/input.py` — symbol switching and slash-search

Mock/storyboard validation:

- `docs/reference/storyboard.md` — regime storyboard expectations
- `src/flow_lens/mock_main.py` — synthetic scenarios (trap/continuation/squeeze/air pocket)
- `src/flow_lens/adapters/mock.py` — deterministic step → Event generator

---

## 3) Runtime: what happens when

### 3.1 Startup sequence (`src/flow_lens/main.py`)

1. **Load defaults**
   - `Defaults()` from `src/flow_lens/engine/constants.py`
   - `Δ = update_window_seconds = 2.0s` by default (also used as update cadence)

2. **Load config**
   - `load_app_config()` reads `config/app.toml`
   - Symbols are normalized by removing `-`/`_` and uppercasing

3. **Resolve configured base symbols to Binance symbols**
   - `BinanceSymbolResolver.resolve_spot()`:
     - fetches spot `exchangeInfo` + `ticker/24hr`
     - selects top `N=3` spot pairs per base by “normalized volume” (quoteVolume × quote→USDT)
   - `BinanceSymbolResolver.resolve_perp()`:
     - fetches perp `exchangeInfo` + `ticker/24hr`
     - selects top `N=1` perp pair per base (supports `1000{BASE}` prefixed bases)
   - `build_symbol_maps()` produces:
     - base→actual list (spot and perp)
     - actual→base reverse maps
     - spot actual→quote_to_usdt conversion rates

4. **Start adapters in a supervisor thread**
   - `AdapterSupervisor` runs an asyncio loop on a background thread
   - It creates:
     - `BinanceSpotWSAdapter(symbols=<flattened actuals>, symbol_rates=<actual→rate>)`
     - `BinancePerpWSAdapter(symbols=<flattened actuals>)`
   - Adapters stream `AdapterEvent(symbol=<actual>, event=<Event>)` into a thread-safe queue

5. **Create per-base-symbol engine loops**
   - For each *base symbol* in config, create:
     - `RollingEventBuffer(window_delta_ms = Δ_ms)`
     - `StateEngine()`
     - `EngineLoop(symbol=<base>, buffer=<...>, engine=<...>)`
   - Note: loops exist for **all symbols simultaneously**, not only the currently selected symbol.

6. **Enter the main loop**
   - Drain adapter events continuously
   - Every `Δ` seconds: compute a new `StateSnapshot` per base symbol
   - At ~30 FPS: render the currently selected symbol

### 3.2 Event ingestion (adapter → queue → per-symbol pending)

- Each websocket message becomes an `Event` (see §4).
- In the main thread, `_drain_events()`:
  - maps the *actual Binance symbol* back to the configured *base symbol*
  - pushes events into `runtime.pending[base_symbol]` until the next update tick

### 3.3 Update tick (every `Δ` seconds)

On each tick at time `t_now_ms`:

For each base symbol:

1. Take and clear pending events for that symbol
2. Call `EngineLoop.step(events, t_now_ms)`
3. Store the returned `StateSnapshot` (or `None` if no price yet)

### 3.4 Render loop (~30 FPS)

The renderer draws:

- A fixed state-space box:
  - Left label: `PERP`, right label: `SPOT`
  - Top: `ACCEPTING`, bottom: `REJECTING`
- Dot position from `state.x`, `state.y`
- Dot size from `state.size_bin`
- Halo from `state.halo_bin`
- Lean offset from `state.lean` (one-frame nudge)
- Status bar derived from adapter staleness + reconnect stats

---

## 4) Data model: Event, FlowFrame, buffer

### 4.1 `Event` (adapter output + buffer storage)

`src/flow_lens/models/event.py`:

```text
Event(
  timestamp: int,          # exchange/event time (ms since epoch)
  source_id: str,          # e.g. "binance_spot" / "binance_perp"
  side_type: "spot"|"perp",
  effort_value: float,     # non-negative proxy
  price: float             # reference price carried with the event
)
```

### 4.2 `RollingEventBuffer` (rolling window Δ)

`src/flow_lens/engine/buffer.py`:

- `append/extend`: append-only event queue; tracks `last_price` and its timestamp
- `expire(now)`: evicts events with `event.timestamp < now - Δ`
- `snapshot()`: returns current in-window events as a tuple
- `reference_price(now)`: returns the last known trade price, **carried forward** if no events occur

### 4.3 `FlowFrame` (engine input per tick)

`src/flow_lens/models/flow_frame.py`:

```text
FlowFrame(
  symbol: str,                    # base symbol
  timestamp: int,                 # tick time (now_ms)
  price: float,                   # reference price from buffer
  efforts: Sequence[EffortContribution]
)
EffortContribution(source_id, side_type, effort_value)
```

In `src/flow_lens/engine/loop.py`, `flow_frame_from_events()` aggregates the **tick’s incoming events**
into per-(source_id, side_type) contributions.

---

## 5) Adapter math: how effort_value is currently computed

### 5.1 Binance spot (`src/flow_lens/adapters/binance_spot_ws.py`)

Consumes Binance spot `@aggTrade` stream.

From payload:

- `price = float(data["p"])`
- `quantity = float(data["q"])`
- `timestamp = int(data["T"])`

Then convert spot price into USDT using `symbol_rates[symbol] = quote_to_usdt` from symbol resolution:

- `price_usdt = price * quote_to_usdt`
- `effort_value = price_usdt * quantity`

Emits:

- `source_id = "binance_spot"`
- `side_type = "spot"`
- `price = price_usdt`

Interpretation: current effort proxy is **quote volume in USDT**.

### 5.2 Binance perp (`src/flow_lens/adapters/binance_perp_ws.py`)

Consumes Binance perp `@aggTrade` stream.

- `price = float(data["p"])`
- `quantity = float(data["q"])`
- `effort_value = price * quantity`

Emits:

- `source_id = "binance_perp"`
- `side_type = "perp"`
- `price = price` (perp USDT price)

---

## 6) Engine math: compute X, Y, size, halo, lean

This is implemented primarily in `src/flow_lens/engine/state_engine.py` (with halo math in
`src/flow_lens/engine/dispersion.py`).

Notation below matches the code structure.

### 6.1 Aggregate efforts

In `StateEngine.compute(frame, ...)`, first aggregate frame efforts:

```text
E_spot = Σ effort_value where side_type="spot"
E_perp = Σ effort_value where side_type="perp"
E      = E_spot + E_perp
D      = E_spot - E_perp
```

Also compute per-source totals:

```text
E_source[source_id] = Σ effort_value across both side_types
```

### 6.2 X-axis (dominance / control)

Raw dominance ratio:

```text
X_raw = D / (E + ε)
X_raw is clamped to [-1, 1]
```

Then low-pass smoothing (see §6.6).

### 6.3 Directional displacement

The engine tracks `last_price` internally.

```text
Δp = price(t) - price(t-1)   # previous tick’s price, not a bar close

disp =
  +Δp  if D > 0   (spot dominant)
  -Δp  if D < 0   (perp dominant)
   0   if D = 0 or no last price
```

### 6.4 Y-axis (effectiveness / accepted vs rejected)

Raw “displacement per unit effort”:

```text
eff_raw = disp / (E + ε)
Y_raw   = tanh(k * eff_raw)
```

Defaults (`src/flow_lens/engine/constants.py`):

- `k = 1.0`

### 6.5 Air pocket guardrail (effort floor gate)

The engine maintains a rolling deque of recent effort totals (length `N=60` ticks).

```text
E_floor = α * median(E_recent)
gate    = clamp(E / (E_floor + ε), 0, 1)
Y_gated = gate * Y_raw
```

Defaults:

- `N = 60`
- `α = 0.2`

### 6.6 Smoothing (X and Y)

Exponential smoothing:

```text
X = X_prev + a_x * (X_raw   - X_prev)
Y = Y_prev + a_y * (Y_gated - Y_prev)
```

Defaults:

- `a_x = 0.15`
- `a_y = 0.15`

### 6.7 Dot size (force magnitude)

Size reflects the **decisiveness** of dominance:

```text
dom   = |D| / (E + ε)
S_raw = sqrt(dom)
S_raw clamped to [0, 1]
```

Then binned with hysteresis (§6.9).

### 6.8 Halo (dispersion)

Current implementation uses the Hill-number “effective contributors” normalized to [0, 1]
(`src/flow_lens/engine/dispersion.py`).

Given per-source totals `E_i`:

```text
w_i    = E_i / ΣE_i
H      = 1 / Σ(w_i^2)
Halo_raw = clamp((H - 1) / (K - 1), 0, 1)    # K = number of active sources
```

Halo then evolves with asymmetric dynamics:

```text
if Halo_raw > Halo_prev:
  Halo = Halo_prev + g * (Halo_raw - Halo_prev)     # slow growth
else:
  Halo = Halo_prev + d * (Halo_raw - Halo_prev)     # fast decay
```

Defaults:

- `g = 0.10`
- `d = 0.50`

Then binned with hysteresis (§6.9).

### 6.9 Coarse visual binning with hysteresis (dot size and halo)

`src/flow_lens/engine/state_engine.py::_bin_with_hysteresis()` implements a 3-bin model:

- Dot size thresholds: `(0.35, 0.70)`
- Halo thresholds: `(0.33, 0.66)`
- Hysteresis band: `±0.05`

Bins are integers `0, 1, 2` used by the renderer.

### 6.10 Lean (direction of structural change)

Lean is derived from the **smoothed** state deltas:

```text
dx = X - X_prev
dy = Y - Y_prev
lean = (sign(dx), sign(dy))
```

Displayed transiently: the current implementation holds it for **1 frame** (`_lean_frames_remaining = 1`).

---

## 7) Where the engine pulls “window” vs “tick” information

This matters because decision records emphasize a rolling window Δ as the source of truth.

In `src/flow_lens/engine/loop.py`:

- `frame.efforts` are built from **only the new events in the current tick**
- Separately, `buffer_snapshot` aggregates **all events in the active rolling window**

In `StateEngine.compute(...)` the call-site currently passes:

- `dispersion_sources = buffer_agg.per_source` (halo uses the rolling window)
- `effort_floor_total = buffer_total` (effort floor uses the rolling window total)

But X, Y_raw, and S_raw are computed from `frame.efforts` (tick events).

If update cadence equals window length (as it does in `main.py`), tick and window tend to be similar.
If they diverge (as they do in `mock_main.py`), the split becomes more visible.

---

## 8) TUI semantics: how visual channels are encoded

`src/flow_lens/tui/renderer.py` implements the locked visual semantics:

- **Dot position (X)**: control (perp ←→ spot)
- **Dot position (Y)**: effectiveness (rejecting ↓ / accepting ↑)
- **Dot size**: force magnitude (3 discrete glyphs)
  - `size_bin=0`: `·`
  - `size_bin=1`: `◉`
  - `size_bin=2`: `⬤` (bold)
- **Halo**: dispersion (0/1/2 radius rings of `.`)
- **Lean**: 1-cell directional offset, shown transiently

Operational-only overlay:

- A bottom status box showing spot/perp adapter health and basic stats

---

## 9) Mock/storyboard validation path (how it is supposed to be used)

`src/flow_lens/mock_main.py` builds synthetic scenarios (`Trap`, `Continuation`, `Squeeze`, `Air Pocket`)
using `StepSpec(x, y, effort_total, source_weights)`.

It converts each step into:

- A set of per-source efforts split into spot/perp consistent with the desired X
- A price increment chosen such that, absent gating/smoothing, `tanh(k * disp/effort_total)` is close to
  the desired Y (it uses `atanh(y)` to invert tanh)

This is the repo’s primary “semantic test harness” for the regime storyboards in `docs/reference/storyboard.md`.

---

## 10) Notable gaps / mismatches vs decision records (current observed)

These are not “bugs” by definition, but they are concrete places where the current code differs from the
stated design decisions.

1. **Reference price preference (spot vs perp)**
   - Decision `docs/decisions/FL-0028-reference-price-source.md` states: prefer spot last trade, else perp.
   - Implementation: `RollingEventBuffer` tracks `last_price` from *whichever event arrives last*.
   - Implication: fast perp prints can dominate the reference price, changing Y even if spot is available.

2. **Dominance/effectiveness computed from tick events, not explicitly from the buffer snapshot**
   - Decisions `docs/decisions/FL-0033-rolling-event-buffer.md` and `docs/decisions/FL-0036-aggregation-from-buffer.md`
     emphasize “buffer as sole source of aggregation”.
   - Implementation: X/Y/size use `frame.efforts` (tick events), while halo and the effort floor use the buffer window.
   - Implication: semantics are “mostly aligned” when tick ≈ window, but the split is structurally inconsistent.

3. **Effort-floor gate uses the passed `effort_floor_total`**
   - Decision `docs/decisions/FL-0021-air-pocket-effort-floor-gate.md` defines the gate using current `E`.
   - Implementation: caller passes `buffer_total` as `effort_floor_total`; then the gate uses that passed value.
   - Implication: if cadence/window diverge, the guardrail may not damp thin-tick moves the way the decision describes.

4. **Dispersion is currently “dispersion across sources”, but sources are coarse**
   - With only `source_id ∈ {"binance_spot","binance_perp"}`, halo primarily reflects how balanced effort is between the
     two adapters, not how “many independent participants” exist.
   - This is expected given a single venue/data source, but it’s a constraint on interpretation today.

5. **Time synchronization/skew is not explicitly handled**
   - `expire()` compares exchange-provided `Event.timestamp` to local `now_ms`.
   - If local clock differs materially from Binance timestamps, expiry can be off.
   - This aligns with `docs/decisions/decisions_on_hold.md` (“Multi-adapter time sync”).

6. **No automated tests are present**
   - `tests/` currently contains only `tests/__init__.py`.
   - The mock storyboard is the effective correctness harness.

---

## 11) Candidate “room for improvement” areas (non-semantic)

These are operational/implementation improvements that can be discussed without turning the lens into a
signal engine. Any semantic change should be recorded as a new `docs/decisions/FL-XXXX` entry.

- Make reference price selection match `FL-0028` (spot-preferred) while still satisfying `FL-0037` continuity.
- Unify window semantics: compute X/Y/size from the **same window aggregate** used for halo/floor, or explicitly
  document the intended split (tick vs window) with a decision record.
- Clarify (and possibly harden) timestamp handling: define whether expiry uses exchange time, local time, or a
  monotonic mapped clock (this is especially relevant if spot/perp feeds drift).
- Improve “single source” halo interpretability: document in the TUI/help that halo is *source dispersion*, and
  with only 2 sources it is effectively a “balance of contribution” measure.
- Add a minimal test harness around the storyboard scenarios (even just assertions on monotonic expectations)
  if/when you want regression protection.

---

## 12) Appendix: default constants (as currently coded)

From `src/flow_lens/engine/constants.py`:

- `Δ = 2.0s`
- Effort floor: `N = 60 ticks`, `α = 0.2`
- Smoothing: `a_x = 0.15`, `a_y = 0.15`
- Effectiveness: `tanh_k = 1.0`
- Halo dynamics: growth `0.10`, decay `0.50`
- Binning:
  - Dot size thresholds `(0.35, 0.70)`
  - Halo thresholds `(0.33, 0.66)`
  - Hysteresis band `0.05`

