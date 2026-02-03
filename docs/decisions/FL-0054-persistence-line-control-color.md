# FL-0054 – Persistence line color shows bullish/bearish persisted state

## Decision

The persistence line keeps its positional meaning (persisted effectiveness), and its color now encodes bullish/bearish persisted state:

- green when bullish pressure dominates (`E_dir / E_total` above deadband)
- red when bearish pressure dominates (`E_dir / E_total` below deadband)
- default color near neutral pressure balance

## Rationale

Users need to read bull-vs-bear control at a glance. Coloring the persistence line by directional effort pressure provides that cue immediately while preserving the line’s primary semantic channel (position).

## Status

Superseded by FL-0055.
