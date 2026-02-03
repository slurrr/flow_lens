# FL-0053 – Acceptance/Rejecting axis flash cue

## Decision

The `ACCEPTING` / `REJECTING` axis labels flash as a short bullish/bearish cue:

- green when bullish pressure dominates (`E_dir / E_total` above deadband)
- red when bearish pressure dominates (`E_dir / E_total` below deadband)

The cue uses a short flash window with cooldown so labels do not blink continuously.

## Rationale

This improves at-a-glance directional interpretation without adding new channels or changing lens state semantics. The cue is derived from the directional effort basis (bull-vs-bear pressure), making the flash explicitly bullish/bearish rather than accepting/rejecting.

## Status

Accepted
