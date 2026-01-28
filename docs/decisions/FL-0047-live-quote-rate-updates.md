# FL-0047 – Live quote-to-USDT rate updates

## Decision

For non-USD spot quotes, quote→USDT conversion rates are updated continuously via live quote-pair trades (e.g., TRYUSDT), rather than fixed at startup or refreshed on a timer.

## Rationale

Static quote rates drift and skew spot price/effort, distorting control and effectiveness. Live updates keep conversions accurate without introducing periodic windows or additional polling.

## Status
Accepted
