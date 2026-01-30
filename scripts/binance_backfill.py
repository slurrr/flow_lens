#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USD_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")
MARKET_URLS = {
    "spot": "https://api.binance.com/api/v3/aggTrades",
    "perp": "https://fapi.binance.com/fapi/v1/aggTrades",
}


@dataclass(frozen=True)
class BackfillConfig:
    symbols: tuple[str, ...]
    markets: tuple[str, ...]
    start_ms: int
    end_ms: int
    out_path: Path
    out_dir: Path
    limit: int
    sleep_s: float
    require_usd_quote: bool
    chunk_ms: int | None
    gzip: bool
    skip_existing: bool
    max_retries: int
    backoff_base_s: float
    backoff_max_s: float


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


def _split_symbol(symbol: str) -> tuple[str, str | None]:
    symbol_upper = symbol.upper()
    for quote in USD_QUOTES:
        if symbol_upper.endswith(quote):
            return symbol_upper[: -len(quote)], quote
    return symbol_upper, None


def _fetch_json(
    url: str,
    params: dict[str, int | str],
    *,
    max_retries: int,
    backoff_base_s: float,
    backoff_max_s: float,
) -> list[dict]:
    query = urlencode(params)
    request = Request(f"{url}?{query}", headers={"User-Agent": "flow_lens"})
    attempt = 0
    while True:
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            if _retries_exhausted(attempt, max_retries) or not _should_retry_exception(exc):
                raise
            _backoff_sleep(attempt, backoff_base_s, backoff_max_s)
            attempt += 1
            continue
        if isinstance(payload, dict) and payload.get("code") is not None:
            if _is_rate_limit(payload) and not _retries_exhausted(attempt, max_retries):
                _backoff_sleep(attempt, backoff_base_s, backoff_max_s)
                attempt += 1
                continue
            raise RuntimeError(f"Binance error: {payload}")
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Binance response payload.")
        return payload


def _iter_agg_trades(
    *,
    symbol: str,
    market: str,
    start_ms: int,
    end_ms: int,
    limit: int,
    sleep_s: float,
    max_retries: int,
    backoff_base_s: float,
    backoff_max_s: float,
) -> Iterable[dict]:
    url = MARKET_URLS[market]
    next_from_id: int | None = None
    last_from_id: int | None = None
    while True:
        params: dict[str, int | str] = {"symbol": symbol, "limit": limit}
        if next_from_id is None:
            params["startTime"] = start_ms
            params["endTime"] = end_ms
        else:
            params["fromId"] = next_from_id
        batch = _fetch_json(
            url,
            params,
            max_retries=max_retries,
            backoff_base_s=backoff_base_s,
            backoff_max_s=backoff_max_s,
        )
        if not batch:
            break
        for trade in batch:
            trade_ts = int(trade["T"])
            if trade_ts < start_ms:
                continue
            if trade_ts > end_ms:
                return
            yield trade
        last_trade = batch[-1]
        next_from_id = int(last_trade["a"]) + 1
        if next_from_id == last_from_id:
            break
        last_from_id = next_from_id
        if int(last_trade["T"]) >= end_ms:
            break
        if sleep_s > 0:
            time.sleep(sleep_s)


def _write_records(config: BackfillConfig) -> None:
    if config.chunk_ms is None:
        config.out_path.parent.mkdir(parents=True, exist_ok=True)
        total_records = _write_range(
            config,
            start_ms=config.start_ms,
            end_ms=config.end_ms,
            out_path=config.out_path,
        )
        print(f"Wrote {total_records} records -> {config.out_path}")
        return

    config.out_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    total_records = 0
    for start_ms, end_ms in _iter_chunks(config.start_ms, config.end_ms, config.chunk_ms):
        chunk_count += 1
        print(
            "Chunk %s: %s -> %s",
            chunk_count,
            _format_ts(start_ms),
            _format_ts(end_ms),
        )
        for symbol in config.symbols:
            for market in config.markets:
                out_path = _chunk_output_path(
                    config.out_dir,
                    symbol=symbol,
                    market=market,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    gzip_enabled=config.gzip,
                )
                if config.skip_existing and out_path.exists():
                    print(f"  skip existing {out_path}")
                    continue
                count = _write_range(
                    config,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    out_path=out_path,
                    symbols=(symbol,),
                    markets=(market,),
                )
                if count > 0:
                    total_records += count
                    print(f"  wrote {count:,} -> {out_path}")
    print(f"Total records: {total_records:,}")


def _write_range(
    config: BackfillConfig,
    *,
    start_ms: int,
    end_ms: int,
    out_path: Path,
    symbols: tuple[str, ...] | None = None,
    markets: tuple[str, ...] | None = None,
) -> int:
    total_records = 0
    symbols_iter = symbols or config.symbols
    markets_iter = markets or config.markets
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_suffix(out_path.suffix + ".part")
    with _open_output(temp_path, gzip_enabled=config.gzip) as handle:
        for symbol in symbols_iter:
            base, quote = _split_symbol(symbol)
            if config.require_usd_quote and quote is None:
                print(f"Skipping {symbol}: non-USD quote.")
                continue
            for market in markets_iter:
                source_id = f"binance_{market}"
                side_type = "spot" if market == "spot" else "perp"
                for trade in _iter_agg_trades(
                    symbol=symbol,
                    market=market,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    limit=config.limit,
                    sleep_s=config.sleep_s,
                    max_retries=config.max_retries,
                    backoff_base_s=config.backoff_base_s,
                    backoff_max_s=config.backoff_max_s,
                ):
                    price = float(trade["p"])
                    quantity = float(trade["q"])
                    aggressor_side = "sell" if trade.get("m") else "buy"
                    record = {
                        "symbol": symbol,
                        "base_symbol": base,
                        "quote": quote,
                        "market": market,
                        "source_id": source_id,
                        "side_type": side_type,
                        "aggressor_side": aggressor_side,
                        "timestamp": int(trade["T"]),
                        "price": price,
                        "quantity": quantity,
                        "effort_value": price * quantity,
                        "agg_id": int(trade["a"]),
                        "trade_id_first": int(trade.get("f", trade["a"])),
                        "trade_id_last": int(trade.get("l", trade["a"])),
                        "is_buyer_maker": bool(trade.get("m")),
                    }
                    handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                    total_records += 1
    temp_path.replace(out_path)
    return total_records


