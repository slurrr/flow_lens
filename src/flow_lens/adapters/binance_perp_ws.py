from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import websockets

from flow_lens.adapters.base import AdapterEvent, BaseAdapter
from flow_lens.models.event import Event

LOGGER = logging.getLogger(__name__)


class BinancePerpWSAdapter(BaseAdapter):
    def __init__(self, *, symbols: list[str]) -> None:
        super().__init__(symbols=symbols)
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
                        LOGGER.error("Binance perp error: %s", payload)
                        raise RuntimeError("Binance perp stream error.")
                    data = payload.get("data", {})
                    symbol = data.get("s")
                    if symbol is None:
                        continue
                    price = float(data["p"])
                    quantity = float(data["q"])
                    timestamp = int(data["T"])
                    effort_value = price * quantity
                    event = Event(
                        timestamp=timestamp,
                        source_id="binance_perp",
                        side_type="perp",
                        effort_value=effort_value,
                        price=price,
                    )
                    self._mark_event(symbol, timestamp)
                    yield AdapterEvent(symbol=symbol, event=event)
            finally:
                self._mark_disconnected()
