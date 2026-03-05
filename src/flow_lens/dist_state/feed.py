from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import cast

import websockets

from flow_lens.dist_state.models import DistKlineCloseEvent, DistTimeframe

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DistFeedConfig:
    symbol: str
    source_id: str
    timeframes: tuple[DistTimeframe, ...]


class BinancePerpDistFeed:
    def __init__(self, config: DistFeedConfig) -> None:
        self._config = config
        self._queue: queue.Queue[DistKlineCloseEvent] = queue.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("Failed to start dist-state feed loop.")
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)

    def drain(self) -> list[DistKlineCloseEvent]:
        out: list[DistKlineCloseEvent] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                return out

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _run(self) -> None:
        while True:
            try:
                async for event in self._stream_once():
                    self._queue.put(event)
            except Exception:
                LOGGER.exception("Dist-state kline stream failed; reconnecting.")
                await asyncio.sleep(2.0)

    async def _stream_once(self):
        symbol = self._config.symbol.lower()
        streams = "/".join(f"{symbol}@kline_{tf}" for tf in self._config.timeframes)
        stream_url = f"wss://fstream.binance.com/stream?streams={streams}"
        LOGGER.info("Connecting to dist-state Binance kline stream (%s).", stream_url)
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
            async for message in ws:
                payload = json.loads(message)
                data = payload.get("data")
                if not isinstance(data, dict):
                    continue
                kline = data.get("k")
                if not isinstance(kline, dict):
                    continue
                if not bool(kline.get("x", False)):
                    continue
                tf_raw = str(kline.get("i", ""))
                if tf_raw not in {"3m", "15m", "1h", "4h"}:
                    continue
                tf = cast(DistTimeframe, tf_raw)
                recv_ms = int(time.time_ns() // 1_000_000)
                try:
                    event = DistKlineCloseEvent(
                        ts_recv_ms=recv_ms,
                        symbol=self._config.symbol.upper(),
                        source_id=self._config.source_id,
                        tf=tf,
                        kline_open_ms=int(kline["t"]),
                        kline_close_ms=int(kline["T"]),
                        open=float(kline["o"]),
                        high=float(kline["h"]),
                        low=float(kline["l"]),
                        close=float(kline["c"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                yield event
