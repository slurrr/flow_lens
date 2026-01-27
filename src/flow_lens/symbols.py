from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolMeta:
    base: str
    resolved: str
    quote: str
    quote_volume: float
    note: str | None = None


@dataclass(frozen=True)
class SymbolResolution:
    resolved: dict[str, str]
    meta: dict[str, SymbolMeta]
    missing: list[str]


@dataclass(frozen=True)
class SymbolMaps:
    spot_base_to_actual: dict[str, str]
    perp_base_to_actual: dict[str, str]
    spot_actual_to_base: dict[str, str]
    perp_actual_to_base: dict[str, str]


class BinanceSymbolResolver:
    def resolve_spot(self, bases: Iterable[str]) -> SymbolResolution:
        exchange = _fetch_json("https://api.binance.com/api/v3/exchangeInfo")
        ticker = _fetch_json("https://api.binance.com/api/v3/ticker/24hr")
        if not isinstance(exchange, dict) or not isinstance(ticker, list):
            raise ValueError("Unexpected spot exchange response.")
        return _resolve(exchange, ticker, list(bases), allow_prefix=False)

    def resolve_perp(self, bases: Iterable[str]) -> SymbolResolution:
        exchange = _fetch_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
        ticker = _fetch_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not isinstance(exchange, dict) or not isinstance(ticker, list):
            raise ValueError("Unexpected perp exchange response.")
        return _resolve(exchange, ticker, list(bases), allow_prefix=True)


def build_symbol_maps(
    spot: SymbolResolution, perp: SymbolResolution
) -> SymbolMaps:
    spot_actual_to_base = {actual: base for base, actual in spot.resolved.items()}
    perp_actual_to_base = {actual: base for base, actual in perp.resolved.items()}
    return SymbolMaps(
        spot_base_to_actual=spot.resolved,
        perp_base_to_actual=perp.resolved,
        spot_actual_to_base=spot_actual_to_base,
        perp_actual_to_base=perp_actual_to_base,
    )


def log_resolution(label: str, resolution: SymbolResolution) -> None:
    for base, meta in resolution.meta.items():
        note = f" ({meta.note})" if meta.note else ""
        LOGGER.info(
            "%s resolved %s -> %s [%s, vol=%s]%s",
            label,
            base,
            meta.resolved,
            meta.quote,
            _format_volume(meta.quote_volume),
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

    resolved: dict[str, str] = {}
    meta: dict[str, SymbolMeta] = {}
    missing: list[str] = []

    for base in bases:
        symbol = base
        if symbol in trading:
            info = trading[symbol]
            volume = volumes.get(symbol, 0.0)
            resolved[base] = symbol
            meta[base] = SymbolMeta(
                base=base,
                resolved=symbol,
                quote=info["quote"],
                quote_volume=volume,
            )
            continue

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

        best = max(
            candidates,
            key=lambda item: volumes.get(item[0], 0.0),
        )
        symbol, info, note = best
        volume = volumes.get(symbol, 0.0)
        resolved[base] = symbol
        meta[base] = SymbolMeta(
            base=base,
            resolved=symbol,
            quote=info["quote"],
            quote_volume=volume,
            note=note,
        )

    return SymbolResolution(resolved=resolved, meta=meta, missing=missing)


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
