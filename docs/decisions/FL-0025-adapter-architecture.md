# FL-0025 – Adapter Architecture

## Decision

Flow Lens uses a **config-driven adapter system**.  
Adapters are responsible only for ingesting raw market data and converting it into standardized **effort contributions**. All state logic, normalization, smoothing, and visualization are handled by the core engine.

Adapters are configured in `app.toml`.

## Rationale

Separating ingestion from interpretation ensures:
- Market microstructure differences do not affect core semantics
- New venues can be added without modifying engine logic
- The system remains testable and storyboard-driven

## Status

Accepted (Architecture)
