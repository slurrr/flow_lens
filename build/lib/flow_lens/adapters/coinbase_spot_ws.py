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


class CoinbaseSpotWSAdapter(BaseAdapter):
    def __init__(self, *, symbols: list[str]) -> None:
        products = [_normalize_product(symbol) for symbol in symbols]
        super().__init__(symbols=products)
        self._product_to_base = {product: product.split("-")[0] for product in products}
        self._product_set = set(products)

    async def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        stream_url = "wss://ws-feed.exchange.coinbase.com"
        LOGGER.info("Connecting to Coinbase spot stream (%s).", stream_url)
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
            subscribe = {
                "type": "subscribe",
                "product_ids": sorted(self._product_set),
                "channels": ["matches"],
            }
            await ws.send(json.dumps(subscribe))
            self._mark_connected()
            try:
                async for message in ws:
                    payload = json.loads(message)
                    msg_type = payload.get("type")
                    if msg_type == "error":
                        self._mark_message(dropped=True)
                        LOGGER.error("Coinbase spot error: %s", payload)
                        raise RuntimeError("Coinbase spot stream error.")
                    if msg_type != "match":
                        self._mark_message(dropped=False)
                        continue
                    product_id = payload.get("product_id")
                    if not isinstance(product_id, str):
                        self._mark_message(dropped=True)
                        continue
                    product_id = product_id.upper()
                    if product_id not in self._product_set:
                        self._mark_message(dropped=True)
                        continue
                    price = float(payload["price"])
                    size = float(payload["size"])
                    ts_recv_ms = self._clamp_recv_timestamp_ms(
                        product_id,
                        int(time.time_ns() // 1_000_000),
                    )
                    venue_ts_ms = normalize_venue_timestamp_ms(payload.get("time"))
                    side_value = str(payload.get("side", "")).lower()
                    if side_value not in {"buy", "sell"}:
                        self._mark_message(dropped=True)
                        continue
                    aggressor_side = cast(AggressorSide, side_value)
                    effort_value = price * size
                    event = Event(
                        timestamp=ts_recv_ms,
                        source_id="coinbase_spot",
                        side_type="spot",
                        aggressor_side=aggressor_side,
                        effort_value=effort_value,
                        price=price,
                        base_qty=size,
                        quote_qty=effort_value,
                        venue_timestamp_ms=venue_ts_ms,
                        trade_id=str(payload.get("trade_id"))
                        if payload.get("trade_id") is not None
                        else None,
                    )
                    self._mark_event(product_id, ts_recv_ms)
                    self._mark_message(dropped=False)
                    yield AdapterEvent(
                        symbol=product_id,
                        base_symbol=self._product_to_base.get(product_id),
                        event=event,
                    )
            finally:
                self._mark_disconnected()


def _normalize_product(symbol: str) -> str:
    candidate = symbol.strip().upper()
    if "-" in candidate:
        return candidate
    if candidate.endswith("USD") and len(candidate) > 3:
        base = candidate[:-3]
        return f"{base}-USD"
    return f"{candidate}-USD"
