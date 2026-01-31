#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO

from flow_lens.config import AppConfig, load_app_config

USD_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "USD1")
EPSILON = 1e-12


@dataclass(frozen=True)
class ChunkFile:
    path: Path
    symbol: str
    market: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TradeTick:
    timestamp: int
    price: float
    side_type: str


@dataclass(frozen=True)
class Scenario:
    symbol: str
    label: str
    start_ms: int
    end_ms: int
    score: float
    metrics: dict[str, float]


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


def _iter_chunks(data_dir: Path) -> list[ChunkFile]:
    chunks: list[ChunkFile] = []
    for path in data_dir.rglob("binance_backfill-*.jsonl*"):
        if path.name.endswith(".part"):
            continue
        chunk = _parse_chunk_filename(path)
        if chunk is not None:
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
        raise SystemExit("Scenario start time must be before end time.")
    return start_ms, end_ms


def _open_jsonl(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _iter_trade_ticks(
    chunk: ChunkFile,
    *,
    start_ms: int,
    end_ms: int,
    allow_non_usd: bool,
) -> Iterator[TradeTick]:
    error_count = 0
    with _open_jsonl(chunk.path) as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                error_count += 1
                continue
            if not isinstance(record, dict):
                error_count += 1
                continue
            if not allow_non_usd and record.get("quote") not in USD_QUOTES:
                continue
            ts_value = record.get("timestamp")
            if ts_value is None:
                error_count += 1
                continue
            try:
                ts = int(ts_value)
            except (TypeError, ValueError):
                error_count += 1
                continue
            if ts < start_ms:
                continue
            if ts >= end_ms:
                break
            yield TradeTick(
                timestamp=ts,
                price=float(record.get("price", 0.0)),
                side_type=str(record.get("side_type", chunk.market)),
            )
    if error_count:
        print(f"Warning: skipped {error_count} malformed lines in {chunk.path}")


def _merge_iters(iters: list[Iterator[TradeTick]]) -> Iterator[TradeTick]:
    heap: list[tuple[int, int, TradeTick, Iterator[TradeTick]]] = []
    for idx, iterator in enumerate(iters):
        try:
            trade = next(iterator)
        except StopIteration:
            continue
        heap.append((trade.timestamp, idx, trade, iterator))
    heap.sort()
    while heap:
        _, idx, trade, iterator = heap.pop(0)
        yield trade
        try:
            nxt = next(iterator)
        except StopIteration:
            continue
        bisect.insort(heap, (nxt.timestamp, idx, nxt, iterator))


def _select_price(
    *,
    mode: str,
    now_ms: int,
    fresh_ms: int,
    last_spot_price: float | None,
    last_spot_ts: int | None,
    last_perp_price: float | None,
    last_perp_ts: int | None,
    last_any_price: float | None,
) -> float | None:
    if mode == "last_trade":
        return last_any_price
    if mode == "spot_only":
        return last_spot_price
    if mode == "perp_only":
        return last_perp_price

    spot_fresh = last_spot_ts is not None and last_spot_ts >= now_ms - fresh_ms
    perp_fresh = last_perp_ts is not None and last_perp_ts >= now_ms - fresh_ms
    if spot_fresh:
        return last_spot_price
    if perp_fresh:
        return last_perp_price
    if last_spot_price is not None:
        return last_spot_price
    return last_perp_price


def _median(sorted_values: list[float]) -> float:
    if not sorted_values:
        return 0.0
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[mid]
    return 0.5 * (sorted_values[mid - 1] + sorted_values[mid])


def _sign(value: float) -> int:
    if value > EPSILON:
        return 1
    if value < -EPSILON:
        return -1
    return 0


def _count_flips(values: list[float]) -> int:
    flips = 0
    last = 0
    for value in values:
        s = _sign(value)
        if s == 0:
            continue
        if last != 0 and s != last:
            flips += 1
        last = s
    return flips


def _select_top(
    candidates: list[Scenario],
    *,
    top_n: int,
    min_gap_ms: int,
) -> list[Scenario]:
    candidates = sorted(candidates, key=lambda s: s.score, reverse=True)
    selected: list[Scenario] = []
    for candidate in candidates:
        if len(selected) >= top_n:
            break
        if all(
            abs(candidate.start_ms - existing.start_ms) >= min_gap_ms
            and abs(candidate.end_ms - existing.end_ms) >= min_gap_ms
            for existing in selected
        ):
            selected.append(candidate)
    return selected


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index price-only scenarios from backfill.")
    parser.add_argument("--data-dir", default="logs/backfill", help="Backfill directory.")
    parser.add_argument("--symbols", default="", help="Comma-separated base symbols.")
    parser.add_argument("--start", default="", help="Start time (ms or ISO). Optional.")
    parser.add_argument("--end", default="", help="End time (ms or ISO). Optional.")
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="App config for defaults (bin size/scale window).",
    )
    parser.add_argument(
        "--bin-seconds",
        type=float,
        default=0.0,
        help="Bin size in seconds (default: config update_window_seconds).",
    )
    parser.add_argument(
        "--fresh-seconds",
        type=float,
        default=0.0,
        help="Freshness window for spot/perp selection (default: update_window_seconds * tbt_window_multiplier).",
    )
    parser.add_argument(
        "--baseline-seconds",
        type=float,
        default=0.0,
        help="Baseline window for rolling median (default: config scale_window_seconds).",
    )
    parser.add_argument(
        "--price-series",
        choices=("spot_pref", "last_trade", "spot_only", "perp_only"),
        default="spot_pref",
        help="Price series selection mode.",
    )
    parser.add_argument(
        "--window-s",
        type=int,
        default=120,
        help="Scenario window size in seconds.",
    )
    parser.add_argument(
        "--step-s",
        type=int,
        default=60,
        help="Scenario step size in seconds.",
    )
    parser.add_argument(
        "--trend-mult",
        type=float,
        default=0.8,
        help="Trend threshold multiplier.",
    )
    parser.add_argument(
        "--dir-consistency",
        type=float,
        default=0.7,
        help="Directional consistency threshold.",
    )
    parser.add_argument(
        "--impulse-ratio",
        type=float,
        default=2.0,
        help="Impulse threshold for max |return|/baseline.",
    )
    parser.add_argument(
        "--chop-mult",
        type=float,
        default=0.2,
        help="Chop threshold multiplier.",
    )
    parser.add_argument(
        "--chop-flip-rate",
        type=float,
        default=4.0,
        help="Min flip rate per minute for chop.",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.0,
        help="Min fraction of bins with trades in a window (default 0.0).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Max scenarios per label per symbol.",
    )
    parser.add_argument(
        "--min-gap-s",
        type=int,
        default=300,
        help="Min gap between scenarios (seconds).",
    )
    parser.add_argument(
        "--strip-1000",
        action="store_true",
        help="Map perp 1000-prefixed symbols back to base symbol.",
    )
    parser.add_argument(
        "--allow-non-usd",
        action="store_true",
        help="Include non-USD quotes (not recommended).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output JSON path (defaults to docs/diagnostics/scenarios-<ts>.json).",
    )
    return parser.parse_args()


