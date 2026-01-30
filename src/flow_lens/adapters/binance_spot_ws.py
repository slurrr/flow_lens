from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import websockets

from flow_lens.adapters.base import AdapterEvent, BaseAdapter
from flow_lens.models.event import Event
from flow_lens.symbols import USD_QUOTES, QuotePair

LOGGER = logging.getLogger(__name__)


class BinanceSpotWSAdapter(BaseAdapter):
    def __init__(
        self,
        *,
        symbols: list[str],
        symbol_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
    ) -> None:
        super().__init__(symbols=symbols)
        self._symbol_quotes = {symbol.upper(): quote.upper() for symbol, quote in symbol_quotes.items()}
        self._quote_pairs = {quote.upper(): pair for quote, pair in quote_pairs.items()}
        self._quote_pair_by_symbol = {
            pair.symbol.upper(): quote.upper() for quote, pair in self._quote_pairs.items()
        }
        self._quote_rates = {quote.upper(): float(rate) for quote, rate in quote_rates.items()}
        for quote in USD_QUOTES:
            self._quote_rates[quote] = 1.0
        stream_symbols = set(symbols)
        stream_symbols.update(pair.symbol for pair in quote_pairs.values())
        self._streams = [f"{symbol.lower()}@aggTrade" for symbol in sorted(stream_symbols)]

    async def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        stream_url = "wss://stream.binance.com:9443/stream?streams=" + "/".join(
            self._streams
        )
        LOGGER.info("Connecting to Binance spot stream (%s).", stream_url)
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
            self._mark_connected()
            try:
                async for message in ws:
                    payload = json.loads(message)
                    if "code" in payload:
                        self._mark_message(dropped=True)
                        LOGGER.error("Binance spot error: %s", payload)
                        raise RuntimeError("Binance spot stream error.")
                    data = payload.get("data", {})
                    symbol = data.get("s")
                    if symbol is None:
                        self._mark_message(dropped=True)
                        continue
                    symbol_upper = symbol.upper()
                    price = float(data["p"])
                    quantity = float(data["q"])
                    timestamp = int(data["T"])
                    aggressor_side = "sell" if data.get("m") else "buy"
                    quote_asset = self._quote_pair_by_symbol.get(symbol_upper)
                    if quote_asset is not None:
                        pair = self._quote_pairs[quote_asset]
                        if price > 0:
                            rate = 1.0 / price if pair.invert else price
                            self._quote_rates[quote_asset] = rate
                        self._mark_message(dropped=False)
                        continue
                    quote = self._symbol_quotes.get(symbol_upper)
                    if quote is None:
                        self._mark_message(dropped=True)
                        continue
                    rate = self._quote_rates.get(quote)
                    if rate is None:
                        self._mark_message(dropped=True)
                        continue
                    price_usdt = price * rate
                    effort_value = price_usdt * quantity
                    event = Event(
                        timestamp=timestamp,
                        source_id="binance_spot",
                        side_type="spot",
                        aggressor_side=aggressor_side,
                        effort_value=effort_value,
                        price=price_usdt,
                    )
                    self._mark_event(symbol, timestamp)
                    self._mark_message(dropped=False)
                    yield AdapterEvent(symbol=symbol, event=event)
            finally:
                self._mark_disconnected()
