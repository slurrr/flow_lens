#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BucketStats:
    count: int = 0
    min_ts: int | None = None
    max_ts: int | None = None
    min_agg_id: int | None = None
    max_agg_id: int | None = None

    def update(self, timestamp: int | None, agg_id: int | None) -> None:
        self.count += 1
        if timestamp is not None:
            if self.min_ts is None or timestamp < self.min_ts:
                self.min_ts = timestamp
            if self.max_ts is None or timestamp > self.max_ts:
                self.max_ts = timestamp
        if agg_id is not None:
            if self.min_agg_id is None or agg_id < self.min_agg_id:
                self.min_agg_id = agg_id
            if self.max_agg_id is None or agg_id > self.max_agg_id:
                self.max_agg_id = agg_id


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect backfill JSONL summary.")
    parser.add_argument(
        "--path",
        required=True,
        help="Path to backfill JSONL.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="Print progress every N lines (0 disables).",
    )
    return parser.parse_args()


def _format_ts(ts: int | None) -> str:
    if ts is None:
        return "n/a"
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()


def main() -> None:
    args = _parse_args()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")

    totals = BucketStats()
    by_key: dict[tuple[str, str], BucketStats] = {}
    line_count = 0

    with _open_input(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            line_count += 1
            record = json.loads(line)
            symbol = str(record.get("symbol", ""))
            market = str(record.get("market", ""))
            key = (symbol, market)
            stats = by_key.setdefault(key, BucketStats())
            ts = record.get("timestamp")
            agg_id = record.get("agg_id")
            ts_int = int(ts) if ts is not None else None
            agg_int = int(agg_id) if agg_id is not None else None
            stats.update(ts_int, agg_int)
            totals.update(ts_int, agg_int)

            if args.progress_every and line_count % args.progress_every == 0:
                print(f"... processed {line_count:,} lines")

    print(f"lines: {line_count:,}")
    print(f"time_range: {_format_ts(totals.min_ts)} -> {_format_ts(totals.max_ts)}")
    print(f"agg_id_range: {totals.min_agg_id} -> {totals.max_agg_id}")
    print("by_symbol_market:")
    for key in sorted(by_key):
        stats = by_key[key]
        print(
            f"  {key[0]} {key[1]}: count={stats.count:,} "
            f"time={_format_ts(stats.min_ts)} -> {_format_ts(stats.max_ts)} "
            f"agg_id={stats.min_agg_id} -> {stats.max_agg_id}"
        )


def _open_input(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


if __name__ == "__main__":
    main()
