from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, cast

import websockets

from flow_lens.adapters.base import AdapterEvent, BaseAdapter
from flow_lens.adapters.time_utils import normalize_venue_timestamp_ms
from flow_lens.models.event import AggressorSide, Event

LOGGER = logging.getLogger(__name__)


class BybitPerpWSAdapter(BaseAdapter):
    def __init__(self, *, symbols: list[str], symbol_to_base: dict[str, str]) -> None:
        super().__init__(symbols=symbols)
        self._symbol_to_base = {
            symbol.upper(): base.upper() for symbol, base in symbol_to_base.items()
        }

    async def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        stream_url = "wss://stream.bybit.com/v5/public/linear"
        LOGGER.info("Connecting to Bybit perp stream (%s).", stream_url)
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
            subscribe = {
                "op": "subscribe",
                "args": [f"publicTrade.{symbol}" for symbol in self._symbols],
            }
            await ws.send(json.dumps(subscribe))
            self._mark_connected()
            try:
                async for message in ws:
                    payload = json.loads(message)
                    op = payload.get("op")
                    if op == "subscribe":
                        if payload.get("success") is False:
                            self._mark_message(dropped=True)
                            LOGGER.error("Bybit perp subscribe error: %s", payload)
                            raise RuntimeError("Bybit perp subscribe error.")
                        self._mark_message(dropped=False)
                        continue
                    if op == "error" or payload.get("success") is False:
                        self._mark_message(dropped=True)
                        LOGGER.error("Bybit perp error: %s", payload)
                        raise RuntimeError("Bybit perp stream error.")
                    data = payload.get("data")
                    if not isinstance(data, list):
                        self._mark_message(dropped=False)
                        continue
                    envelope_ts = payload.get("ts")
                    emitted = False
                    for row in data:
                        symbol = str(row.get("s", "")).upper()
                        if not symbol or not self.has_symbol(symbol):
                            continue
                        venue_ts_ms = (
                            normalize_venue_timestamp_ms(row.get("T"))
                            or normalize_venue_timestamp_ms(envelope_ts)
                        )
                        ts_recv_ms = self._clamp_recv_timestamp_ms(
                            symbol,
                            int(time.time_ns() // 1_000_000),
                        )
                        side_value = str(row.get("S", "")).lower()
                        if side_value not in {"buy", "sell"}:
                            continue
                        try:
                            price = float(row["p"])
                            size = float(row["v"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        effort_value = price * size
                        aggressor_side = cast(AggressorSide, side_value)
                        event = Event(
                            timestamp=ts_recv_ms,
                            source_id="bybit_perp",
                            side_type="perp",
                            aggressor_side=aggressor_side,
                            effort_value=effort_value,
                            price=price,
                            venue_timestamp_ms=venue_ts_ms,
                            trade_id=str(row.get("i")) if row.get("i") is not None else None,
                        )
                        self._mark_event(symbol, ts_recv_ms)
                        emitted = True
                        yield AdapterEvent(
                            symbol=symbol,
                            base_symbol=self._symbol_to_base.get(symbol),
                            event=event,
                        )
                    self._mark_message(dropped=not emitted)
            finally:
                self._mark_disconnected()
