#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank scenarios by score per symbol/label.")
    parser.add_argument(
        "--runs-dir",
        default="docs/diagnostics/scenario_runs",
        help="Directory containing per-scenario JSON files.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Top N per label/symbol (default 5).",
    )
    parser.add_argument(
        "--out",
        default="docs/diagnostics/scenario_runs/top5_by_label.json",
        help="Output JSON file.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    runs_dir = Path(args.runs_dir)
    if not runs_dir.exists():
        raise SystemExit(f"Missing runs dir: {runs_dir}")

    scenarios: list[dict] = []
    for path in runs_dir.rglob("*.json"):
        if path.name == "manifest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        symbol = str(payload.get("symbol", "")).upper()
        label = str(payload.get("label", ""))
        if not symbol or not label:
            continue
        score = payload.get("score", 0.0)
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        scenarios.append(
            {
                "symbol": symbol,
                "label": label,
                "id": str(payload.get("id", "")),
                "start_ms": int(payload.get("start_ms", 0)),
                "end_ms": int(payload.get("end_ms", 0)),
                "score": score_value,
                "metrics": payload.get("metrics", {}),
                "file": str(path),
            }
        )

    grouped: dict[str, dict[str, list[dict]]] = {}
    for scenario in scenarios:
        grouped.setdefault(scenario["symbol"], {}).setdefault(scenario["label"], []).append(scenario)

    groups: dict[str, dict[str, list[dict]]] = {}
    for symbol, labels in grouped.items():
        symbol_out: dict[str, list[dict]] = {}
        for label, items in labels.items():
            items_sorted = sorted(items, key=lambda item: item["score"], reverse=True)
            symbol_out[label] = items_sorted[: args.top_n]
        groups[symbol] = symbol_out

    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "top_n": args.top_n,
        "groups": groups,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote rankings: {out_path}")


if __name__ == "__main__":
    main()
