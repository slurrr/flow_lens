# Flow Lens Engine — Reference Model (Contributor Guide)

This document defines the **data-source-agnostic internal model** and the exact transformations used to produce the visual channels. Adapters plug into this model; the engine logic below must remain invariant unless a formal design change is approved.

All math examples and logic are **authoritative**.

---

## 1) Standardized Internal Model (Data-Source Agnostic)

At each update tick `t`, the engine consumes a **FlowFrame** with only these conceptual inputs:

### Price

- `p(t)`: reference price series (one per symbol)

### Spot Effort (one or many sources)

- `E_spot_i(t)`: spot “effort” contributions by source _i_

### Perp Effort (one or many sources)

- `E_perp_j(t)`: perp “effort” contributions by source _j_

**Effort definition**  
Effort is a **non-negative quantity** representing aggressive participation. Exact proxy is adapter-defined.

**No other fields are required.**

---

## 2) Compute the 4 Visual Channels

### A) X-axis: Control (Spot vs Perp Dominance)

We want **directional dominance**, not raw volume.

**Net dominance**

```
D(t) = E_spot(t) - E_perp(t)
where
E_spot = Σ E_spot_i
E_perp = Σ E_perp_j
```

**Normalize to [-1, +1] with spike-safe scaling**

```
X(t) = D(t) / (E_spot(t) + E_perp(t) + ε)
```

This is a clean “who is pushing” ratio.

**Storyboard match**

- Perp trap/squeeze → X strongly negative
- Spot continuation/air pocket → X positive (or drifting)

---

### B) Y-axis: Effectiveness (Is Effort Working)

Y means:

> “Given who’s dominant, is price moving in that direction per unit of effort?”

**1. Directional displacement aligned to dominance**

```
Δp = p(t) - p(t-Δ)
disp = sgn(D) * Δp
```

(positive if price moved in the direction of the dominant side)

**2. Total effort**

```
E = E_spot + E_perp
```

**3. Raw effectiveness**

```
eff_raw = disp / (E + ε)
```

**4. Compress to [-1, +1]**

```
Y = tanh(k * eff_raw)
```

`k` is a **global tuning constant**, not symbol-specific.

**Storyboard match**

- Trap: dominant side has big E, disp small/negative ⇒ eff_raw ≤ 0 ⇒ Y down
- Squeeze/continuation: disp positive relative to E ⇒ Y up
- Chop: disp ~0 despite E ⇒ Y ~0
- Air pocket: disp big but E tiny ⇒ eff_raw explodes ⇒ without guardrails Y would peg up (bad)

So we add one guardrail.

---

## 3) Air Pocket Guardrail

Avoid misreading “empty movement” as conviction. Do **not** change Y semantics — only damp when effort is too low.

**Effort floor gate**

```
E_floor = median(E over last N) * α   (α small, e.g., 0.2)
gate = clamp(E / (E_floor + ε), 0, 1)
Y = gate * tanh(k * eff_raw)
```

**Effect**

- Thin effort → Y cannot scream “accepted trend”
- Still allows modest Y, but extremes require real effort

**Storyboard preservation**

- Air pocket: high disp, low E ⇒ gate small ⇒ Y not extreme
- Real trend: healthy E ⇒ gate ~1 ⇒ Y reflects true effectiveness

---

## 4) Dot Size: Dominance Magnitude (Force)

Dot size reflects **decisiveness of control**, not raw effort.

```
dom = |D| / (E + ε)    in [0,1]
```

Optional tempering:

```
S = sqrt(dom)   (more sensitivity at low end)
or
S = dom         (linear)
```

Map `S` into bins (3–4) **with hysteresis**.

**Storyboard alignment**

- Trap/squeeze: large decisive dominance ⇒ big dot
- Chop: D cancels ⇒ small dot even if E high
- Continuation: dot grows as dominance grows

---

## 5) Halo: Dispersion of Contributing Effort

Halo measures **how distributed effort is across independent sources**. Prevents a single source from faking breadth.

Let `E_i` be all source efforts (spot + perp combined).

**Normalized weights**

```
w_i = E_i / (Σ E_i + ε)
```

### Option A: Effective Number of Sources (Hill Number)

```
H = 1 / Σ(w_i^2)      (ranges 1..K)
Hn = (H - 1) / (K - 1)   in [0,1]
```

### Option B: Entropy

```
H = -Σ(w_i log w_i)
Hn = H / log K
```

```
halo_raw = Hn
```

**Asymmetric dynamics**

```
halo = min(halo + grow_rate, halo_raw)      // grow slowly
halo = max(halo - shrink_rate, halo_raw)    // shrink faster
```

(or equivalent smoothing with different time constants)

Preserves:

- Crowd arrives gradually
- Crowd leaves quickly
- No instant “everyone agrees” artifact

---

## 6) Update Cadence + Smoothing

Define a fixed update window `Δ` (e.g., 1s, 2s, 5s).

### X, Y Smoothing

```
X = lerp(X_prev, X_new, ax)
Y = lerp(Y_prev, Y_new, ay)
```

Small `a` values (0.1–0.2) are sufficient.

### Dot Size Binning with Hysteresis

Example thresholds (3 bins):

- small < 0.35
- medium 0.35–0.70
- large > 0.70

Add hysteresis bands (e.g., ±0.05).

### Lean

Lean direction = sign of `(X_new−X_prev, Y_new−Y_prev)`  
Displayed briefly (1–2 frames per update).

---

## 7) Adapter Contract (Plug Any Data Source)

Each adapter must output, per symbol, per tick:

- `p(t)` (reference price)

- A list of effort contributions:

```
efforts = [(source_id, effort_value, side_type)]
where side_type ∈ {spot, perp}
```

**Engine responsibilities**

- aggregation
- normalization
- damping
- dispersion
- rendering channels

**Adapter freedom**
Effort proxy is adapter-defined (aggressive volume, quote imbalance proxy, CVD delta, etc.), provided:

- effort is non-negative
- higher = more aggressive participation
- consistent across time for that source
