from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

USD_QUOTES = {"USDT", "USDC", "FDUSD", "BUSD"}


@dataclass(frozen=True)
class SymbolMeta:
    base: str
    resolved: str
    quote: str
    quote_volume: float
    quote_to_usdt: float
    normalized_volume: float
    note: str | None = None


@dataclass(frozen=True)
class SymbolResolution:
    resolved: dict[str, list[str]]
    meta: dict[str, list[SymbolMeta]]
    missing: list[str]
    quote_pairs: dict[str, "QuotePair"]


@dataclass(frozen=True)
class QuotePair:
    symbol: str
    invert: bool


@dataclass(frozen=True)
class SymbolMaps:
    spot_base_to_actual: dict[str, list[str]]
    perp_base_to_actual: dict[str, list[str]]
    spot_actual_to_base: dict[str, str]
    perp_actual_to_base: dict[str, str]
    spot_actual_to_quote: dict[str, str]
    quote_pairs: dict[str, QuotePair]
    quote_rates: dict[str, float]


class BinanceSymbolResolver:
    def resolve_spot(self, bases: Iterable[str]) -> SymbolResolution:
        exchange = _fetch_json("https://api.binance.com/api/v3/exchangeInfo")
        ticker = _fetch_json("https://api.binance.com/api/v3/ticker/24hr")
        if not isinstance(exchange, dict) or not isinstance(ticker, list):
            raise ValueError("Unexpected spot exchange response.")
        resolution = _resolve(exchange, ticker, list(bases), allow_prefix=False, top_n=3)
        quotes = {meta.quote for metas in resolution.meta.values() for meta in metas}
        quote_pairs = _build_quote_pairs(exchange, quotes)
        return SymbolResolution(
            resolved=resolution.resolved,
            meta=resolution.meta,
            missing=resolution.missing,
            quote_pairs=quote_pairs,
        )

    def resolve_perp(self, bases: Iterable[str]) -> SymbolResolution:
        exchange = _fetch_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
        ticker = _fetch_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not isinstance(exchange, dict) or not isinstance(ticker, list):
            raise ValueError("Unexpected perp exchange response.")
        resolution = _resolve(exchange, ticker, list(bases), allow_prefix=True, top_n=1)
        return SymbolResolution(
            resolved=resolution.resolved,
            meta=resolution.meta,
            missing=resolution.missing,
            quote_pairs={},
        )


def build_symbol_maps(
    spot: SymbolResolution, perp: SymbolResolution
) -> SymbolMaps:
    spot_actual_to_base: dict[str, str] = {}
    spot_actual_to_quote: dict[str, str] = {}
    quote_rates: dict[str, float] = {}
    for base, metas in spot.meta.items():
        for meta in metas:
            spot_actual_to_base[meta.resolved] = base
            spot_actual_to_quote[meta.resolved] = meta.quote
            if meta.quote not in quote_rates:
                quote_rates[meta.quote] = meta.quote_to_usdt

    perp_actual_to_base: dict[str, str] = {}
    for base, actuals in perp.resolved.items():
        for actual in actuals:
            perp_actual_to_base[actual] = base
    return SymbolMaps(
        spot_base_to_actual=spot.resolved,
        perp_base_to_actual=perp.resolved,
        spot_actual_to_base=spot_actual_to_base,
        perp_actual_to_base=perp_actual_to_base,
        spot_actual_to_quote=spot_actual_to_quote,
        quote_pairs=spot.quote_pairs,
        quote_rates=quote_rates,
    )


def log_resolution(label: str, resolution: SymbolResolution) -> None:
    for base, metas in resolution.meta.items():
        for rank, meta in enumerate(metas, start=1):
            note = f" ({meta.note})" if meta.note else ""
            LOGGER.info(
                "%s resolved %s #%s -> %s [%s, vol=%s, usd=%s]%s",
                label,
                base,
                rank,
                meta.resolved,
                meta.quote,
                _format_volume(meta.quote_volume),
                _format_volume(meta.normalized_volume),
                note,
            )
    if resolution.missing:
        LOGGER.warning("%s missing symbols: %s", label, ",".join(resolution.missing))


