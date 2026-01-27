# FL-0014 – Design Constants

## Decision

The following are tunable system constants governing time resolution, effort normalization, and dispersion measurement:

### Time Domain
- Update window Δ ∈ {1s, 2s, 5s}

### Effort Floor (Air Pocket Guardrail)
- Rolling window length N (in ticks)
- Effort floor multiplier α

### Effectiveness Scaling
- tanh compression factor k

### Dispersion Measurement
- Dispersion metric ∈ {Hill number, entropy}

### Initial Defaults
- Δ = 2s
- N = 60 ticks
- α = 0.2
- k = 1.0
- Dispersion metric = Hill number

## Rationale

Flow Lens is designed around structural regimes, not microsecond microstructure. A 2s window balances responsiveness with stability. The effort floor prevents thin-liquidity price jumps from being misinterpreted as conviction. Hill number dispersion maps intuitively to “effective number of contributors,” which aligns with the halo’s semantic role.

## Status

Accepted (Baseline; tunable)
