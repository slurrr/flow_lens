from __future__ import annotations

import asyncio
import logging
import time
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
    base_symbol: str | None
    event: Event


@dataclass(frozen=True)
class AdapterStats:
    message_count: int
    dropped_count: int
    reconnect_count: int
    active_pairs: int
    total_pairs: int
    tbt_ms: float | None


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
        self._message_count = 0
        self._dropped_count = 0
        self._reconnect_count = 0
        self._tbt_mean_ms: dict[str, float] = {}
        self._tbt_count: dict[str, int] = {}
        self._connected_since_ms: int | None = None
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

    def stats_for(self, now_ms: int, *, symbols: Iterable[str]) -> AdapterStats:
        active_pairs, total_pairs = self._activity(now_ms, symbols)
        tbt_ms = self._tbt_min(symbols)
        return AdapterStats(
            message_count=self._message_count,
            dropped_count=self._dropped_count,
            reconnect_count=self._reconnect_count,
            active_pairs=active_pairs,
            total_pairs=total_pairs,
            tbt_ms=tbt_ms,
        )

    def tbt_min(self, symbols: Iterable[str]) -> float | None:
        return self._tbt_min(symbols)

    async def stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            try:
                async for item in self._stream_once():
                    yield item
            except Exception:
                self._connected = False
                self._reconnect_count += 1
                self._logger.exception("Adapter stream failed; reconnecting.")
                await asyncio.sleep(self._reconnect_delay_s)

    def _stream_once(self) -> AsyncIterator[AdapterEvent]:
        raise NotImplementedError

    def _mark_connected(self) -> None:
        self._connected = True
        self._connected_since_ms = int(time.time() * 1000)
        self._logger.info("Adapter connected (%s symbols).", len(self._symbols))

    def _mark_disconnected(self) -> None:
        self._connected = False
        self._logger.warning("Adapter disconnected.")

    def _mark_event(self, symbol: str, timestamp_ms: int) -> None:
        if symbol not in self._last_event_ms:
            self._logger.info("First event received for %s.", symbol)
        last_ms = self._last_event_ms.get(symbol)
        if last_ms is not None:
            delta = timestamp_ms - last_ms
            if delta > 0:
                count = self._tbt_count.get(symbol, 0)
                mean = self._tbt_mean_ms.get(symbol, 0.0)
                new_mean = (mean * count + delta) / (count + 1)
                self._tbt_mean_ms[symbol] = new_mean
                self._tbt_count[symbol] = count + 1
        self._last_event_ms[symbol] = timestamp_ms

    def _mark_message(self, *, dropped: bool) -> None:
        self._message_count += 1
        if dropped:
            self._dropped_count += 1

    def _activity(self, now_ms: int, symbols: Iterable[str]) -> tuple[int, int]:
        if not self._connected:
            total = len(tuple(symbols))
            return 0, total
        symbols_list = list(symbols)
        active_pairs = 0
        for symbol in symbols_list:
            last_ms = self._last_event_ms.get(symbol)
            if last_ms is None:
                continue
            if now_ms - last_ms <= self._stale_after_ms:
                active_pairs += 1
        return active_pairs, len(symbols_list)

    def _tbt_min(self, symbols: Iterable[str]) -> float | None:
        minimum: float | None = None
        for symbol in symbols:
            mean = self._tbt_mean_ms.get(symbol)
            if mean is None:
                continue
            if minimum is None or mean < minimum:
                minimum = mean
        return minimum