def _resolve(
    exchange_info: dict,
    ticker: list[dict],
    bases: list[str],
    *,
    allow_prefix: bool,
    top_n: int,
) -> SymbolResolution:
    trading = {}
    for entry in exchange_info.get("symbols", []):
        if entry.get("status") != "TRADING":
            continue
        trading[entry["symbol"]] = {
            "base": entry["baseAsset"],
            "quote": entry["quoteAsset"],
        }

    volumes = {item["symbol"]: float(item.get("quoteVolume", 0.0)) for item in ticker}
    last_price = {item["symbol"]: float(item.get("lastPrice", 0.0)) for item in ticker}
    quote_rates = _build_quote_rates(trading, last_price)

    resolved: dict[str, list[str]] = {}
    meta: dict[str, list[SymbolMeta]] = {}
    missing: list[str] = []

    for base in bases:
        candidates: list[tuple[str, dict, str | None]] = []
        prefix_symbol = f"1000{base}"
        for sym, info in trading.items():
            if info["base"] == base:
                candidates.append((sym, info, None))
            elif allow_prefix and info["base"] == prefix_symbol:
                candidates.append((sym, info, "1000-prefix"))

        if not candidates:
            missing.append(base)
            continue

        ranked: list[SymbolMeta] = []
        for sym, info, note in candidates:
            quote = info["quote"]
            rate = quote_rates.get(quote)
            if rate is None:
                continue
            volume = volumes.get(sym, 0.0)
            normalized = volume * rate
            ranked.append(
                SymbolMeta(
                    base=base,
                    resolved=sym,
                    quote=quote,
                    quote_volume=volume,
                    quote_to_usdt=rate,
                    normalized_volume=normalized,
                    note=note,
                )
            )

        ranked.sort(key=lambda item: item.normalized_volume, reverse=True)
        if len(ranked) < top_n:
            raise ValueError(f"Not enough normalized pairs for {base}.")

        selected = ranked[:top_n]
        resolved[base] = [meta.resolved for meta in selected]
        meta[base] = selected

    return SymbolResolution(resolved=resolved, meta=meta, missing=missing, quote_pairs={})


def _build_quote_pairs(exchange_info: dict, quotes: set[str]) -> dict[str, QuotePair]:
    trading = {}
    for entry in exchange_info.get("symbols", []):
        if entry.get("status") != "TRADING":
            continue
        trading[entry["symbol"]] = {
            "base": entry["baseAsset"],
            "quote": entry["quoteAsset"],
        }

    quote_pairs: dict[str, QuotePair] = {}
    for quote in quotes:
        if quote in USD_QUOTES:
            continue
        direct = None
        inverse = None
        for symbol, info in trading.items():
            if info["base"] == quote and info["quote"] == "USDT":
                direct = symbol
                break
        if direct is None:
            for symbol, info in trading.items():
                if info["base"] == "USDT" and info["quote"] == quote:
                    inverse = symbol
                    break
        if direct is not None:
            quote_pairs[quote] = QuotePair(symbol=direct, invert=False)
        elif inverse is not None:
            quote_pairs[quote] = QuotePair(symbol=inverse, invert=True)
        else:
            LOGGER.warning("No USDT quote pair found for %s; rate updates may be stale.", quote)
    return quote_pairs


def _fetch_json(url: str) -> dict | list:
    request = Request(url, headers={"User-Agent": "flow_lens"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _format_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}"


def _build_quote_rates(
    trading: Mapping[str, Mapping[str, str]],
    last_price: Mapping[str, float],
) -> dict[str, float]:
    rates: dict[str, float] = {"USDT": 1.0}
    for quote in USD_QUOTES:
        rates[quote] = 1.0

    for symbol, info in trading.items():
        base = info["base"]
        quote = info["quote"]
        price = last_price.get(symbol, 0.0)
        if price <= 0:
            continue
        if quote == "USDT":
            rates[base] = price
        if base == "USDT":
            rates[quote] = 1.0 / price

    btc_usdt = rates.get("BTC")
    eth_usdt = rates.get("ETH")
    if btc_usdt is None and eth_usdt is None:
        return rates

    for symbol, info in trading.items():
        base = info["base"]
        quote = info["quote"]
        price = last_price.get(symbol, 0.0)
        if price <= 0:
            continue
        if btc_usdt is not None:
            if quote == "BTC" and base not in rates:
                rates[base] = price * btc_usdt
            if base == "BTC" and quote not in rates:
                rates[quote] = (1.0 / price) * btc_usdt
        if eth_usdt is not None:
            if quote == "ETH" and base not in rates:
                rates[base] = price * eth_usdt
            if base == "ETH" and quote not in rates:
                rates[quote] = (1.0 / price) * eth_usdt
    return rates
