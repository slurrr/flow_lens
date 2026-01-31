#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO, cast

from flow_lens.config import AppConfig, load_app_config
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.constants import (
    Defaults,
    EffectivenessDeadband,
    EffectivenessScaling,
    InputNormalization,
)
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import AggressorSide, Event, SideType

USD_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "USD1")
EPSILON = 1e-9


@dataclass(frozen=True)
class ChunkFile:
    path: Path
    symbol: str
    market: str
    start_ms: int
    end_ms: int


def _parse_time(value: str) -> int:
    value = value.strip()
    if not value:
        raise ValueError("Empty time value.")
    if value.isdigit():
        raw = int(value)
        if raw < 10_000_000_000:
            return raw * 1000
        return raw
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


_CHUNK_RE = re.compile(
    r"^binance_backfill-(?P<symbol>[^-]+)-(?P<market>spot|perp)-"
    r"(?P<start>\d{8}-\d{6})_(?P<end>\d{8}-\d{6})\.jsonl(?:\.gz)?$"
)


def _parse_chunk_filename(path: Path) -> ChunkFile | None:
    match = _CHUNK_RE.match(path.name)
    if not match:
        return None
    symbol = match.group("symbol")
    market = match.group("market")
    start_str = match.group("start")
    end_str = match.group("end")
    try:
        start_ms = int(
            datetime.strptime(start_str, "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
            * 1000
        )
        end_ms = int(
            datetime.strptime(end_str, "%Y%m%d-%H%M%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
            * 1000
        )
    except ValueError:
        return None
    return ChunkFile(path=path, symbol=symbol, market=market, start_ms=start_ms, end_ms=end_ms)


def _split_symbol(symbol: str) -> str:
    symbol_upper = symbol.upper()
    for quote in USD_QUOTES:
        if symbol_upper.endswith(quote):
            return symbol_upper[: -len(quote)]
    return symbol_upper


def _normalize_base_symbol(base: str, market: str, strip_1000: bool) -> str:
    if strip_1000 and market == "perp" and base.startswith("1000"):
        return base[4:]
    return base


def _coerce_side_type(value: str, fallback: str) -> SideType:
    candidate = value.lower()
    if candidate in ("spot", "perp"):
        return cast(SideType, candidate)
    fallback_value = fallback.lower()
    if fallback_value in ("spot", "perp"):
        return cast(SideType, fallback_value)
    return "spot"


def _coerce_aggressor_side(value: str) -> AggressorSide:
    candidate = value.lower()
    if candidate in ("buy", "sell"):
        return cast(AggressorSide, candidate)
    return "buy"


def _iter_chunk_events(
    chunk: ChunkFile,
    *,
    start_ms: int,
    end_ms: int,
) -> Iterator[Event]:
    opener = gzip.open if chunk.path.suffix == ".gz" else open
    with opener(chunk.path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            ts = int(record["timestamp"])
            if ts < start_ms:
                continue
            if ts >= end_ms:
                break
            yield Event(
                timestamp=ts,
                source_id=str(record.get("source_id", "")),
                side_type=_coerce_side_type(
                    str(record.get("side_type", chunk.market)),
                    chunk.market,
                ),
                aggressor_side=_coerce_aggressor_side(
                    str(record.get("aggressor_side", "buy"))
                ),
                effort_value=float(record.get("effort_value", 0.0)),
                price=float(record.get("price", 0.0)),
            )


def _merge_event_iters(iters: list[Iterator[Event]]) -> Iterator[Event]:
    heap: list[tuple[int, int, Event, Iterator[Event]]] = []
    for idx, iterator in enumerate(iters):
        try:
            event = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (event.timestamp, idx, event, iterator))
    while heap:
        _, idx, event, iterator = heapq.heappop(heap)
        yield event
        try:
            nxt = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(heap, (nxt.timestamp, idx, nxt, iterator))


def _build_defaults(config: AppConfig) -> Defaults:
    base = Defaults()
    return Defaults(
        time_domain=base.time_domain,
        effort_floor=base.effort_floor,
        dispersion_metric=base.dispersion_metric,
        smoothing=base.smoothing,
        effectiveness_scaling=EffectivenessScaling(tanh_k=config.tanh_k),
        input_normalization=InputNormalization(scale_window_seconds=config.scale_window_seconds),
        effectiveness_deadband=EffectivenessDeadband(
            disp_scale_multiplier=config.disp_scale_multiplier
        ),
        halo_dynamics=base.halo_dynamics,
        binning=base.binning,
    )


def _open_output(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8")


def _log_record(
    handle: TextIO,
    *,
    symbol: str,
    state: StateSnapshot,
    now_ms: int,
    buffer: RollingEventBuffer,
    tanh_k: float,
) -> None:
    record = {
        "ts_wall_ms": now_ms,
        "now_ms": now_ms,
        "symbol": symbol,
        "window_ms": buffer.window_delta_ms,
        "window_seconds": state.window_seconds,
        "buffer_event_count": buffer.size,
        "tanh_k": tanh_k,
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
        "E_dir_sign": 0 if abs(state.e_dir) <= EPSILON else (1 if state.e_dir > 0 else -1),
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
    handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def _iter_chunks(data_dir: Path) -> list[ChunkFile]:
    chunks: list[ChunkFile] = []
    for path in data_dir.rglob("binance_backfill-*.jsonl*"):
        if path.name.endswith(".part"):
            continue
        chunk = _parse_chunk_filename(path)
        if chunk is None:
            continue
        chunks.append(chunk)
    return chunks


def _select_chunks(
    chunks: list[ChunkFile],
    *,
    base_symbol: str,
    strip_1000: bool,
    start_ms: int,
    end_ms: int,
) -> list[ChunkFile]:
    selected: list[ChunkFile] = []
    for chunk in chunks:
        base = _split_symbol(chunk.symbol)
        base = _normalize_base_symbol(base, chunk.market, strip_1000)
        if base != base_symbol:
            continue
        if chunk.end_ms <= start_ms or chunk.start_ms >= end_ms:
            continue
        selected.append(chunk)
    selected.sort(key=lambda c: (c.start_ms, c.market, c.symbol))
    return selected


def _resolve_time_bounds(
    chunks: list[ChunkFile],
    *,
    start_override: int | None,
    end_override: int | None,
) -> tuple[int, int]:
    if not chunks:
        raise SystemExit("No backfill chunks matched the requested symbol.")
    start_ms = min(chunk.start_ms for chunk in chunks)
    end_ms = max(chunk.end_ms for chunk in chunks)
    if start_override is not None:
        start_ms = start_override
    if end_override is not None:
        end_ms = end_override
    if start_ms >= end_ms:
        raise SystemExit("Replay start time must be before end time.")
    return start_ms, end_ms


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay backfill JSONL into diagnostics logs.")
    parser.add_argument(
        "--data-dir",
        default="logs/backfill",
        help="Directory containing binance_backfill JSONL files.",
    )
    parser.add_argument(
        "--scenario-file",
        default="",
        help="Scenario JSON file (from scenario_split). Overrides symbols/start/end.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated base symbols to replay (e.g., BTC,SOL,SHIB).",
    )
    parser.add_argument(
        "--start",
        default="",
        help="Start time (ms since epoch or ISO-8601). Optional.",
    )
    parser.add_argument(
        "--end",
        default="",
        help="End time (ms since epoch or ISO-8601). Optional.",
    )
    parser.add_argument(
        "--window-ms",
        type=int,
        default=2000,
        help="Rolling window size in ms (default 2000).",
    )
    parser.add_argument(
        "--update-ms",
        type=int,
        default=2000,
        help="Update interval in ms (default 2000).",
    )
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="Path to app config for scaling parameters.",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/replay",
        help="Output directory for replay diagnostics JSONL.",
    )
    parser.add_argument(
        "--strip-1000",
        action="store_true",
        help="Map perp 1000-prefixed symbols back to base symbol.",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Write output as .jsonl.gz.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f"Missing data dir: {data_dir}")

    scenario_file: Path | None = None
    scenario_payload: dict[str, object] | None = None
    if args.scenario_file:
        scenario_file = Path(args.scenario_file)
        if not scenario_file.exists():
            raise SystemExit(f"Missing scenario file: {scenario_file}")
        with scenario_file.open("r", encoding="utf-8") as handle:
            scenario_payload = json.load(handle)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    chunks = _iter_chunks(data_dir)
    if not chunks:
        raise SystemExit("No backfill files found.")

    config = load_app_config(args.config)
    defaults = _build_defaults(config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start_override = _parse_time(args.start) if args.start else None
    end_override = _parse_time(args.end) if args.end else None
    update_ms = max(1, args.update_ms)

    if scenario_payload:
        symbol_value = str(scenario_payload.get("symbol", "")).upper()
        if not symbol_value:
            raise SystemExit("Scenario file missing symbol.")
        base_symbols = [symbol_value]
        replay_start_value = scenario_payload.get("replay_start_ms")
        replay_end_value = scenario_payload.get("replay_end_ms")
        if replay_start_value is None or replay_end_value is None:
            raise SystemExit("Scenario file missing replay_start_ms or replay_end_ms.")
        if not isinstance(replay_start_value, (int, float, str)) or not isinstance(
            replay_end_value, (int, float, str)
        ):
            raise SystemExit("Scenario file replay_start_ms/replay_end_ms must be ints.") from None
        try:
            start_override = int(replay_start_value)
            end_override = int(replay_end_value)
        except (TypeError, ValueError):
            raise SystemExit("Scenario file replay_start_ms/replay_end_ms must be ints.") from None
    else:
        if not symbols:
            base_symbols = sorted(
                {
                    _normalize_base_symbol(_split_symbol(chunk.symbol), chunk.market, args.strip_1000)
                    for chunk in chunks
                }
            )
        else:
            base_symbols = symbols

    for base_symbol in base_symbols:
        selected = _select_chunks(
            chunks,
            base_symbol=base_symbol,
            strip_1000=args.strip_1000,
            start_ms=start_override or 0,
            end_ms=end_override or (2**63 - 1),
        )
        if not selected:
            continue

        start_ms, end_ms = _resolve_time_bounds(
            selected, start_override=start_override, end_override=end_override
        )

        iters = [
            _iter_chunk_events(chunk, start_ms=start_ms, end_ms=end_ms)
            for chunk in selected
        ]
        merged = _merge_event_iters(iters)

        buffer = RollingEventBuffer(window_delta_ms=args.window_ms)
        engine = StateEngine(defaults)
        loop = EngineLoop(symbol=base_symbol, buffer=buffer, engine=engine)

        timestamp_tag = time.strftime("%Y%m%d-%H%M%S")
        label_suffix = ""
        if scenario_payload:
            label = str(scenario_payload.get("label", "")).strip()
            scenario_id = str(scenario_payload.get("id", "")).strip()
            if label or scenario_id:
                label_suffix = f"-{label or 'scenario'}{('-' + scenario_id) if scenario_id else ''}"
        out_name = f"flow_lens_replay-{base_symbol}{label_suffix}-{timestamp_tag}.jsonl"
        if args.gzip:
            out_name += ".gz"
        out_path = out_dir / out_name

        last_event_ms: int | None = None
        now_ms = start_ms
        try:
            next_event = next(merged)
        except StopIteration:
            continue

        with _open_output(out_path) as handle:
            while now_ms < end_ms:
                events: list[Event] = []
                while next_event is not None and next_event.timestamp <= now_ms:
                    events.append(next_event)
                    last_event_ms = next_event.timestamp
                    try:
                        next_event = next(merged)
                    except StopIteration:
                        next_event = None
                        break
                if last_event_ms is not None:
                    state = loop.step(events, now_ms, window_override_ms=args.window_ms)
                    if state is not None:
                        _log_record(
                            handle,
                            symbol=base_symbol,
                            state=state,
                            now_ms=now_ms,
                            buffer=buffer,
                            tanh_k=defaults.effectiveness_scaling.tanh_k,
                        )
                now_ms += update_ms

        print(f"Wrote replay diagnostics: {out_path}")


if __name__ == "__main__":
    main()
