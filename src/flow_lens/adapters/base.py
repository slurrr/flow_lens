from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Iterable

from flow_lens.models.event import Event


class AdapterStatus(str, Enum):
    CONNECTED = "connected"
    STALE = "stale"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class AdapterEvent:
    symbol: str
    event: Event


class BaseAdapter:
    def __init__(
        self,
        *,
        symbols: Iterable[str],
        stale_after_ms: int = 5_000,
        reconnect_delay_s: float = 2.0,
    ) -> None:
        self._symbols = tuple(symbol.upper() for symbol in symbols)
        self._symbol_set = set(self._symbols)
        self._stale_after_ms = stale_after_ms
        self._reconnect_delay_s = reconnect_delay_s
        self._connected = False
        self._last_event_ms: dict[str, int] = {}
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def has_symbol(self, symbol: str) -> bool:
        return symbol.upper() in self._symbol_set

    def missing_symbols(self) -> tuple[str, ...]:
        return tuple(symbol for symbol in self._symbols if symbol not in self._last_event_ms)

    def status(self, symbol: str, now_ms: int) -> AdapterStatus:
        if not self._connected:
            return AdapterStatus.DISCONNECTED
        last_ms = self._last_event_ms.get(symbol)
        if last_ms is None:
            return AdapterStatus.STALE
        if now_ms - last_ms > self._stale_after_ms:
            return AdapterStatus.STALE
        return AdapterStatus.CONNECTED

    async def stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            try:
                async for item in self._stream_once():
                    yield item
            except Exception:
                self._connected = False
                self._logger.exception("Adapter stream failed; reconnecting.")
                await asyncio.sleep(self._reconnect_delay_s)

    def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        raise NotImplementedError

    def _mark_connected(self) -> None:
        self._connected = True
        self._logger.info("Adapter connected (%s symbols).", len(self._symbols))

    def _mark_disconnected(self) -> None:
        self._connected = False
        self._logger.warning("Adapter disconnected.")

    def _mark_event(self, symbol: str, timestamp_ms: int) -> None:
        if symbol not in self._last_event_ms:
            self._logger.info("First event received for %s.", symbol)
        self._last_event_ms[symbol] = timestamp_ms
