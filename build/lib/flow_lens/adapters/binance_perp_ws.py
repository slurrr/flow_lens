from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import websockets

from flow_lens.adapters.base import AdapterEvent, BaseAdapter
from flow_lens.adapters.time_utils import normalize_venue_timestamp_ms
from flow_lens.models.event import Event

LOGGER = logging.getLogger(__name__)


class BinancePerpWSAdapter(BaseAdapter):
    def __init__(self, *, symbols: list[str], symbol_to_base: dict[str, str]) -> None:
        super().__init__(symbols=symbols)
        self._symbol_to_base = {
            symbol.upper(): base.upper() for symbol, base in symbol_to_base.items()
        }
        self._streams = [f"{symbol.lower()}@aggTrade" for symbol in symbols]

    async def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        stream_url = "wss://fstream.binance.com/stream?streams=" + "/".join(
            self._streams
        )
        LOGGER.info("Connecting to Binance perp stream (%s).", stream_url)
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
            self._mark_connected()
            try:
                async for message in ws:
                    payload = json.loads(message)
                    if "code" in payload:
                        self._mark_message(dropped=True)
                        LOGGER.error("Binance perp error: %s", payload)
                        raise RuntimeError("Binance perp stream error.")
                    data = payload.get("data", {})
                    symbol = data.get("s")
                    if symbol is None:
                        self._mark_message(dropped=True)
                        continue
                    symbol_upper = symbol.upper()
                    price = float(data["p"])
                    quantity = float(data["q"])
                    ts_recv_ms = self._clamp_recv_timestamp_ms(
                        symbol_upper,
                        int(time.time_ns() // 1_000_000),
                    )
                    venue_ts_ms = normalize_venue_timestamp_ms(data.get("T"))
                    aggressor_side = "sell" if data.get("m") else "buy"
                    effort_value = price * quantity
                    event = Event(
                        timestamp=ts_recv_ms,
                        source_id="binance_perp",
                        side_type="perp",
                        aggressor_side=aggressor_side,
                        effort_value=effort_value,
                        price=price,
                        base_qty=quantity,
                        quote_qty=effort_value,
                        venue_timestamp_ms=venue_ts_ms,
                        trade_id=str(data.get("a")) if data.get("a") is not None else None,
                    )
                    self._mark_event(symbol_upper, ts_recv_ms)
                    self._mark_message(dropped=False)
                    yield AdapterEvent(
                        symbol=symbol,
                        base_symbol=self._symbol_to_base.get(symbol_upper),
                        event=event,
                    )
            finally:
                self._mark_disconnected()
