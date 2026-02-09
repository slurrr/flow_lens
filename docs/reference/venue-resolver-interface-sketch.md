# Venue Resolver Interface Sketch (Phase 1)

Status: reference sketch. Not a decision record.

Purpose:

- Keep adapters dumb while allowing per-venue multi-pair resolution.
- Provide a shared *interface/shape* without forcing shared business logic.

Principle:

- **Per-venue resolver modules** own venue-specific API calls and ranking logic.
- **Shared interface/shape** keeps orchestration simple and avoids refactors.

## Suggested interface (per venue)

Each venue provides a resolver with a uniform output shape:

```
class VenueSymbolResolver:
    venue_id: str
    def resolve_spot(bases: list[str]) -> SymbolResolution
    def resolve_perp(bases: list[str]) -> SymbolResolution
```

Where `SymbolResolution` matches the existing shape:

- `resolved: dict[base_symbol, list[actual_symbols]]`
- `meta: dict[base_symbol, list[SymbolMeta]]`
- `missing: list[base_symbol]`
- `quote_pairs: dict[quote, QuotePair]` (optional, only if conversion is needed)

## Shared shapes (already in use)

- `SymbolResolution`, `SymbolMeta`, `QuotePair` (see `src/flow_lens/symbols.py`)

These should remain the common result type across venues, even if the resolver
implementation is venue-specific.

## Orchestration expectations

- Adapter config `symbols` should list **base symbols**.
- Resolver maps bases -> actual symbols (product ids) + meta.
- Orchestration builds:
  - `actual -> base` map for routing,
  - optional quote conversion pairs/rates (if venue needs conversion),
  - source registry + selector metadata.
- Adapters receive only:
  - resolved actual symbols,
  - base_symbol mapping,
  - quote conversion tables (if needed).

## Example: Binance (existing)

- Resolver calls exchangeInfo + 24h ticker.
- Ranks by normalized quote volume.
- Produces top-N actuals per base.

## Example: Coinbase (simple)

- No resolver needed for Phase 1 (only `BTC-USD`, `SOL-USD`).
- If multi-quote products are ever used, a Coinbase resolver can follow the same
  interface and ranking pattern.

## Guardrails

- Resolver logic must not leak into adapters.
- Any conversion logic must be explicit (`quote_mode=converted`).
- If a venue lacks usable volume data, resolver should log and fail fast rather
  than silently picking a pair.
