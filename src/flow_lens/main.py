from __future__ import annotations

import argparse
import asyncio
import curses
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from flow_lens.adapters import (
    AdapterEvent,
    AdapterStatus,
    BinancePerpWSAdapter,
    BinanceSpotWSAdapter,
)
from flow_lens.config import AppConfig, load_app_config
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.constants import (
    Binning,
    Defaults,
    DispScaleConfig,
    EffectivenessDeadband,
    EffectivenessScaling,
    EffortFloor,
    EffortScaleConfig,
    HaloDynamics,
    InputNormalization,
    Smoothing,
    TimeDomain,
)
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import Event
from flow_lens.symbols import (
    BinanceSymbolResolver,
    QuotePair,
    SymbolMaps,
    build_symbol_maps,
    log_resolution,
)
from flow_lens.tui.input import InputState
from flow_lens.tui.metrics import LiveMetrics
from flow_lens.tui.renderer import Renderer, RendererConfig


@dataclass
class RuntimeState:
    loops: dict[str, EngineLoop]
    last_state: dict[str, StateSnapshot | None]
    pending: dict[str, list[Event]]
    symbol_maps: SymbolMaps
    last_event_ms: dict[str, int | None]


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Flow Lens TUI")
    parser.add_argument(
        "--dia",
        action="store_true",
        help="Enable diagnostics logging to JSONL.",
    )
    args = parser.parse_args()
    curses.wrapper(partial(_run, diagnostics_enabled=args.dia))


