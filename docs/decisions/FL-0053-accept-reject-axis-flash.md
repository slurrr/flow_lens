# FL-0053 – Acceptance/Rejecting axis flash cue

## Decision

The `ACCEPTING` / `REJECTING` axis labels flash as a short directional cue:

- green when signed effectiveness displacement is bullish (`disp > 0`)
- red when signed effectiveness displacement is bearish (`disp < 0`)

The cue uses a short flash window with cooldown so labels do not blink continuously.

## Rationale

This improves at-a-glance directional interpretation without adding new channels or changing lens state semantics. The cue is derived from the same signed displacement basis that drives effectiveness, preserving semantic consistency.

## Status

Accepted
