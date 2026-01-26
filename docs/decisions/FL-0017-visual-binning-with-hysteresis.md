# FL-0017 – Visual Binning with Hysteresis

## Decision

Dot size and halo size are displayed using coarse bins with hysteresis bands.

Example (3-bin model):

Dot Size Thresholds:
- Small < 0.35
- Medium 0.35–0.70
- Large > 0.70

Halo Thresholds:
- Low < 0.33
- Medium 0.33–0.66
- High > 0.66

Hysteresis band: ±0.05 around each boundary.

A bin transition occurs only if the value exits the band beyond the boundary.

## Rationale

Coarse bins preserve pre-attentive readability. Hysteresis prevents rapid flipping at boundaries, which would be interpreted as signal when it is noise.

## Status

Accepted (Invariant)
