#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyperliquid.info import Info


@dataclass(frozen=True)
class LagStats:
    n: int
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: int
    max_ms: int
    frac_gt_1s: float
    frac_gt_5s: float
    frac_gt_10s: float
    frac_gt_30s: float


def _pct(sorted_values: list[int], p: float) -> int:
    if not sorted_values:
        return 0
    if p <= 0:
        return sorted_values[0]
    if p >= 1:
        return sorted_values[-1]
    return sorted_values[round(p * (len(sorted_values) - 1))]


def _lag_stats(lags_ms: list[int]) -> LagStats:
    lags_ms = sorted(lags_ms)
    if not lags_ms:
        return LagStats(
            n=0,
            median_ms=0.0,
            p95_ms=0.0,
            p99_ms=0.0,
            min_ms=0,
            max_ms=0,
            frac_gt_1s=0.0,
            frac_gt_5s=0.0,
            frac_gt_10s=0.0,
            frac_gt_30s=0.0,
        )
    n = len(lags_ms)
    median_ms = float(statistics.median(lags_ms))
    p95_ms = float(_pct(lags_ms, 0.95))
    p99_ms = float(_pct(lags_ms, 0.99))
    min_ms = int(lags_ms[0])
    max_ms = int(lags_ms[-1])
    frac_gt_1s = sum(1 for x in lags_ms if x > 1000) / n
    frac_gt_5s = sum(1 for x in lags_ms if x > 5000) / n
    frac_gt_10s = sum(1 for x in lags_ms if x > 10_000) / n
    frac_gt_30s = sum(1 for x in lags_ms if x > 30_000) / n
    return LagStats(
        n=n,
        median_ms=median_ms,
        p95_ms=p95_ms,
        p99_ms=p99_ms,
        min_ms=min_ms,
        max_ms=max_ms,
        frac_gt_1s=frac_gt_1s,
        frac_gt_5s=frac_gt_5s,
        frac_gt_10s=frac_gt_10s,
        frac_gt_30s=frac_gt_30s,
    )


def _dup_frac(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    ctr = Counter((int(r.get("time_ms") or 0), str(r.get("px")), str(r.get("sz"))) for r in rows)
    dup = sum(c - 1 for c in ctr.values() if c > 1)
    return dup / len(rows)


def _write_gz_jsonl(path: Path, rows: list[dict[str, Any]], *, meta: dict[str, Any]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, separators=(",", ":")) + "\n")
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_raw_capture(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    out: list[dict[str, Any]] = []
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[call-overload]
        for line in f:
            obj = json.loads(line)
            if "_meta" in obj:
                continue
            out.append(obj)
    return out


def _latest_trades_jsonl_gz(root: Path) -> Path:
    candidates = sorted(root.glob("*_trades_capture/trades.jsonl.gz"), reverse=True)
    if candidates:
        return candidates[0]
    candidates = sorted(root.glob("*_trades_capture/trades.jsonl"), reverse=True)
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"No trades.jsonl(.gz) found under {root}")


def _run_raw_capture(*, python: str, duration_s: int, symbol: str, out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    argv = [
        python,
        "scripts/venue_trades_capture.py",
        "--symbols",
        symbol,
        "--candidates",
        "hyperliquid_perp",
        "--duration-s",
        str(duration_s),
        "--out-root",
        str(out_root),
        "--gzip",
        "--hyperliquid-ts-mode",
        "venue",
        "--debug-hyperliquid",
    ]
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Raw capture failed:\n"
            + f"argv={argv}\n"
            + f"exit={proc.returncode}\n"
            + f"stdout:\n{proc.stdout}\n"
            + f"stderr:\n{proc.stderr}\n"
        )
    return _latest_trades_jsonl_gz(out_root)