def _build_series(
    merged: Iterator[TradeTick],
    *,
    start_ms: int,
    end_ms: int,
    bin_ms: int,
    fresh_ms: int,
    price_series: str,
) -> tuple[list[int], list[float], list[bool]]:
    times: list[int] = []
    prices: list[float] = []
    bin_has_trade: list[bool] = []

    last_spot_price: float | None = None
    last_spot_ts: int | None = None
    last_perp_price: float | None = None
    last_perp_ts: int | None = None
    last_any_price: float | None = None

    try:
        next_trade = next(merged)
    except StopIteration:
        return times, prices, bin_has_trade

    now_ms = start_ms + bin_ms
    has_trade_in_bin = False

    while now_ms <= end_ms:
        while next_trade is not None and next_trade.timestamp <= now_ms:
            has_trade_in_bin = True
            last_any_price = next_trade.price
            if next_trade.side_type == "spot":
                last_spot_price = next_trade.price
                last_spot_ts = next_trade.timestamp
            elif next_trade.side_type == "perp":
                last_perp_price = next_trade.price
                last_perp_ts = next_trade.timestamp
            try:
                next_trade = next(merged)
            except StopIteration:
                next_trade = None
                break

        price = _select_price(
            mode=price_series,
            now_ms=now_ms,
            fresh_ms=fresh_ms,
            last_spot_price=last_spot_price,
            last_spot_ts=last_spot_ts,
            last_perp_price=last_perp_price,
            last_perp_ts=last_perp_ts,
            last_any_price=last_any_price,
        )
        if price is not None:
            times.append(now_ms)
            prices.append(price)
            bin_has_trade.append(has_trade_in_bin)

        has_trade_in_bin = False
        now_ms += bin_ms

    return times, prices, bin_has_trade


