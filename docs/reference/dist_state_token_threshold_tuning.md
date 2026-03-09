---
title: "Dist-State Token Threshold Tuning (V1)"
created: 2026-03-06
status: "draft"
related:
  - "docs/decisions/FL-0071-dist-state-row-tokens-v1.md"
  - "SPEC-dist-state-layer-phase2-tokens.md"
  - "docs/diagnostics/dist_state_diagnostics-*.jsonl"
---

# Dist-State Token Threshold Tuning (V1)

This document defines a **practical, repeatable** process for tuning the Phase 2 row-token thresholds so tokens actually
emit in real conditions while staying stable and meaningful.

Tokens are computed from **continuous normalized metrics** (`DistRowMetrics`). Display bins are rendering-only.

## 1) What “good” looks like

Targets (guidelines, not hard rules):

- **Token coverage** (fraction of processed closes with `token != None`):
  - `3m`: 40–80%
  - `15m`: 30–80%
  - `1h`: 20–80%
  - `4h`: 20–80%
- **NEUT**:
  - rare (`< 5%` of closes). `NEUT` is explicit quiet-neutral only; it is not a default.
- **None**:
  - expected sometimes; `None` means “no dominant callout” and the ribbons remain the nuance.
- **Churn**:
  - low. If token flips constantly, widen hysteresis gaps and/or increase dwell.

## 2) Why “no tokens at all” happens

If your diagnostics show:

- `token=None` always, and
- `token_predicate_hits` shows all `false` (except maybe `extended`),

then your thresholds are simply **outside the observed metric ranges** for that timeframe.

This is not a code bug; it’s mis-calibration.

## 3) Inputs and artifacts

You tune from dist-state diagnostics JSONL:

- `docs/diagnostics/dist_state_diagnostics-YYYYMMDD-HHMMSS-p00.jsonl`

Each `dist_state_close` record includes:

- `metrics_v/s/a/t/p` (the continuous values),
- `token_predicate_hits` (what the engine thought was true),
- `token` / `token_strength`.

## 4) Minimum sample sizes (per TF)

Do not tune from tiny samples; distributions will lie.

Minimum processed closes before taking thresholds seriously:

- `3m`: 200+
- `15m`: 60+
- `1h`: 30+
- `4h`: 10+

If you don’t have enough on `1h/4h`, tune `3m/15m` first, then revisit.

## 5) Compute distributions (per TF)

Use this quick analysis snippet to print quantiles and predicate hit rates for a file:

```bash
.venv/bin/python - <<'PY'
import json
from collections import defaultdict
from pathlib import Path

path = Path("docs/diagnostics/dist_state_diagnostics-YYYYMMDD-HHMMSS-p00.jsonl")
rows = defaultdict(list)
for line in path.read_text().splitlines():
    if not line:
        continue
    o = json.loads(line)
    if o.get("event_type") != "dist_state_close" or not o.get("processed"):
        continue
    rows[o["tf"]].append(o)

def quantiles(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    def q(p):
        i = int(round(p * (len(vals) - 1)))
        return vals[i]
    return {
        "n": len(vals),
        "min": vals[0],
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "max": vals[-1],
    }

for tf, items in sorted(rows.items()):
    print(f"\n[{tf}] closes={len(items)}")
    for key in ["metrics_v", "metrics_s", "metrics_a", "metrics_t", "metrics_p"]:
        print(f"- {key}: {quantiles([it.get(key) for it in items])}")
    counts = defaultdict(int)
    for it in items:
        for name, val in (it.get("token_predicate_hits") or {}).items():
            if val:
                counts[name] += 1
    print("- predicate true rates:")
    for name in ["exp", "exh", "cont", "revert", "comp", "neut", "extended"]:
        c = counts.get(name, 0)
        print(f"  - {name}: {c}/{len(items)} ({(c/len(items)) if items else 0:.1%})")
PY
```

## 6) Tuning strategy (v1)

The spec currently uses **fixed numeric thresholds**. That means we tune by aligning thresholds with the observed
distribution while keeping semantics intact.

Principles:

1. **Semantics first**: keep sign meaning.
   - `EXP` should require `T` meaningfully positive.
   - `COMP` should require `T` meaningfully negative.
2. **Coverage second**: move thresholds until you get non-zero predicate hits.
3. **Stability third**: widen hysteresis gaps and/or raise dwell if churn is too high.