def _sdk_capture(*, duration_s: int, symbol: str) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    rows: list[dict[str, Any]] = []
    batch_sizes: list[int] = []
    recv_gaps_ms: list[int] = []
    last_recv_ms: int | None = None

    def on_msg(ws_msg: Any) -> None:
        nonlocal last_recv_ms
        recv_ms = int(time.time() * 1000)
        if last_recv_ms is not None:
            recv_gaps_ms.append(recv_ms - last_recv_ms)
        last_recv_ms = recv_ms

        if not isinstance(ws_msg, dict) or ws_msg.get("channel") != "trades":
            return
        data = ws_msg.get("data")
        if not isinstance(data, list):
            return
        batch_sizes.append(len(data))
        for t in data:
            if not isinstance(t, dict):
                continue
            if t.get("coin") != symbol:
                continue
            try:
                t_ms = int(t.get("time") or 0)
            except (TypeError, ValueError):
                continue
            if t_ms <= 0:
                continue
            rows.append(
                {
                    "time_ms": t_ms,
                    "px": t.get("px"),
                    "sz": t.get("sz"),
                    "side": t.get("side"),
                    "recv_ms": recv_ms,
                }
            )

    info = Info()
    sub_id = info.subscribe({"type": "trades", "coin": symbol}, on_msg)
    time.sleep(max(1, int(duration_s)))
    info.unsubscribe({"type": "trades", "coin": symbol}, sub_id)
    info.disconnect_websocket()
    return rows, batch_sizes, recv_gaps_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Hyperliquid SOL trades via SDK vs raw websocket capture.")
    parser.add_argument("--symbol", default="SOL", help="Base symbol to subscribe (default: SOL).")
    parser.add_argument("--duration-s", type=int, default=60, help="Capture duration seconds for each method (default: 60).")
    parser.add_argument("--out-root", default="/tmp/hl_sdk_vs_raw", help="Output directory root (default: /tmp/hl_sdk_vs_raw).")
    args = parser.parse_args()

    symbol = str(args.symbol).upper().strip()
    duration_s = max(5, int(args.duration_s))
    out_root = Path(str(args.out_root)) / time.strftime("%Y%m%d-%H%M%S")
    out_root.mkdir(parents=True, exist_ok=True)

    python = str(Path(".venv/bin/python"))

    print(f"capture symbol={symbol} duration_s={duration_s}")

    # SDK capture
    sdk_rows, sdk_batch_sizes, sdk_recv_gaps_ms = _sdk_capture(duration_s=duration_s, symbol=symbol)
    sdk_path = out_root / "sdk_trades.jsonl.gz"
    _write_gz_jsonl(
        sdk_path,
        sdk_rows,
        meta={
            "type": "hl_sdk_trades",
            "symbol": symbol,
            "duration_s": duration_s,
            "batch_sizes": {"n": len(sdk_batch_sizes), "max": max(sdk_batch_sizes) if sdk_batch_sizes else 0},
        },
    )

    # Raw capture (our existing capture script)
    raw_root = out_root / "raw"
    raw_path = _run_raw_capture(python=python, duration_s=duration_s, symbol=symbol, out_root=raw_root)
    raw_rows = _read_raw_capture(raw_path)

    # Summaries
    sdk_lags = [int(r["recv_ms"]) - int(r["time_ms"]) for r in sdk_rows if isinstance(r.get("recv_ms"), int)]
    raw_lags = [int(r["ts_recv_ms"]) - int(r["ts_exchange_ms"]) for r in raw_rows]

    sdk_stats = _lag_stats(sdk_lags)
    raw_stats = _lag_stats(raw_lags)

    # Duplicates by (time,px,sz) for apples-to-apples
    sdk_dup = _dup_frac(sdk_rows)
    raw_dup = 0.0
    if raw_rows:
        ctr = Counter((int(r.get("ts_exchange_ms") or 0), str(r.get("price")), str(r.get("size"))) for r in raw_rows)
        dup = sum(c - 1 for c in ctr.values() if c > 1)
        raw_dup = dup / len(raw_rows)

    print("")
    print("== SDK ==")
    print(f"out: {sdk_path}")
    print(f"trades: {sdk_stats.n} dup_frac(time,px,sz): {sdk_dup:.3f}")
    print(
        "lag_ms:"
        f" med={sdk_stats.median_ms:.0f} p95={sdk_stats.p95_ms:.0f} p99={sdk_stats.p99_ms:.0f}"
        f" min={sdk_stats.min_ms} max={sdk_stats.max_ms}"
    )
    print(
        "frac_lag_gt:"
        f" 1s={sdk_stats.frac_gt_1s:.3f} 5s={sdk_stats.frac_gt_5s:.3f}"
        f" 10s={sdk_stats.frac_gt_10s:.3f} 30s={sdk_stats.frac_gt_30s:.3f}"
    )
    if sdk_batch_sizes:
        print(f"batches: n={len(sdk_batch_sizes)} max={max(sdk_batch_sizes)} median={statistics.median(sdk_batch_sizes):.0f}")
    if sdk_recv_gaps_ms:
        print(f"recv_gap_ms: p95={_pct(sorted(sdk_recv_gaps_ms), 0.95)} max={max(sdk_recv_gaps_ms)}")

    print("")
    print("== RAW ==")
    print(f"out: {raw_path}")
    print(f"trades: {raw_stats.n} dup_frac(time,px,sz): {raw_dup:.3f}")
    print(
        "lag_ms:"
        f" med={raw_stats.median_ms:.0f} p95={raw_stats.p95_ms:.0f} p99={raw_stats.p99_ms:.0f}"
        f" min={raw_stats.min_ms} max={raw_stats.max_ms}"
    )
    print(
        "frac_lag_gt:"
        f" 1s={raw_stats.frac_gt_1s:.3f} 5s={raw_stats.frac_gt_5s:.3f}"
        f" 10s={raw_stats.frac_gt_10s:.3f} 30s={raw_stats.frac_gt_30s:.3f}"
    )
    print(f"raw_debug: {_latest_trades_jsonl_gz(raw_root).parent / 'hyperliquid_debug.jsonl'}")
    print("")
    print(f"run_root: {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