def _build_scenarios_for_symbol(
    symbol: str,
    *,
    times: list[int],
    prices: list[float],
    bin_has_trade: list[bool],
    bin_s: float,
    baseline_s: float,
    window_s: int,
    step_s: int,
    trend_mult: float,
    dir_consistency: float,
    impulse_ratio: float,
    chop_mult: float,
    chop_flip_rate: float,
    min_coverage: float,
    top_n: int,
    min_gap_s: int,
) -> list[Scenario]:
    if len(prices) < 2:
        return []

    returns: list[float] = [0.0]
    abs_returns: list[float] = [0.0]
    for idx in range(1, len(prices)):
        prev = prices[idx - 1]
        curr = prices[idx]
        if prev <= 0 or curr <= 0:
            lr = 0.0
        else:
            lr = float(math.log(curr / prev))
        returns.append(lr)
        abs_returns.append(abs(lr))

    baseline_bins = max(1, int(round(baseline_s / bin_s)))
    window_vals: list[float] = []
    window_queue: list[float] = []
    disp_scale: list[float] = []
    for value in abs_returns:
        bisect.insort(window_vals, value)
        window_queue.append(value)
        if len(window_queue) > baseline_bins:
            removed = window_queue.pop(0)
            pos = bisect.bisect_left(window_vals, removed)
            if pos < len(window_vals):
                window_vals.pop(pos)
        disp_scale.append(_median(window_vals))

    window_bins = max(1, int(round(window_s / bin_s)))
    step_bins = max(1, int(round(step_s / bin_s)))
    min_gap_ms = min_gap_s * 1000

    trend_up: list[Scenario] = []
    trend_down: list[Scenario] = []
    impulse: list[Scenario] = []
    chop: list[Scenario] = []

    for start_idx in range(0, len(prices) - window_bins + 1, step_bins):
        end_idx = start_idx + window_bins
        window_returns = returns[start_idx + 1 : end_idx]
        window_scales = disp_scale[start_idx + 1 : end_idx]
        if not window_returns:
            continue

        coverage = sum(1 for flag in bin_has_trade[start_idx:end_idx] if flag) / window_bins
        if coverage < min_coverage:
            continue

        sum_disp = sum(window_returns)
        avg_disp_scale = sum(window_scales) / max(1, len(window_scales))
        disp_ratio_max = max(
            abs(ret) / (scale + EPSILON) for ret, scale in zip(window_returns, window_scales)
        )

        signs = [_sign(ret) for ret in window_returns if ret != 0.0]
        target_sign = _sign(sum_disp)
        if target_sign != 0 and signs:
            consistency = sum(1 for s in signs if s == target_sign) / len(signs)
        else:
            consistency = 0.0

        flip_rate = _count_flips(window_returns) / (window_s / 60.0)
        threshold = trend_mult * avg_disp_scale * len(window_returns)
        chop_threshold = chop_mult * avg_disp_scale * len(window_returns)

        metrics = {
            "sum_disp": sum_disp,
            "avg_disp_scale": avg_disp_scale,
            "disp_ratio_max": disp_ratio_max,
            "dir_consistency": consistency,
            "flip_rate": flip_rate,
            "coverage": coverage,
        }

        if abs(sum_disp) >= threshold and consistency >= dir_consistency:
            label = "trend_up" if sum_disp > 0 else "trend_down"
            candidate = Scenario(symbol, label, times[start_idx], times[end_idx - 1], abs(sum_disp), metrics)
            if sum_disp > 0:
                trend_up.append(candidate)
            else:
                trend_down.append(candidate)

        if disp_ratio_max >= impulse_ratio:
            impulse.append(
                Scenario(symbol, "impulse", times[start_idx], times[end_idx - 1], disp_ratio_max, metrics)
            )

        if abs(sum_disp) <= chop_threshold and flip_rate >= chop_flip_rate:
            chop.append(
                Scenario(symbol, "chop", times[start_idx], times[end_idx - 1], flip_rate, metrics)
            )

    scenarios: list[Scenario] = []
    scenarios.extend(_select_top(trend_up, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(trend_down, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(impulse, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(chop, top_n=top_n, min_gap_ms=min_gap_ms))
    return scenarios


def _load_defaults(config: AppConfig, *, bin_override: float, fresh_override: float, baseline_override: float) -> tuple[float, float, float]:
    bin_seconds = bin_override if bin_override > 0 else config.update_window_seconds
    fresh_seconds = (
        fresh_override
        if fresh_override > 0
        else config.update_window_seconds * config.tbt_window_multiplier
    )
    baseline_seconds = baseline_override if baseline_override > 0 else config.scale_window_seconds
    return bin_seconds, fresh_seconds, baseline_seconds


def main() -> None:
    args = _parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f"Missing data dir: {data_dir}")

    config = load_app_config(args.config)
    bin_seconds, fresh_seconds, baseline_seconds = _load_defaults(
        config,
        bin_override=args.bin_seconds,
        fresh_override=args.fresh_seconds,
        baseline_override=args.baseline_seconds,
    )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    chunks = _iter_chunks(data_dir)
    if not chunks:
        raise SystemExit("No backfill files found.")

    start_override = _parse_time(args.start) if args.start else None
    end_override = _parse_time(args.end) if args.end else None

    if not symbols:
        symbols = sorted(
            {
                _normalize_base_symbol(_split_symbol(chunk.symbol), chunk.market, args.strip_1000)
                for chunk in chunks
            }
        )

    all_scenarios: list[Scenario] = []
    bin_ms = int(max(0.1, bin_seconds) * 1000)
    fresh_ms = int(max(bin_seconds, fresh_seconds) * 1000)

    for base_symbol in symbols:
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
            _iter_trade_ticks(
                chunk,
                start_ms=start_ms,
                end_ms=end_ms,
                allow_non_usd=args.allow_non_usd,
            )
            for chunk in selected
        ]
        merged = _merge_iters(iters)
        times, prices, bin_has_trade = _build_series(
            merged,
            start_ms=start_ms,
            end_ms=end_ms,
            bin_ms=bin_ms,
            fresh_ms=fresh_ms,
            price_series=args.price_series,
        )
        scenarios = _build_scenarios_for_symbol(
            base_symbol,
            times=times,
            prices=prices,
            bin_has_trade=bin_has_trade,
            bin_s=bin_seconds,
            baseline_s=baseline_seconds,
            window_s=args.window_s,
            step_s=args.step_s,
            trend_mult=args.trend_mult,
            dir_consistency=args.dir_consistency,
            impulse_ratio=args.impulse_ratio,
            chop_mult=args.chop_mult,
            chop_flip_rate=args.chop_flip_rate,
            min_coverage=max(0.0, min(args.min_coverage, 1.0)),
            top_n=args.top_n,
            min_gap_s=args.min_gap_s,
        )
        all_scenarios.extend(scenarios)

    output_path = Path(args.out) if args.out else Path("docs/diagnostics") / (
        f"scenarios-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bin_seconds": bin_seconds,
        "fresh_seconds": fresh_seconds,
        "baseline_seconds": baseline_seconds,
        "price_series": args.price_series,
        "window_s": args.window_s,
        "step_s": args.step_s,
        "trend_mult": args.trend_mult,
        "dir_consistency": args.dir_consistency,
        "impulse_ratio": args.impulse_ratio,
        "chop_mult": args.chop_mult,
        "chop_flip_rate": args.chop_flip_rate,
        "min_coverage": args.min_coverage,
        "top_n": args.top_n,
        "min_gap_s": args.min_gap_s,
        "scenarios": [
            {
                "symbol": s.symbol,
                "label": s.label,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "score": s.score,
                "metrics": s.metrics,
            }
            for s in all_scenarios
        ],
    }

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(f"Wrote scenarios: {output_path} (count={len(all_scenarios)})")


if __name__ == "__main__":
    main()
