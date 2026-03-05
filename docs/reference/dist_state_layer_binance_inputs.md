---
title: "Distribution State Layer (Binance Perp Inputs)"
created: 2026-03-04
status: "reference-notes"
scope: "v1 single-source binance perp"
---

# Distribution State Layer (Binance Perp Inputs)

This document captures what we probed as available inputs for a v1 dist-state layer using **Binance USDT-margined futures**
for `BTCUSDT` (perp-coherent rows).

The goal is to make later specs concrete about:

- what streams/endpoints exist,
- what timestamps we can align to bar closes, and
- what the overhead looks like.

## 1) Candle (kline) data

### REST (historical warmup)

Example:

`GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=3m&limit=1`

Response shape (single row):

```
[
  open_time_ms,
  open,
  high,
  low,
  close,
  volume,
  close_time_ms,
  quote_volume,
  trade_count,
  taker_buy_base_volume,
  taker_buy_quote_volume,
  ignore
]
```

This is sufficient for returns + ATR inputs per timeframe.

### WebSocket (live updates + bar close)

Example stream:

`wss://fstream.binance.com/stream?streams=btcusdt@kline_3m`

Observed payload structure:

- envelope: `{ "stream": "...", "data": {...} }`
- `data.e == "kline"`
- `data.k` includes:
  - `k.i` interval string (e.g. `"3m"`)
  - `k.t` kline start time ms
  - `k.T` kline close time ms
  - `k.o/h/l/c` OHLC strings
  - `k.v` volume string
  - `k.x` is_closed boolean

Important behavioral point:

- kline streams send updates throughout the candle; dist-state should update its row state only on `k.x == true`
  (bar close), to avoid intra-bar churn/noise.

## 2) Open interest (OI)

### REST (current snapshot)

Example:

`GET https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT`

Observed keys:

- `openInterest` (string; contract units)
- `time` (ms)

This can be sampled at bar closes to compute `ΔOI` per bar.

### REST (historical warmup)

Example:

`GET https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=15m&limit=2`

Observed keys per row:

- `sumOpenInterest` (string)
- `sumOpenInterestValue` (string)
- `timestamp` (ms)

Availability note:

- We confirmed `period` works for: `5m`, `15m`, `1h`, `4h`.
- `3m` was not probed as available and should not be assumed available for OI history in v1.

Implication for v1:

- `P` for `15m/1h/4h` can warm up cleanly from `openInterestHist`.
- `P` for `3m` can still be computed live by sampling `openInterest` snapshots on each `3m` bar close, but its
  normalization warmup will be “live-built” unless we choose a seeding strategy (spec decision).

## 3) Funding + mark/index (optional early input)

### REST (snapshot)

Example:

`GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT`

Observed keys include:

- `markPrice`, `indexPrice`
- `lastFundingRate`
- `nextFundingTime`
- `time`

### WebSocket (live mark price updates)

Example stream:

`wss://fstream.binance.com/stream?streams=btcusdt@markPrice@1s`

Observed keys:

- `p` mark price
- `i` index price
- `r` funding rate
- `T` next funding time ms

This provides a stepwise funding rate stream suitable for optional funding-derived metrics.

## 4) Overhead / rate-limit considerations (v1 intuition)

For a single symbol (`BTC`) and four timeframes (`3m/15m/1h/4h`), a low-bloat approach is:

- one WebSocket connection multiplexing:
  - `kline_3m`, `kline_15m`, `kline_1h`, `kline_4h`, and optionally `markPrice@1s`
- REST `openInterest` sampled on each **3m bar close**
  - because `15m/1h/4h` closes are also on `3m` boundaries, this sample cadence is sufficient to align OI to all rows
    (within the same close tick), without increasing REST call frequency.

This yields roughly:

- ~20 `openInterest` REST calls per hour (one per 3m close), plus a handful of warmup calls on startup.

This should be negligible compared to existing multi-symbol trade streams.

