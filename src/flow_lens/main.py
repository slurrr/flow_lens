from __future__ import annotations

import asyncio
import curses
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from flow_lens.adapters import (
    AdapterEvent,
    AdapterStatus,
    BinancePerpWSAdapter,
    BinanceSpotWSAdapter,
)
from flow_lens.config import AppConfig, load_app_config
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.constants import Defaults
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import Event
from flow_lens.symbols import (
    BinanceSymbolResolver,
    SymbolMaps,
    build_symbol_maps,
    log_resolution,
)
from flow_lens.tui.input import InputState
from flow_lens.tui.renderer import Renderer, RendererConfig


@dataclass
class RuntimeState:
    loops: dict[str, EngineLoop]
    last_state: dict[str, StateSnapshot | None]
    pending: dict[str, list[Event]]
    symbol_maps: SymbolMaps


def main() -> None:
    _configure_logging()
    curses.wrapper(_run)


def _run(stdscr: "curses.window") -> None:
    stdscr.nodelay(True)
    stdscr.keypad(True)
    curses.curs_set(0)

    defaults = Defaults()
    update_interval_s = defaults.time_domain.update_window_seconds
    window_ms = int(defaults.time_domain.update_window_seconds * 1000)

    config = load_app_config()
    base_symbols = _collect_symbols(config)

    resolver = BinanceSymbolResolver()
    spot_resolution = resolver.resolve_spot(config.adapters["binance_spot"].symbols)
    perp_resolution = resolver.resolve_perp(config.adapters["binance_perp"].symbols)
    log_resolution("Spot", spot_resolution)
    log_resolution("Perp", perp_resolution)

    symbol_maps = build_symbol_maps(spot_resolution, perp_resolution)
    queue_events: queue.Queue[AdapterEvent] = queue.Queue()
    supervisor = AdapterSupervisor(queue_events)
    supervisor.start(
        spot_symbols=list(symbol_maps.spot_base_to_actual.values()),
        perp_symbols=list(symbol_maps.perp_base_to_actual.values()),
    )

    runtime = _init_runtime(base_symbols, window_ms, symbol_maps)
    input_state = InputState(symbols=base_symbols)
    renderer = Renderer(RendererConfig())

    last_update = time.monotonic()
    last_frame = last_update
    start_time = time.monotonic()
    last_resolve = time.monotonic()
    reported_missing = False

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        if key != -1:
            input_state.handle_key(key)

        _drain_events(queue_events, runtime)

        now = time.monotonic()
        if now - last_update >= update_interval_s:
            last_update = now
            now_ms = int(time.time() * 1000)
            _update_state(runtime, now_ms)

        if not reported_missing and now - start_time > 30:
            _report_missing(runtime, supervisor)
            reported_missing = True

        if now - last_resolve > 15 * 60:
            last_resolve = now
            _refresh_resolution(config, runtime, supervisor)

        if now - last_frame >= 1 / 30.0:
            last_frame = now
            symbol = input_state.symbol
            now_ms = int(time.time() * 1000)
            status_spot = _adapter_status(symbol, now_ms, supervisor.spot, runtime.symbol_maps.spot_base_to_actual)
            status_perp = _adapter_status(symbol, now_ms, supervisor.perp, runtime.symbol_maps.perp_base_to_actual)
            renderer.draw(
                stdscr,
                symbol,
                runtime.last_state.get(symbol),
                status_spot=status_spot,
                status_perp=status_perp,
                search_mode=input_state.search_mode,
                search_buffer=input_state.search_buffer,
            )

        time.sleep(0.001)


def _collect_symbols(config: AppConfig) -> list[str]:
    symbols: set[str] = set()
    for adapter in config.adapters.values():
        symbols.update(adapter.symbols)
    return sorted(symbols)


class AdapterSupervisor:
    def __init__(self, queue_events: queue.Queue[AdapterEvent]) -> None:
        self._queue_events = queue_events
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.spot: BinanceSpotWSAdapter | None = None
        self.perp: BinancePerpWSAdapter | None = None

    def start(self, *, spot_symbols: list[str], perp_symbols: list[str]) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)
        self.update_symbols(spot_symbols=spot_symbols, perp_symbols=perp_symbols)

    def update_symbols(self, *, spot_symbols: list[str], perp_symbols: list[str]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._restart(spot_symbols=spot_symbols, perp_symbols=perp_symbols),
            self._loop,
        )

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _restart(self, *, spot_symbols: list[str], perp_symbols: list[str]) -> None:
        await self._cancel_tasks()
        spot = BinanceSpotWSAdapter(symbols=spot_symbols)
        perp = BinancePerpWSAdapter(symbols=perp_symbols)
        with self._lock:
            self.spot = spot
            self.perp = perp
        self._tasks = [
            asyncio.create_task(self._consume(spot)),
            asyncio.create_task(self._consume(perp)),
        ]

    async def _cancel_tasks(self) -> None:
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _consume(self, adapter) -> None:
        try:
            async for item in adapter.stream():
                self._queue_events.put(item)
        except asyncio.CancelledError:
            adapter._mark_disconnected()
            raise


