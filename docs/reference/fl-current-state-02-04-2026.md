# Flow Lens — Current System State (fl-current-state-02-04-2026)

Created: 2026-02-04

## 0) Scope and intent

This document is a **current-state engineering + tuning snapshot** for the Flow Lens repo.

It captures:

- The current semantic contract for each visual channel (X, Y, size, halo, lean, persistence line)
- The current “frozen” tuning baseline (config + replay diagnostics references)
- Recent changes since the last current-state snapshot (2026-01-28)
- What is considered stable vs under active investigation

It does **not** propose new trading logic, signals, alerts, or multi-symbol opinions.

---

## 1) Current baseline (frozen)

### 1.1 Baseline config source of truth

- `config/app.toml` (runtime knobs used by live runs and replays)

Key baseline values (BTC/SOL-tuned focus):

- `tanh_k = 0.30`
- `effort_scale_percentile = 0.5`
- `disp_scale_multiplier = 0.10`
- Persistence (Experiment B):
  - `persist_input = "y_gated"`
  - `persist_tau_eff_active = 12.0`
  - `persist_neutral_dir_abs_persist = 0.08`
  - `persist_tau_dir_active = 14.0`
  - pivot/dormancy knobs remain at their current defaults in `config/app.toml`

### 1.2 Baseline diagnostic gates and artifacts

BTC/SOL top1 replay suite (baseline reference for persistence + release metrics):

- `docs/diagnostics/diagnostics-summary-20260203-153321_persist_baseline_final_v3.txt`
  - Includes `Pivot_to0_*` and `Quiet_half_*` release metrics (updated report logic).

Top1-all sanity sweep (last known good for Y saturation targets, pre persistence-metrics update):

- `docs/diagnostics/diagnostics-summary-20260203-002118_k_0.30_top1_all.txt`

---

## 2) Visual semantics (current)

### 2.1 Dot position (X): control

- X encodes **spot vs perp dominance** (control axis).
- X is intended to be control-only (no effort magnitude encoded in X).

### 2.2 Dot position (Y): effectiveness

- Y encodes **effort-normalized effectiveness** (accepted vs rejected axis), with gating and smoothing.
- Tuning intent is: “maximum movement without saturation”, with BTC/SOL as the stability gate.

### 2.3 Dot size: force magnitude (total effort intensity)

Dot size semantics were corrected to remove overlap with X.

- Decision: `docs/decisions/FL-0056-dot-size-total-effort-intensity.md`
- Dot size represents **per-symbol normalized total effort intensity**, independent of X and Y.

### 2.4 Halo: dispersion

- Halo remains “dispersion of contributing effort”.
- With a single venue (Binance) and effectively two sources (spot/perp), halo is structurally limited.
  - More venues (or cohorts) are required before halo is a strong dispersion proxy.

### 2.5 Lean: direction of structural change

- Lean is intended to be transitional and visually subtle.
- Current UI perception: lean is not salient enough to read reliably (tracked as a known gap, not currently tuned).

### 2.6 Persistence line (Phase 1, Experiment B): persisted effectiveness + provenance color

Persistence exists to reduce “instantaneous Y noise” and to surface whether acceptance is building/holding/shifting.

Current semantics (Experiment B):

- Line position represents persisted effectiveness state (`S_eff`) tracking `Y_gated` over time (dt-safe).
- Line color represents **direction provenance** (`S_dir`) derived from the same signed effectiveness basis.
- Pivot controller exists to prevent “directionality stacking” (bull then bear still pushing the line upward).
- Dormancy controller exists to allow release after “lost support” under low activity (support-required fade).

Key decisions:

- `docs/decisions/FL-0053-accept-reject-axis-flash.md` (axis flash direction basis)
- `docs/decisions/FL-0054-persistence-line-control-color.md` (line color semantics; tracked for possible supersession if provenance color semantics are formalized beyond current notes)

Supporting investigation / rationale:

- `docs/reference/persistence-directionality-findings-2026-02-03.md`
- `docs/reference/persistence-phase1-planning-2026-02-02.md`
- `docs/reference/persistence_tuning_doc.md`

---

## 3) Diagnostics + replay workflow (current)

### 3.1 Replay runner (no destructive cleanup)

- `scripts/tune_top1_btc_sol.py`
  - Runs BTC/SOL top1 scenarios into unique run directories under `logs/tuning_runs/…`
  - Writes a diagnostics summary to `docs/diagnostics/…`

### 3.2 Diagnostics summary semantics (important change)

The previous “opposition unwind” lines in the diagnostics summary were keyed to an older mode name and an overly-high
threshold for current persistence amplitudes.

Current diagnostics now measure Experiment B release behavior via:

- `Pivot_to0_*` (pivot unwind time-to-neutral)
- `Quiet_half_*` (active-mode release when `A_eff` goes quiet)
- `S_hold_*` (stale elevated persistence while input is quiet)

Baseline reference summary with corrected metrics:

- `docs/diagnostics/diagnostics-summary-20260203-153321_persist_baseline_final_v3.txt`

---

## 4) Stability status (as of 2026-02-04)

### 4.1 Engine + tuning stability (replay)

- BTC/SOL are currently treated as the stability gate for tuning and appear stable under the top1 replay suite.

### 4.2 UI stability (manual validation)

Status (manual review, limited live price action observed since baseline):

- Build responsiveness (line rises with sustained acceptance): **looks stable / likely correct**
- Release behavior (line relaxes after support disappears, and pivots unwind): **monitoring; passed replay diagnostics**
- Provenance color stability (avoid flicker / ambiguous direction): **monitoring; passed replay diagnostics**

Additional UI notes are expected to be appended after more real price action is observed live.

---

## 5) Notable changes since 2026-01-28 snapshots

- Price series switching hysteresis:
  - `docs/decisions/FL-0052-price-series-switch-hysteresis.md`
- Persistence Phase 1 moved from Experiment A exploration to Experiment B semantics (pivot + dormancy):
  - see §2.6 and §3.2 references
- Dot size semantics corrected to total effort intensity (per-symbol), removing overlap with X:
  - `docs/decisions/FL-0056-dot-size-total-effort-intensity.md`
  - Corresponding updates: `AGENTS.md`, `README.md`

---

## 6) Open items (tracked, not yet acted on)

- Multi-venue expansion plan (needed for true dispersion/halo semantics; also improves X “pinned” interpretability).
- Halo semantics with one venue are known-limited; cohorts may be considered later but are intentionally deferred.
- Lean visibility in the UI is currently too subtle to be a reliable glance signal (candidate for later phase work).
- Any change that alters invariants (persistence semantics, visual channel meanings) requires a new decision record.