def _run(stdscr: "curses.window", *, diagnostics_enabled: bool) -> None:
    stdscr.nodelay(True)
    stdscr.keypad(True)
    curses.curs_set(0)

    config = load_app_config()
    update_interval_s = config.update_window_seconds
    window_ms = int(config.update_window_seconds * 1000)
    defaults = Defaults(
        time_domain=TimeDomain(update_window_seconds=config.update_window_seconds),
        effort_floor=EffortFloor(
            rolling_window_ticks=config.effort_floor_ticks,
            multiplier_alpha=config.effort_floor_multiplier,
        ),
        dispersion_metric=config.dispersion_metric,
        smoothing=Smoothing(
            dominance_alpha=config.smoothing_dominance_alpha,
            effectiveness_alpha=config.smoothing_effectiveness_alpha,
        ),
        effectiveness_scaling=EffectivenessScaling(tanh_k=config.tanh_k),
        input_normalization=InputNormalization(
            scale_window_seconds=config.scale_window_seconds,
        ),
        effectiveness_deadband=EffectivenessDeadband(
            disp_scale_multiplier=config.disp_scale_multiplier,
        ),
        disp_scale=DispScaleConfig(
            percentile=config.disp_scale_percentile,
            min_samples=config.disp_scale_min_samples,
        ),
        effort_scale=EffortScaleConfig(
            percentile=config.effort_scale_percentile,
            min_samples=config.effort_scale_min_samples,
        ),
        halo_dynamics=HaloDynamics(
            growth_rate=config.halo_growth_rate,
            decay_rate=config.halo_decay_rate,
        ),
        binning=Binning(
            dot_size_thresholds=config.binning_dot_size_thresholds,
            halo_thresholds=config.binning_halo_thresholds,
            hysteresis_band=config.binning_hysteresis_band,
        ),
    )
    logging.info(
        "Runtime config: tanh_k=%.3f tbt_window_multiplier=%.3f "
        "scale_window_seconds=%.3f disp_scale_multiplier=%.3f "
        "disp_scale_percentile=%.3f disp_scale_min_samples=%d "
        "effort_scale_percentile=%.3f effort_scale_min_samples=%d",
        config.tanh_k,
        config.tbt_window_multiplier,
        config.scale_window_seconds,
        config.disp_scale_multiplier,
        config.disp_scale_percentile,
        config.disp_scale_min_samples,
        config.effort_scale_percentile,
        config.effort_scale_min_samples,
    )
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
        spot_symbols=_flatten(symbol_maps.spot_base_to_actual),
        spot_quotes=symbol_maps.spot_actual_to_quote,
        quote_pairs=symbol_maps.quote_pairs,
        quote_rates=symbol_maps.quote_rates,
        perp_symbols=_flatten(symbol_maps.perp_base_to_actual),
    )

    runtime = _init_runtime(base_symbols, window_ms, symbol_maps, defaults)
    input_state = InputState(symbols=base_symbols)
    renderer = Renderer(RendererConfig())
    live_metrics = LiveMetrics()
    diagnostics: DiagnosticLogger | None = None
    if diagnostics_enabled:
        diagnostics = DiagnosticLogger(
            path=Path("logs/flow_lens_diagnostics.jsonl"),
            symbols={"ASTER", "XPL", "SHIB", "BTC", "ETH", "SOL"},
            tanh_k=config.tanh_k,
        )

    last_update = time.monotonic()
    last_frame = last_update
    start_time = time.monotonic()
    reported_missing = False
    reported_still_missing = False
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
            tbt_cutoffs, tbt_windows = _build_tbt_settings(
                runtime,
                supervisor,
                window_ms,
                config.tbt_window_multiplier,
            )
            _update_state(
                runtime,
                now_ms,
                tbt_cutoffs,
                tbt_windows,
                diagnostics,
                live_metrics,
            )

        if not reported_missing and now - start_time > 30:
            _report_missing(runtime, supervisor, prefix="No events yet")
            reported_missing = True
        if not reported_still_missing and now - start_time > 300:
            _report_missing(runtime, supervisor, prefix="Missing")
            reported_still_missing = True

        if now - last_frame >= 1 / 30.0:
            last_frame = now
            symbol = input_state.symbol
            now_ms = int(time.time() * 1000)
            status_spot = _adapter_status(
                symbol, now_ms, supervisor.spot, runtime.symbol_maps.spot_base_to_actual
            )
            status_perp = _adapter_status(
                symbol, now_ms, supervisor.perp, runtime.symbol_maps.perp_base_to_actual
            )
            spot_stats = None
            perp_stats = None
            spot_actuals = runtime.symbol_maps.spot_base_to_actual.get(symbol, [])
            perp_actuals = runtime.symbol_maps.perp_base_to_actual.get(symbol, [])
            if supervisor.spot is not None:
                spot_stats = supervisor.spot.stats_for(now_ms, symbols=spot_actuals)
            if supervisor.perp is not None:
                perp_stats = supervisor.perp.stats_for(now_ms, symbols=perp_actuals)
            metrics_snapshot = live_metrics.snapshot(symbol)
            renderer.draw(
                stdscr,
                symbol,
                runtime.last_state.get(symbol),
                status_spot=status_spot,
                status_perp=status_perp,
                spot_stats=spot_stats,
                perp_stats=perp_stats,
                metrics=metrics_snapshot,
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

    def start(
        self,
        *,
        spot_symbols: list[str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
    ) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)
        self.update_symbols(
            spot_symbols=spot_symbols,
            spot_quotes=spot_quotes,
            quote_pairs=quote_pairs,
            quote_rates=quote_rates,
            perp_symbols=perp_symbols,
        )

    def update_symbols(
        self,
        *,
        spot_symbols: list[str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
    ) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._restart(
                spot_symbols=spot_symbols,
                spot_quotes=spot_quotes,
                quote_pairs=quote_pairs,
                quote_rates=quote_rates,
                perp_symbols=perp_symbols,
            ),
            self._loop,
        )

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    async def _restart(
        self,
        *,
        spot_symbols: list[str],
        spot_quotes: dict[str, str],
        quote_pairs: dict[str, QuotePair],
        quote_rates: dict[str, float],
        perp_symbols: list[str],
    ) -> None:
        await self._cancel_tasks()
        spot = BinanceSpotWSAdapter(
            symbols=spot_symbols,
            symbol_quotes=spot_quotes,
            quote_pairs=quote_pairs,
            quote_rates=quote_rates,
        )
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


def _init_runtime(
    symbols: list[str],
    window_ms: int,
    symbol_maps: SymbolMaps,
    defaults: Defaults,
) -> RuntimeState:
    loops: dict[str, EngineLoop] = {}
    last_state: dict[str, StateSnapshot | None] = {}
    pending: dict[str, list[Event]] = {symbol: [] for symbol in symbols}
    last_event_ms: dict[str, int | None] = {symbol: None for symbol in symbols}

    for symbol in symbols:
        buffer = RollingEventBuffer(window_delta_ms=window_ms)
        engine = StateEngine(defaults)
        loops[symbol] = EngineLoop(symbol=symbol, buffer=buffer, engine=engine)
        last_state[symbol] = None

    return RuntimeState(
        loops=loops,
        last_state=last_state,
        pending=pending,
        symbol_maps=symbol_maps,
        last_event_ms=last_event_ms,
    )


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
        runtime.last_event_ms[base_symbol] = item.event.timestamp


def _update_state(
    runtime: RuntimeState,
    now_ms: int,
    tbt_cutoffs: dict[str, int],
    tbt_windows: dict[str, int],
    diagnostics: "DiagnosticLogger | None",
    live_metrics: LiveMetrics | None,
) -> None:
    for symbol, loop in runtime.loops.items():
        events = runtime.pending[symbol]
        runtime.pending[symbol] = []
        window_override = tbt_windows.get(symbol)
        if events:
            state = loop.step(events, now_ms, window_override_ms=window_override)
            runtime.last_state[symbol] = state
            if live_metrics is not None and state is not None:
                live_metrics.update(symbol, state, now_ms)
            if diagnostics is not None:
                diagnostics.log(symbol, runtime.last_state[symbol], now_ms, loop.buffer)
            continue
        last_event_ms = runtime.last_event_ms.get(symbol)
        if last_event_ms is None:
            continue
        cutoff_ms = tbt_cutoffs.get(symbol)
        if cutoff_ms is None:
            continue
        if now_ms - last_event_ms <= cutoff_ms:
            state = loop.step(events, now_ms, window_override_ms=window_override)
            runtime.last_state[symbol] = state
            if live_metrics is not None and state is not None:
                live_metrics.update(symbol, state, now_ms)
            if diagnostics is not None:
                diagnostics.log(symbol, runtime.last_state[symbol], now_ms, loop.buffer)


def _adapter_status(
    symbol: str,
    now_ms: int,
    adapter: BinanceSpotWSAdapter | BinancePerpWSAdapter | None,
    mapping: dict[str, list[str]],
) -> AdapterStatus:
    if adapter is None:
        return AdapterStatus.DISCONNECTED
    actuals = mapping.get(symbol)
    if not actuals:
        return AdapterStatus.DISCONNECTED
    if adapter.status(actuals[0], now_ms) == AdapterStatus.DISCONNECTED:
        return AdapterStatus.DISCONNECTED
    statuses = [adapter.status(actual, now_ms) for actual in actuals]
    if any(status == AdapterStatus.CONNECTED for status in statuses):
        return AdapterStatus.CONNECTED
    return AdapterStatus.STALE


def _configure_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "flow_lens.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )


class DiagnosticLogger:
    def __init__(
        self,
        *,
        path: Path,
        symbols: set[str],
        tanh_k: float,
        max_lines: int = 20_000,
    ) -> None:
        self._base_path = path
        self._symbols = {symbol.upper() for symbol in symbols}
        self._tanh_k = tanh_k
        self._max_lines = max_lines
        self._line_count = 0
        self._part = 0
        self._run_id = time.strftime("%Y%m%d-%H%M%S")
        self._base_path.parent.mkdir(exist_ok=True)
        self._file = self._open_new_file()

    def _open_new_file(self) -> TextIO:
        suffix = f"-{self._run_id}-p{self._part:02d}.jsonl"
        filename = self._base_path.with_name(self._base_path.stem + suffix)
        self._part += 1
        self._line_count = 0
        return filename.open("w", encoding="utf-8")

    def log(
        self,
        symbol: str,
        state: StateSnapshot | None,
        now_ms: int,
        buffer: RollingEventBuffer,
    ) -> None:
        if state is None:
            return
        symbol_upper = symbol.upper()
        if symbol_upper not in self._symbols:
            return
        record = {
            "ts_wall_ms": int(time.time() * 1000),
            "now_ms": now_ms,
            "symbol": symbol_upper,
            "window_ms": buffer.window_delta_ms,
            "window_seconds": state.window_seconds,
            "buffer_event_count": buffer.size,
            "tanh_k": self._tanh_k,
            "price_series_used": state.price_series_used,
            "spot_fresh": state.spot_fresh,
            "perp_fresh": state.perp_fresh,
            "last_spot_event_ts": state.last_spot_event_ts,
            "last_perp_event_ts": state.last_perp_event_ts,
            "spot_event_count_window": state.spot_event_count_window,
            "perp_event_count_window": state.perp_event_count_window,
            "price_start": state.price_start,
            "price_end": state.price_end,
            "log_return": state.log_return,
            "delta_price": state.price_end - state.price_start,
            "disp_rate": state.disp_rate,
            "E_rate": state.effort_rate,
            "disp_scale": state.disp_scale,
            "E_scale": state.effort_scale,
            "disp_deadband_active": state.disp_deadband_active,
            "E_spot": state.e_spot,
            "E_perp": state.e_perp,
            "E_dir": state.e_dir,
            "E_dir_sign": _sign(state.e_dir),
            "E_total": state.total_effort,
            "D": state.dominance,
            "E_spot_share": state.e_spot_share,
            "X_raw": state.x_raw,
            "X": state.x,
            "size_raw": state.size_raw,
            "size_bin": state.size_bin,
            "disp": state.disp,
            "effort_floor": state.effort_floor,
            "effort_median": state.effort_median,
            "effort_norm": state.effort_norm,
            "gate": state.gate,
            "eff_raw": state.eff_raw,
            "Y_raw": state.y_raw,
            "Y_gated": state.y_gated,
            "Y": state.y,
            "halo_raw": state.halo_raw,
            "halo": state.halo,
            "halo_bin": state.halo_bin,
            "source_count_active": state.source_count_active,
            "max_source_share": state.max_source_share,
            "top_source_id": state.top_source_id,
            "top_source_effort": state.top_source_effort,
        }
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()
        self._line_count += 1
        if self._line_count >= self._max_lines:
            self._file.close()
            self._file = self._open_new_file()


def _report_missing(
    runtime: RuntimeState, supervisor: AdapterSupervisor, *, prefix: str
) -> None:
    if supervisor.spot is not None:
        missing = [
            runtime.symbol_maps.spot_actual_to_base.get(actual, actual)
            for actual in supervisor.spot.missing_symbols()
        ]
        if missing:
            logging.warning("%s spot symbols: %s", prefix, ",".join(missing))
    if supervisor.perp is not None:
        missing = [
            runtime.symbol_maps.perp_actual_to_base.get(actual, actual)
            for actual in supervisor.perp.missing_symbols()
        ]
        if missing:
            logging.warning("%s perp symbols: %s", prefix, ",".join(missing))


def _map_to_base(item: AdapterEvent, symbol_maps: SymbolMaps) -> str | None:
    if item.event.source_id.startswith("binance_spot"):
        return symbol_maps.spot_actual_to_base.get(item.symbol)
    return symbol_maps.perp_actual_to_base.get(item.symbol)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _build_tbt_settings(
    runtime: RuntimeState,
    supervisor: AdapterSupervisor,
    fallback_ms: int,
    window_multiplier: float,
) -> tuple[dict[str, int], dict[str, int]]:
    cutoffs: dict[str, int] = {}
    windows: dict[str, int] = {}
    for symbol in runtime.loops:
        spot_actuals = runtime.symbol_maps.spot_base_to_actual.get(symbol, [])
        perp_actuals = runtime.symbol_maps.perp_base_to_actual.get(symbol, [])
        tbt_values: list[float] = []
        if supervisor.spot is not None:
            spot_tbt = supervisor.spot.tbt_min(spot_actuals)
            if spot_tbt is not None:
                tbt_values.append(spot_tbt)
        if supervisor.perp is not None:
            perp_tbt = supervisor.perp.tbt_min(perp_actuals)
            if perp_tbt is not None:
                tbt_values.append(perp_tbt)
        if tbt_values:
            min_tbt = min(tbt_values)
            cutoffs[symbol] = max(1, int(min_tbt))
            windows[symbol] = max(fallback_ms, int(min_tbt * window_multiplier))
        else:
            cutoffs[symbol] = fallback_ms
            windows[symbol] = fallback_ms
    return cutoffs, windows


def _flatten(mapping: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for symbols in mapping.values():
        flattened.extend(symbols)
    return flattened


if __name__ == "__main__":
    main()