### 6.1 Start by making `CONT`/`REVERT` reachable

In practice, the `A` metric often clusters near 0. If `a_cont_enter` is set to `0.35`, you may never see `CONT` at all.

Procedure (per TF):

- choose an initial `a_cont_enter` at roughly `p90(metrics_a)` (clamped to at least `0.08`),
- choose `a_cont_exit = 0.6 * a_cont_enter` (or a similar “meaningful gap”),
- mirror for `REVERT` using negative values:
  - `a_revert_enter` near `p10(metrics_a)` (clamped to at most `-0.08`),
  - `a_revert_exit = 0.6 * a_revert_enter` (note sign).

Then re-run and check that `cont` / `revert` predicate hits are non-zero.

### 6.2 Make `COMP` reachable (watch the V-gate)

In v1, `COMP` requires:

- compression impulse latch (`T <= t_comp_enter`), AND
- `V <= v_low_threshold`.

If you never see `comp=true`:

1. lower the magnitude of `t_comp_enter` (toward zero, but keep it negative), and
2. raise `v_low_threshold` (toward the observed `metrics_v` p25/p50).

Practical guideline:

- set `v_low_threshold` near `p25(metrics_v)` for that TF to avoid “COMP always”.

### 6.3 Make `EXP` reachable (and keep it rare)

Do not force `EXP` to fire if the market is not expanding.

If your `metrics_t` distribution is mostly negative for a TF, then “no EXP tokens” is correct.

When expansion does happen, you want `EXP` to be:

- infrequent but obvious, and
- allowed to override dwell.

Guideline:

- set `t_exp_enter` to a small positive number that is reachable only during real expansions (often `0.10–0.20`),
- set `t_exp_exit` to `0.6 * t_exp_enter` (keep a hysteresis gap).

### 6.4 EXH and “extended”

If `extended` is frequently true but `EXH` is never true, remember the v1 definition:

- `EXH` requires **extension + instability** (reversion bias or compression impulse).

If you want `EXH` to fire under different “instability” semantics, that is a **mapping rule change**, not a threshold
change. Tune thresholds first; then revisit `EXH` semantics only if needed.

### 6.5 NEUT bands

NEUT should be rare and explicit.

If you see lots of NEUT, your neutral bands are too wide. Tighten:

- `s_neut_max`, `a_neut_max`, `t_neut_max`,
- and/or narrow `v_neut_min..v_neut_max`.

## 7) “Aggressive starter” (for first live testing)

If you need tokens to start emitting immediately to validate UI plumbing and churn logging, this is a deliberately
aggressive baseline you can try (then tune from diagnostics):

- `s_dir_deadband = 0.05`
- `a_cont_enter = 0.10`, `a_cont_exit = 0.06`
- `a_revert_enter = -0.10`, `a_revert_exit = -0.06`
- `t_comp_enter = -0.15`, `t_comp_exit = -0.10`
- `t_exp_enter = 0.15`, `t_exp_exit = 0.10`
- `v_low_threshold = 0.45`
- `s_ext_enter = 0.45`, `s_ext_exit = 0.35`

This is not “final.” It’s a fast way to ensure you are not debugging a silent token engine.

## 8) Tighten after you see tokens

Once tokens emit, tune toward the target behavior in §1:

- If token coverage is too high: raise enter thresholds (move away from 0) and/or increase dwell.
- If token coverage is too low: lower enter thresholds (toward 0) and/or decrease dwell.
- If churn is too high: widen hysteresis gaps (enter/exit separation) before increasing dwell.

## 9) What not to do

- Do not couple token thresholds to display bin cutpoints.
- Do not add a default `NEUT` “else” token.
- Do not “fix” missing tokens by emitting fake tokens; tune thresholds and mapping semantics instead.

## 10) Future: dwell scope (base token vs modifiers)

V1 dwell (`token_min_hold_bars_*`) is applied to **base token changes** only.

Even when a base token is held/blocked by dwell, modifiers are allowed to update from the latest metrics:

- strength (`+` / `++`) may change,
- transition risk (`!`) may appear/disappear,
- `P`-based confirmation/divergence may change modifiers,

…as long as the base token is unchanged.

This gives you stable “what is it doing?” labeling while still reflecting real-time changes in intensity and risk.
