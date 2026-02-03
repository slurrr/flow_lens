# Persistence Tuning Doc

Status: working tuning guide for replay and live validation.

This document focuses on Phase 1 Experiment B persistence behavior.

---

## What To Watch Out For

- Pivot spam in chop: if the line keeps neutralizing/rebuilding constantly, raise `persist_pivot_active_abs`, `persist_pivot_confirm_s`, or `persist_pivot_cooldown_s`.
- Over-sticky persistence: if the line does not let go after regime change, lower `persist_tau_eff_active` or `persist_pivot_neutralize_tau`, or raise `persist_max_delta_s_eff_per_second`.
- Over-reactive persistence: if the line whips too fast, increase `persist_tau_eff_active` and/or lower `persist_max_delta_s_eff_per_second`.
- Color flip noise: if the line color flickers, raise `persist_neutral_dir_abs_persist` and/or `persist_tau_dir_active`.
- Dormancy misfire:
  - if calm periods never relax, lower `persist_dormant_quiet_s` or `persist_dormant_effort_norm_threshold`,
  - if it relaxes during real activity, raise them.

---

## Persistence Knobs (Current)

### Base

- `persist_enabled`: master on/off.
- `persist_input`: source for persistence input (`y_gated` recommended).
- `persist_input_deadband`: zeros tiny `A_eff` noise before persistence input.

### Direction Deadbands

- `persist_neutral_dir_abs_flash`: bull/bear neutral zone for axis flash (now-pressure).
- `persist_neutral_dir_abs_persist`: neutral zone for line color provenance logic.

### Active Tracking

- `persist_tau_eff_active`: how fast line position (`S_eff`) tracks persisted effectiveness.
  - lower = faster response
  - higher = smoother/slower response
- `persist_tau_dir_active`: how fast line color provenance (`S_dir`) tracks directional builder.
  - lower = faster color changes
  - higher = steadier color

### Pivot / Handoff

- `persist_pivot_active_abs`: minimum accepted directional strength needed to consider pivot.
- `persist_pivot_confirm_s`: opposite-side confirmation time before pivot starts.
- `persist_pivot_neutralize_tau`: speed of pulling `S_eff` toward neutral during pivot.
- `persist_pivot_neutral_zone_abs`: threshold defining "neutral reached".
- `persist_rebuild_confirm_s`: confirmation time before rebuild on the new side.
- `persist_pivot_cooldown_s`: lockout after pivot exits (anti-thrash).
- `persist_pivot_max_s`: hard cap on pivot duration.
- `persist_max_delta_s_eff_per_second`: max neutralization speed cap (anti-cliff).
- `persist_tau_dir_pivot`: speed of provenance color transition during pivot.

### Dormancy

- `persist_dormant_quiet_abs`: quiet threshold for acceptance input.
- `persist_dormant_active_abs`: wake-up threshold (should be >= quiet threshold).
- `persist_dormant_quiet_s`: required quiet duration before dormancy starts.
- `persist_tau_dormant`: relax speed toward neutral during dormancy.
- `persist_dormant_effort_norm_threshold`: activity guard for dormancy entry.

---

## High-Leverage Tuning Playbook (Replay First)

### Step 1: Stabilize Pivot Triggering (highest leverage)

Goal: avoid spam pivots in chop while still catching real handoffs.

Tune in this order:
- `persist_pivot_active_abs` (up to reduce false pivots)
- `persist_pivot_confirm_s` (up to require stronger confirmation)
- `persist_pivot_cooldown_s` (up to suppress rapid retriggers)

Symptoms:
- Too many pivots -> increase one or more above.
- Missed real handoff -> decrease `persist_pivot_confirm_s` slightly.

### Step 2: Shape Handoff Unwind

Goal: neutralize fast enough to reflect regime handoff, but without cliffs.

Tune in this order:
- `persist_pivot_neutralize_tau` (lower = faster neutralization)
- `persist_max_delta_s_eff_per_second` (raise to allow faster move, lower to cap cliffs)
- `persist_pivot_neutral_zone_abs` (slightly higher can finish handoff sooner)

Symptoms:
- Handoff feels late -> lower `persist_pivot_neutralize_tau`.
- Handoff looks jumpy/cliffy -> lower `persist_max_delta_s_eff_per_second`.

### Step 3: Set Baseline Persistence Feel (Active Mode)

Goal: get the line to build/hold with meaningful persistence in trends.

Tune in this order:
- `persist_tau_eff_active` (primary line responsiveness)
- `persist_tau_dir_active` (primary color persistence stability)
- `persist_tau_dir_pivot` (color transition behavior during handoff)

Symptoms:
- Line too sluggish in trend -> lower `persist_tau_eff_active`.
- Line too twitchy -> raise `persist_tau_eff_active`.
- Color too noisy -> raise `persist_tau_dir_active` and/or `persist_neutral_dir_abs_persist`.

### Step 4: Quiet Regime Cleanup (Dormancy)

Goal: preserve memory during meaningful activity, release ghost memory in quiet regimes.

Tune in this order:
- `persist_dormant_quiet_s` (how long quiet must persist)
- `persist_dormant_effort_norm_threshold` (activity filter sensitivity)
- `persist_tau_dormant` (relax speed once dormant)

Symptoms:
- Ghost persistence hangs in quiet -> reduce `persist_dormant_quiet_s` or `persist_tau_dormant`.
- Dormancy activates during active flow -> increase `persist_dormant_quiet_s` and/or threshold.

---

## Replay Tuning Loop (Recommended)

1. Change only one cluster (pivot or active or dormancy), not everything at once.
2. Run replay batch over the same scenarios.
3. Compare:
   - mode fractions (`active/pivot/dormant`)
   - pivot frequency in chop
   - handoff time-to-neutral behavior
   - color flip stability
4. Keep changes that improve handoff clarity without adding chop instability.
5. Repeat.

---

## Practical Defaults Philosophy

- First optimize for correct handoff behavior in chop and pivots.
- Then optimize trend readability.
- Then fine-tune dormancy for low-liquidity quiet regimes.

If you must trade off, prioritize semantic correctness and stability over visual drama.
