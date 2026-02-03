# Flow Lens — Stability Checklist (Replay + Diagnostics)

Created: 2026-02-02

This checklist is the **stability gate** for changes that might affect Flow Lens behavior (engine math, windowing, scaling, gating, persistence, or anything that could change dot semantics or perceived stability).

It is intentionally *single-symbol* and *lens-first*: it does not add signals or “good/bad trade” logic. It exists to prevent regressions in semantic correctness and perceptual stability.

---

## 1) What “stable” means here

“Stable” means:

- **Semantics preserved**: X/Y/size/halo/lean meanings remain unchanged (see `docs/reference/internal_model.md` + `docs/decisions/FL-0009-visual-channels-are-orthogonal.md`).
- **Dynamics controlled**: no unexpected saturation, excessive sign flipping, deadband collapse, effort gate pathologies, or price-series thrash.
- **Cross-symbol consistency**: BTC/SOL gate passes with a global `k`, and lower-liquidity symbols do not become unusable.

---

## 2) Gate tiers

### Tier 1 (required): BTC + SOL “top1”

Run:
- BTC: `chop`, `impulse`, `trend_down`, `trend_up`
- SOL: `chop`, `impulse`, `trend_down`, `trend_up`

This is the primary stability gate because BTC/SOL tend to be the hardest to keep both responsive and non-saturated with a single global `k`.

Script: `docs/diagnostics/scenario_runs/top1_runlist_btc_sol.sh`

### Tier 2 (recommended): all symbols “top1”

Run the broader sanity set periodically (or before/after any non-trivial merge).

Script: `docs/diagnostics/scenario_runs/top1_runlist.sh`

---

## 3) Clean-room run procedure (prevents mixed replay dirs)

Prereq: run inside the repo venv (per `AGENTS.md`).

```bash
source .venv/bin/activate
pip install -e .
```

Create a fresh replay directory for the run:

```bash
RUN_TAG="$(date +%Y%m%d-%H%M%S)"
mkdir -p logs/replay_archive
if [ -d logs/replay ]; then mv logs/replay "logs/replay_archive/${RUN_TAG}"; fi
mkdir -p logs/replay
```

Run Tier 1 (BTC/SOL):

```bash
bash docs/diagnostics/scenario_runs/top1_runlist_btc_sol.sh
```

Generate the Tier 1 summary (aggregates all replay logs in `logs/replay/`):

```bash
python scripts/diagnostics_report.py --dir logs/replay --out "docs/diagnostics/diagnostics-summary-${RUN_TAG}_btc_sol.txt"
```

Optional: run the replay-window health check (zero-disp / price-series fallback diagnostics):

```bash
python scripts/replay_window_diagnose.py --logs-dir logs/replay --symbols BTC,SOL --out "docs/diagnostics/replay-window-${RUN_TAG}_btc_sol.txt"
```

Tier 2 (all symbols) is the same flow, swapping the runlist:

```bash
RUN_TAG_ALL="$(date +%Y%m%d-%H%M%S)"
mkdir -p logs/replay_archive
if [ -d logs/replay ]; then mv logs/replay "logs/replay_archive/${RUN_TAG_ALL}"; fi
mkdir -p logs/replay

bash docs/diagnostics/scenario_runs/top1_runlist.sh
python scripts/diagnostics_report.py --dir logs/replay --out "docs/diagnostics/diagnostics-summary-${RUN_TAG_ALL}_all.txt"
```

---

## 4) Pass/fail checks (Tier 1)

Use the summary output file (example: `docs/diagnostics/diagnostics-summary-*_btc_sol.txt`).

### 4.1 Must-pass (BTC + SOL trend legs)

Evaluate at least:
- `BTC trend_up`, `BTC trend_down`
- `SOL trend_up`, `SOL trend_down`

Targets are taken from `tuning_doc.md` (treat as guidance, not “signals”):

- `p95|Y_raw|` in **0.6–0.8**
- `p99|Y_raw|` **< 0.9**
- `Y_raw_sat` **≤ 0.03**
- `Flip Y_raw` **3–8 / min**
- `Y` **1–4 / min**
- `Deadband` **0.25–0.55**
- `Series switch` **< 1 / min** for majors (`BTC`, `SOL`)
- `Gate low` should be *rare* on majors (rule of thumb: **< 0.10**)
- `Y_raw_dir_mismatch` should be **0.00** (directionality regression indicator)

If any of the above are violated on trend legs, treat it as a stability failure unless you have a written rationale (and ideally a decision record) for why the behavior is intentionally changing.

### 4.2 Soft checks (chop / impulse)

Chop and impulse scenarios are useful for sanity but less “targetable”:

- `chop`: higher flip rates and higher deadband rates are expected; this should not force saturation.
- `impulse`: brief spikes are expected; sustained saturation or extreme gating is a red flag.

If chop/impulse fail but trend legs pass, treat it as “needs review” rather than an automatic block.

---

## 5) Persistence line sanity (Phase 1)

The Tier 1 summary does not currently highlight persistence metrics directly. When persistence behavior is in-scope for a change, add a drilldown step:

1) Pick the relevant replay logs from `logs/replay/` (trend legs first).
2) Run a per-file report to surface `persist_raw`, `persist_slope`, and `persist_sign` distributions:

```bash
python scripts/diagnostics_report.py --path "logs/replay/<flow_lens_replay-...>.jsonl.gz" --out "docs/diagnostics/diagnostics-report-${RUN_TAG}_<label>.txt"
```

Sanity expectations (not hard gates):

- `persist_raw` should remain bounded in `[-1, 1]` and not produce NaNs.
- In sustained trend legs where `Y_raw` is consistently strong and same-signed, `persist_raw` should show a **clear drift** in that direction (even if it does not “track the dot” closely).
- In chop, `persist_raw` should **not** whip with `Y_raw` at the same rate; it should tend toward neutral unless there is sustained net acceptance/rejection.

If this conflicts with observed “truth” on live usage, treat it as a spec/definition question (decision record territory), not a tuning tweak.

---

## 6) Compare to a baseline (recommended)

Keep one “known good” Tier 1 summary as a baseline, then diff new runs against it:

```bash
diff -u docs/diagnostics/diagnostics-summary-20260201-111951_k_0.14_baseline_all.txt "docs/diagnostics/diagnostics-summary-${RUN_TAG}_btc_sol.txt" | less
```

Focus on the must-pass fields in §4.1 and on any large changes in:
- `Series switch` rate
- `Gate low` rate
- `Deadband` rate
- saturation (`Y_raw_sat`)

---

## 7) If the gate fails: what to do next

1) Identify whether the failure is:
   - **semantic** (directionality/sign, gating applied to wrong channel, price-series selection coherence), or
   - **tuning** (k too high/low, deadband too strong/weak, scale windows).
2) Re-run Tier 1 after the smallest possible change.
3) If the failure implies a change in meaning or behavior contract, stop and capture a decision record (`docs/decisions/FL-XXXX`).