def _parse_args() -> BackfillConfig:
    parser = argparse.ArgumentParser(
        description="Backfill Binance aggTrades to JSONL for replay.",
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated Binance symbols, e.g. BTCUSDT,ETHUSDT.",
    )
    parser.add_argument(
        "--market",
        default="both",
        choices=("spot", "perp", "both"),
        help="Market to fetch.",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start time (ms since epoch or ISO-8601).",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End time (ms since epoch or ISO-8601).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output JSONL path (defaults to logs/binance_backfill-<ts>.jsonl).",
    )
    parser.add_argument(
        "--out-dir",
        default="logs/backfill",
        help="Output directory when chunking (default: logs/backfill).",
    )
    parser.add_argument(
        "--chunk-hours",
        type=float,
        default=0.0,
        help="Split output by time chunks (hours). 0 disables.",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Write output as .jsonl.gz.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip chunk files that already exist (assumes completed).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Max aggTrades per request (Binance limit is 1000).",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=200,
        help="Sleep between requests (ms).",
    )
    parser.add_argument(
        "--allow-non-usd",
        action="store_true",
        help="Allow non-USD quotes (effort_value will be in quote units).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=-1,
        help="Max retry attempts for rate limits or transient errors (-1 = infinite).",
    )
    parser.add_argument(
        "--backoff-ms",
        type=int,
        default=500,
        help="Base backoff delay in milliseconds.",
    )
    parser.add_argument(
        "--backoff-max-ms",
        type=int,
        default=5000,
        help="Max backoff delay in milliseconds.",
    )
    args = parser.parse_args()

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    if not symbols:
        raise SystemExit("No symbols provided.")
    start_ms = _parse_time(args.start)
    end_ms = _parse_time(args.end)
    if start_ms >= end_ms:
        raise SystemExit("Start time must be before end time.")
    markets = ("spot", "perp") if args.market == "both" else (args.market,)
    out_path = Path(args.out) if args.out else _default_output_path(gzip_enabled=args.gzip)
    out_dir = Path(args.out_dir)
    chunk_ms = int(args.chunk_hours * 3600 * 1000) if args.chunk_hours > 0 else None
    return BackfillConfig(
        symbols=symbols,
        markets=markets,
        start_ms=start_ms,
        end_ms=end_ms,
        out_path=out_path,
        out_dir=out_dir,
        limit=args.limit,
        sleep_s=max(0.0, args.sleep_ms / 1000.0),
        require_usd_quote=not args.allow_non_usd,
        chunk_ms=chunk_ms,
        gzip=args.gzip,
        skip_existing=args.skip_existing,
        max_retries=int(args.max_retries),
        backoff_base_s=max(0.0, args.backoff_ms / 1000.0),
        backoff_max_s=max(0.0, args.backoff_max_ms / 1000.0),
    )


def _default_output_path(*, gzip_enabled: bool) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ".jsonl.gz" if gzip_enabled else ".jsonl"
    return Path("logs") / f"binance_backfill-{timestamp}{suffix}"


def _chunk_output_path(
    out_dir: Path,
    *,
    symbol: str,
    market: str,
    start_ms: int,
    end_ms: int,
    gzip_enabled: bool,
) -> Path:
    suffix = f"{_format_ts_compact(start_ms)}_{_format_ts_compact(end_ms)}"
    ext = ".jsonl.gz" if gzip_enabled else ".jsonl"
    filename = f"binance_backfill-{symbol}-{market}-{suffix}{ext}"
    return out_dir / filename


def _format_ts(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.isoformat()


def _format_ts_compact(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y%m%d-%H%M%S")


def _iter_chunks(start_ms: int, end_ms: int, chunk_ms: int) -> Iterable[tuple[int, int]]:
    cursor = start_ms
    while cursor < end_ms:
        next_end = min(end_ms, cursor + chunk_ms)
        yield cursor, next_end
        cursor = next_end


def _is_rate_limit(payload: dict) -> bool:
    code = payload.get("code")
    message = str(payload.get("msg", "")).lower()
    if code in (-1003, 429):
        return True
    return "too many requests" in message or "rate limit" in message


def _should_retry_exception(exc: Exception) -> bool:
    status = getattr(exc, "code", None)
    if status in (418, 429, 500, 502, 503, 504):
        return True
    return True


def _backoff_sleep(attempt: int, base_s: float, max_s: float) -> None:
    if base_s <= 0:
        time.sleep(0.0)
        return
    delay = min(max_s, base_s * (2**attempt))
    jitter = random.uniform(0.0, min(delay, 0.25))
    time.sleep(delay + jitter)


def _retries_exhausted(attempt: int, max_retries: int) -> bool:
    if max_retries < 0:
        return False
    return attempt >= max_retries


def _open_output(path: Path, *, gzip_enabled: bool) -> TextIO:
    if gzip_enabled or path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8")


def main() -> None:
    config = _parse_args()
    _write_records(config)


if __name__ == "__main__":
    main()