def _init_runtime(symbols: list[str], window_ms: int, symbol_maps: SymbolMaps) -> RuntimeState:
    loops: dict[str, EngineLoop] = {}
    last_state: dict[str, StateSnapshot | None] = {}
    pending: dict[str, list[Event]] = {symbol: [] for symbol in symbols}

    for symbol in symbols:
        buffer = RollingEventBuffer(window_delta_ms=window_ms)
        engine = StateEngine()
        loops[symbol] = EngineLoop(symbol=symbol, buffer=buffer, engine=engine)
        last_state[symbol] = None

    return RuntimeState(loops=loops, last_state=last_state, pending=pending, symbol_maps=symbol_maps)


def _drain_events(queue_events: queue.Queue[AdapterEvent], runtime: RuntimeState) -> None:
    while True:
        try:
            item = queue_events.get_nowait()
        except queue.Empty:
            break
        base_symbol = _map_to_base(item, runtime.symbol_maps)
        if base_symbol is None or base_symbol not in runtime.pending:
            continue
        runtime.pending[base_symbol].append(item.event)


def _update_state(runtime: RuntimeState, now_ms: int) -> None:
    for symbol, loop in runtime.loops.items():
        events = runtime.pending[symbol]
        runtime.pending[symbol] = []
        runtime.last_state[symbol] = loop.step(events, now_ms)


def _adapter_status(
    symbol: str,
    now_ms: int,
    adapter: BinanceSpotWSAdapter | BinancePerpWSAdapter | None,
    mapping: dict[str, str],
) -> AdapterStatus:
    if adapter is None:
        return AdapterStatus.DISCONNECTED
    actual = mapping.get(symbol)
    if actual is None:
        return AdapterStatus.DISCONNECTED
    return adapter.status(actual, now_ms)


def _configure_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "flow_lens.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )


def _report_missing(runtime: RuntimeState, supervisor: AdapterSupervisor) -> None:
    if supervisor.spot is not None:
        missing = [
            runtime.symbol_maps.spot_actual_to_base.get(actual, actual)
            for actual in supervisor.spot.missing_symbols()
        ]
        if missing:
            logging.warning("Spot missing symbols: %s", ",".join(missing))
    if supervisor.perp is not None:
        missing = [
            runtime.symbol_maps.perp_actual_to_base.get(actual, actual)
            for actual in supervisor.perp.missing_symbols()
        ]
        if missing:
            logging.warning("Perp missing symbols: %s", ",".join(missing))


def _refresh_resolution(
    config: AppConfig,
    runtime: RuntimeState,
    supervisor: AdapterSupervisor,
) -> None:
    resolver = BinanceSymbolResolver()
    spot_resolution = resolver.resolve_spot(config.adapters["binance_spot"].symbols)
    perp_resolution = resolver.resolve_perp(config.adapters["binance_perp"].symbols)

    _log_promotions("Spot", runtime.symbol_maps.spot_base_to_actual, spot_resolution.resolved)
    _log_promotions("Perp", runtime.symbol_maps.perp_base_to_actual, perp_resolution.resolved)

    symbol_maps = build_symbol_maps(spot_resolution, perp_resolution)
    runtime.symbol_maps = symbol_maps
    supervisor.update_symbols(
        spot_symbols=list(symbol_maps.spot_base_to_actual.values()),
        perp_symbols=list(symbol_maps.perp_base_to_actual.values()),
    )


def _log_promotions(label: str, current: dict[str, str], updated: dict[str, str]) -> None:
    for base, new_symbol in updated.items():
        old_symbol = current.get(base)
        if old_symbol and old_symbol != new_symbol:
            logging.info("%s promoted %s: %s -> %s", label, base, old_symbol, new_symbol)


def _map_to_base(item: AdapterEvent, symbol_maps: SymbolMaps) -> str | None:
    if item.event.source_id == "binance_spot":
        return symbol_maps.spot_actual_to_base.get(item.symbol)
    return symbol_maps.perp_actual_to_base.get(item.symbol)


if __name__ == "__main__":
    main()
