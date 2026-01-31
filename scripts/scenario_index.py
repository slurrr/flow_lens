#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

EPSILON = 1e-9


@dataclass(frozen=True)
class Scenario:
    symbol: str
    label: str
    start_ms: int
    end_ms: int
    score: float
    metrics: dict[str, float]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index scenarios from diagnostics JSONL.")
    parser.add_argument(
        "--path",
        required=True,
        help="Diagnostics JSONL path or directory.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols to include.",
    )
    parser.add_argument(
        "--window-s",
        type=int,
        default=120,
        help="Window size in seconds (default 120).",
    )
    parser.add_argument(
        "--step-s",
        type=int,
        default=60,
        help="Step size in seconds (default 60).",
    )
    parser.add_argument(
        "--trend-mult",
        type=float,
        default=0.8,
        help="Trend threshold multiplier relative to disp_scale (default 0.8).",
    )
    parser.add_argument(
        "--dir-consistency",
        type=float,
        default=0.7,
        help="Directional consistency threshold (default 0.7).",
    )
    parser.add_argument(
        "--impulse-ratio",
        type=float,
        default=2.0,
        help="Impulse threshold for max |disp_rate|/disp_scale (default 2.0).",
    )
    parser.add_argument(
        "--chop-mult",
        type=float,
        default=0.2,
        help="Chop threshold multiplier relative to disp_scale (default 0.2).",
    )
    parser.add_argument(
        "--chop-flip-rate",
        type=float,
        default=4.0,
        help="Min Y_raw flip rate per minute for chop (default 4.0).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Max scenarios per label per symbol (default 5).",
    )
    parser.add_argument(
        "--min-gap-s",
        type=int,
        default=300,
        help="Min gap between selected scenarios (seconds, default 300).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output JSON path (defaults to docs/diagnostics/scenarios-<ts>.json).",
    )
    return parser.parse_args()


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = list(path.rglob("*.jsonl"))
    files.extend(path.rglob("*.jsonl.gz"))
    return sorted(files)


def _open_jsonl(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _load_records(path: Path) -> dict[str, list[dict]]:
    by_symbol: dict[str, list[dict]] = {}
    with _open_jsonl(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            symbol = str(record.get("symbol", "")).upper()
            if not symbol:
                continue
            by_symbol.setdefault(symbol, []).append(record)
    for records in by_symbol.values():
        records.sort(key=lambda r: int(r.get("now_ms", 0)))
    return by_symbol


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


def _build_scenarios_for_symbol(
    symbol: str,
    records: list[dict],
    *,
    window_s: int,
    step_s: int,
    trend_mult: float,
    dir_consistency: float,
    impulse_ratio: float,
    chop_mult: float,
    chop_flip_rate: float,
    top_n: int,
    min_gap_s: int,
) -> list[Scenario]:
    if not records:
        return []
    window_ms = window_s * 1000
    step_ms = step_s * 1000
    min_gap_ms = min_gap_s * 1000

    times = [int(r["now_ms"]) for r in records]
    disp_rates = [float(r.get("disp_rate", 0.0)) for r in records]
    disp_scales = [float(r.get("disp_scale", 0.0)) for r in records]
    e_dir_signs = [int(r.get("E_dir_sign", 0)) for r in records]
    y_raws = [float(r.get("Y_raw", 0.0)) for r in records]

    start_time = times[0]
    end_time = times[-1]

    idx_start = 0
    idx_end = 0

    trend_up: list[Scenario] = []
    trend_down: list[Scenario] = []
    impulse: list[Scenario] = []
    chop: list[Scenario] = []

    t = start_time
    while t + window_ms <= end_time:
        while idx_start < len(times) and times[idx_start] < t:
            idx_start += 1
        while idx_end < len(times) and times[idx_end] < t + window_ms:
            idx_end += 1

        if idx_end - idx_start < 2:
            t += step_ms
            continue

        window_disp_rates = disp_rates[idx_start:idx_end]
        window_disp_scales = disp_scales[idx_start:idx_end]
        window_e_dir = e_dir_signs[idx_start:idx_end]
        window_y_raw = y_raws[idx_start:idx_end]

        avg_disp_scale = sum(window_disp_scales) / max(1, len(window_disp_scales))
        disp_ratio_max = max(
            abs(dr) / (ds + EPSILON) for dr, ds in zip(window_disp_rates, window_disp_scales)
        )

        dt_s = window_s / max(1, len(window_disp_rates))
        sum_disp = sum(window_disp_rates) * dt_s
        trend_threshold = trend_mult * avg_disp_scale * window_s
        chop_threshold = chop_mult * avg_disp_scale * window_s

        target_sign = _sign(sum_disp)
        if target_sign != 0:
            nonzero_dir = [s for s in window_e_dir if s != 0]
            if nonzero_dir:
                consistent = sum(1 for s in nonzero_dir if s == target_sign) / len(nonzero_dir)
            else:
                consistent = 0.0
        else:
            consistent = 0.0

        flip_rate = _count_flips(window_y_raw) / (window_s / 60.0)

        metrics = {
            "sum_disp": sum_disp,
            "avg_disp_scale": avg_disp_scale,
            "disp_ratio_max": disp_ratio_max,
            "dir_consistency": consistent,
            "flip_rate_y_raw": flip_rate,
        }

        if abs(sum_disp) >= trend_threshold and consistent >= dir_consistency:
            label = "trend_up" if sum_disp > 0 else "trend_down"
            candidate = Scenario(symbol, label, t, t + window_ms, abs(sum_disp), metrics)
            if sum_disp > 0:
                trend_up.append(candidate)
            else:
                trend_down.append(candidate)

        if disp_ratio_max >= impulse_ratio:
            impulse.append(
                Scenario(symbol, "impulse", t, t + window_ms, disp_ratio_max, metrics)
            )

        if abs(sum_disp) <= chop_threshold and flip_rate >= chop_flip_rate:
            chop.append(
                Scenario(symbol, "chop", t, t + window_ms, flip_rate, metrics)
            )

        t += step_ms

    scenarios: list[Scenario] = []
    scenarios.extend(_select_top(trend_up, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(trend_down, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(impulse, top_n=top_n, min_gap_ms=min_gap_ms))
    scenarios.extend(_select_top(chop, top_n=top_n, min_gap_ms=min_gap_ms))
    return scenarios


def main() -> None:
    args = _parse_args()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"Missing path: {path}")

    target_symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    files = _iter_files(path)
    all_scenarios: list[Scenario] = []

    for file_path in files:
        records_by_symbol = _load_records(file_path)
        for symbol, records in records_by_symbol.items():
            if target_symbols and symbol not in target_symbols:
                continue
            scenarios = _build_scenarios_for_symbol(
                symbol,
                records,
                window_s=args.window_s,
                step_s=args.step_s,
                trend_mult=args.trend_mult,
                dir_consistency=args.dir_consistency,
                impulse_ratio=args.impulse_ratio,
                chop_mult=args.chop_mult,
                chop_flip_rate=args.chop_flip_rate,
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
        "window_s": args.window_s,
        "step_s": args.step_s,
        "trend_mult": args.trend_mult,
        "dir_consistency": args.dir_consistency,
        "impulse_ratio": args.impulse_ratio,
        "chop_mult": args.chop_mult,
        "chop_flip_rate": args.chop_flip_rate,
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
