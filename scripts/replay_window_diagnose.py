#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

EPSILON = 1e-12


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose replay windows for zero-disp and price-series issues."
    )
    parser.add_argument(
        "--logs-dir",
        default="logs/replay",
        help="Directory containing replay JSONL/JSONL.GZ logs.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols to include (e.g., BTC,SOL).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output file (text).",
    )
    return parser.parse_args()


def _iter_log_paths(logs_dir: Path) -> list[Path]:
    if not logs_dir.exists():
        raise SystemExit(f"Missing logs dir: {logs_dir}")
    paths = [
        path
        for path in logs_dir.rglob("flow_lens_replay-*.jsonl*")
        if not path.name.endswith(".part")
    ]
    if not paths:
        raise SystemExit("No replay logs found.")
    return sorted(paths)


def _open_log(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _parse_label(path: Path) -> tuple[str, str | None]:
    name = path.stem
    if name.endswith(".jsonl"):
        name = Path(name).stem
    if not name.startswith("flow_lens_replay-"):
        return "UNKNOWN", None
    parts = name.split("-")
    if len(parts) >= 3:
        return parts[1].upper(), parts[2]
    return parts[1].upper(), None


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 1:
        return ordered[-1]
    idx = (len(ordered) - 1) * pct
    low = math.floor(idx)
    high = math.ceil(idx)
    if low == high:
        return ordered[low]
    weight = idx - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _write_lines(lines: Iterable[str], out_path: str | None) -> None:
    text = "\n".join(lines) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main() -> None:
    args = _parse_args()
    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    paths = _iter_log_paths(Path(args.logs_dir))

    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    disp_abs: dict[tuple[str, str], list[float]] = defaultdict(list)
    disp_abs_nonzero: dict[tuple[str, str], list[float]] = defaultdict(list)
    event_counts: dict[tuple[str, str], list[int]] = defaultdict(list)
    series_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for path in paths:
        symbol_label, regime_label = _parse_label(path)
        if symbols and symbol_label not in symbols:
            continue
        with _open_log(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict) and "_meta" in record:
                    continue
                if isinstance(record, dict) and "event_type" in record:
                    continue
                if not isinstance(record, dict):
                    continue
                symbol = str(record.get("symbol", symbol_label)).upper()
                if symbols and symbol not in symbols:
                    continue
                regime = regime_label or "unknown"
                key = (symbol, regime)

                counts[key]["records"] += 1
                series = str(record.get("price_series_used", "unknown"))
                series_counts[key][series] += 1

                spot_count = int(record.get("spot_event_count_window", 0) or 0)
                perp_count = int(record.get("perp_event_count_window", 0) or 0)
                window_events = spot_count + perp_count
                event_counts[key].append(window_events)
                if window_events > 0:
                    counts[key]["with_events"] += 1
                delta_price = float(record.get("delta_price", 0.0))
                disp_rate = float(record.get("disp_rate", 0.0))
                if window_events > 0:
                    if abs(delta_price) <= EPSILON:
                        counts[key]["events_zero_price"] += 1
                    if abs(disp_rate) <= EPSILON:
                        counts[key]["events_zero_disp"] += 1
                    disp_abs[key].append(abs(disp_rate))
                    if abs(disp_rate) > EPSILON:
                        disp_abs_nonzero[key].append(abs(disp_rate))

    lines: list[str] = []
    lines.append("== replay window diagnostics ==")
    for key in sorted(counts.keys()):
        symbol, regime = key
        total = counts[key]["records"]
        with_events = counts[key]["with_events"]
        zero_price = counts[key]["events_zero_price"]
        zero_disp = counts[key]["events_zero_disp"]
        lines.append(f"\n{symbol} {regime}")
        lines.append(f"records: {total}  with_events: {with_events}")
        if with_events > 0:
            lines.append(
                "events_zero_price: "
                f"{zero_price} ({zero_price / with_events:.2%})"
            )
            lines.append(
                "events_zero_disp: "
                f"{zero_disp} ({zero_disp / with_events:.2%})"
            )
        event_list = event_counts.get(key, [])
        if event_list:
            lines.append(
                "event_count_p50/p90/p99: "
                f"{_percentile([float(v) for v in event_list], 0.50):.0f}/"
                f"{_percentile([float(v) for v in event_list], 0.90):.0f}/"
                f"{_percentile([float(v) for v in event_list], 0.99):.0f}"
            )
        disp_all = disp_abs.get(key, [])
        disp_nz = disp_abs_nonzero.get(key, [])
        if disp_all:
            lines.append(
                "disp_abs_p50/p90/p95/p99 (events): "
                f"{_percentile(disp_all, 0.50):.6g}/"
                f"{_percentile(disp_all, 0.90):.6g}/"
                f"{_percentile(disp_all, 0.95):.6g}/"
                f"{_percentile(disp_all, 0.99):.6g}"
            )
        if disp_nz:
            lines.append(
                "disp_abs_p50/p90/p95/p99 (nonzero): "
                f"{_percentile(disp_nz, 0.50):.6g}/"
                f"{_percentile(disp_nz, 0.90):.6g}/"
                f"{_percentile(disp_nz, 0.95):.6g}/"
                f"{_percentile(disp_nz, 0.99):.6g}"
            )
        series = series_counts.get(key, Counter())
        if series:
            total_series = sum(series.values())
            fallback = sum(
                count for name, count in series.items() if "fallback" in name or name == "none"
            )
            lines.append(
                "price_series_fallback: "
                f"{fallback} ({fallback / total_series:.2%})  "
                f"counts={dict(series)}"
            )

    lines.append(
        "\nNote: unique price counts per window are not available from replay snapshots. "
        "To measure unique prices per window, compute directly from raw trades."
    )
    _write_lines(lines, args.out if args.out else None)


if __name__ == "__main__":
    main()
