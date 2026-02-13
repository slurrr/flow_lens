from __future__ import annotations

import json
import logging
from typing import AsyncIterator, cast

import websockets

from flow_lens.adapters.base import AdapterEvent, BaseAdapter
from flow_lens.models.event import AggressorSide, Event

LOGGER = logging.getLogger(__name__)


class BybitSpotWSAdapter(BaseAdapter):
    def __init__(self, *, symbols: list[str], symbol_to_base: dict[str, str]) -> None:
        super().__init__(symbols=symbols)
        self._symbol_to_base = {
            symbol.upper(): base.upper() for symbol, base in symbol_to_base.items()
        }

    async def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        stream_url = "wss://stream.bybit.com/v5/public/spot"
        LOGGER.info("Connecting to Bybit spot stream (%s).", stream_url)
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
                            LOGGER.error("Bybit spot subscribe error: %s", payload)
                            raise RuntimeError("Bybit spot subscribe error.")
                        self._mark_message(dropped=False)
                        continue
                    if op == "error" or payload.get("success") is False:
                        self._mark_message(dropped=True)
                        LOGGER.error("Bybit spot error: %s", payload)
                        raise RuntimeError("Bybit spot stream error.")
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
                        timestamp = _parse_timestamp_ms(row.get("T"), envelope_ts)
                        if timestamp is None:
                            continue
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
                            timestamp=timestamp,
                            source_id="bybit_spot",
                            side_type="spot",
                            aggressor_side=aggressor_side,
                            effort_value=effort_value,
                            price=price,
                        )
                        self._mark_event(symbol, timestamp)
                        emitted = True
                        yield AdapterEvent(
                            symbol=symbol,
                            base_symbol=self._symbol_to_base.get(symbol),
                            event=event,
                        )
                    self._mark_message(dropped=not emitted)
            finally:
                self._mark_disconnected()


def _parse_timestamp_ms(value: object, fallback: object) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(fallback, (int, float)):
        return int(fallback)
    if isinstance(fallback, str) and fallback.isdigit():
        return int(fallback)
    return None
