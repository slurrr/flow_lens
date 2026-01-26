# FL-0020 – Effectiveness Normalization (Y-Axis)

## Decision

Effectiveness (Y) is computed as directional displacement per unit effort:

disp = sign(D) · Δp  
eff_raw = disp / (E_spot + E_perp + ε)

Y_raw = tanh(k · eff_raw)

Y is then damped by the effort floor gate before smoothing.

## Rationale

Using displacement per effort distinguishes true acceptance from force without movement. The tanh transform compresses outliers and stabilizes cross-symbol comparability. Directional alignment ensures squeezes and traps are distinguishable.

## Status

Accepted (Invariant)
