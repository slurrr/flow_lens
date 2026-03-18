from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import cast

import websockets

from flow_lens.dist_state.models import DistKlineCloseEvent, DistOiSamplerSnapshot, DistTimeframe

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DistFeedConfig:
    symbol: str
    source_id: str
    timeframes: tuple[DistTimeframe, ...]
    oi_poll_interval_ms: int
    oi_verify_enabled: bool
    oi_verify_timeframes: tuple[DistTimeframe, ...]
    oi_verify_timeout_ms: int
    oi_verify_max_rate_per_min: int


class BinancePerpDistFeed:
    def __init__(self, config: DistFeedConfig) -> None:
        self._config = config
        self._queue: queue.Queue[DistKlineCloseEvent] = queue.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._oi_live_url = "https://fapi.binance.com/fapi/v1/openInterest"
        self._sampler_snapshot: DistOiSamplerSnapshot | None = None
        self._sampler_last_order_key: tuple[int, int] | None = None
        self._sample_seq = 0
        self._verify_timestamps: deque[float] = deque()

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
                sampler_task = asyncio.create_task(self._run_oi_sampler())
                stream_task = asyncio.create_task(self._run_stream())
                done, pending = await asyncio.wait(
                    {sampler_task, stream_task},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
            except Exception:
                LOGGER.exception("Dist-state feed failed; reconnecting.")
                await asyncio.sleep(2.0)

    async def _run_oi_sampler(self) -> None:
        interval_s = max(self._config.oi_poll_interval_ms, 100) / 1000.0
        while True:
            snapshot = await asyncio.to_thread(
                self._fetch_live_oi,
                self._config.oi_verify_timeout_ms,
            )
            if snapshot is not None:
                self._accept_sampler_snapshot(snapshot)
            await asyncio.sleep(interval_s)

    async def _run_stream(self) -> None:
        # Binance Futures kline stream expects the full contract symbol (e.g. btcusdt).
        symbol = f"{self._config.symbol.lower()}usdt"
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
                if not isinstance(kline, dict) or not bool(kline.get("x", False)):
                    continue
                tf_raw = str(kline.get("i", ""))
                if tf_raw not in {"3m", "15m", "1h", "4h"}:
                    continue
                tf = cast(DistTimeframe, tf_raw)
                recv_ms = int(time.time_ns() // 1_000_000)
                sampler_snapshot = self._sampler_snapshot
                verify_snapshot: DistOiSamplerSnapshot | None = None
                if self._should_verify(tf):
                    verify_snapshot = await asyncio.to_thread(
                        self._fetch_live_oi,
                        self._config.oi_verify_timeout_ms,
                    )
                    if verify_snapshot is not None:
                        self._accept_sampler_snapshot(verify_snapshot)
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
                        sampler_snapshot=sampler_snapshot,
                        verify_snapshot=verify_snapshot,
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                self._queue.put(event)

    def _should_verify(self, tf: DistTimeframe) -> bool:
        if not self._config.oi_verify_enabled:
            return False
        if tf not in self._config.oi_verify_timeframes:
            return False
        now = time.monotonic()
        window_start = now - 60.0
        while self._verify_timestamps and self._verify_timestamps[0] < window_start:
            self._verify_timestamps.popleft()
        if len(self._verify_timestamps) >= self._config.oi_verify_max_rate_per_min:
            return False
        self._verify_timestamps.append(now)
        return True

    def _accept_sampler_snapshot(self, snapshot: DistOiSamplerSnapshot) -> None:
        primary_ts = snapshot.venue_time_ms if snapshot.venue_time_ms is not None else snapshot.ts_recv_ms
        order_key = (primary_ts, snapshot.sample_seq)
        if self._sampler_last_order_key is not None and order_key <= self._sampler_last_order_key:
            return
        self._sampler_last_order_key = order_key
        self._sampler_snapshot = snapshot

    def _fetch_live_oi(self, timeout_ms: int) -> DistOiSamplerSnapshot | None:
        params = {"symbol": f"{self._config.symbol.upper()}USDT"}
        query = urllib.parse.urlencode(params)
        full = f"{self._oi_live_url}?{query}"
        req = urllib.request.Request(full, method="GET")
        timeout_s = max(timeout_ms, 100) / 1000.0
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            LOGGER.debug("Dist-state OI fetch failed: %s", full, exc_info=True)
            return None
        if not isinstance(payload, dict):
            return None
        oi_raw = payload.get("openInterest")
        if not isinstance(oi_raw, (int, float, str)):
            return None
        try:
            oi = float(oi_raw)
        except (TypeError, ValueError):
            return None
        venue_time_raw = payload.get("time")
        venue_time_ms: int | None = None
        if isinstance(venue_time_raw, (int, float)):
            venue_time_ms = int(venue_time_raw)
        self._sample_seq += 1
        return DistOiSamplerSnapshot(
            oi=oi,
            venue_time_ms=venue_time_ms,
            ts_recv_ms=int(time.time_ns() // 1_000_000),
            sample_seq=self._sample_seq,
        )
