# FL-0031 – Adapter Configuration (app.toml)

## Decision

Adapters are declared in `app.toml`.

Example:

[adapters.binance_spot]
type = "binance_spot_ws"
symbols = ["BTCUSDT", "ETHUSDT"]

[adapters.binance_perp]
type = "binance_perp_ws"
symbols = ["BTCUSDT", "ETHUSDT"]

## Rationale

Config-driven design allows adding/removing venues without code changes and supports multi-source dispersion.

## Status

Accepted
