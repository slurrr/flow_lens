#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate runlist from rankings.")
    parser.add_argument(
        "--rankings",
        default="docs/diagnostics/scenario_runs/top5_by_label.json",
        help="Ranking JSON produced by scenario_rank.py.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=1,
        help="Number of scenarios per symbol/label.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols to include (defaults to all).",
    )
    parser.add_argument(
        "--out",
        default="docs/diagnostics/scenario_runs/top1_runlist.sh",
        help="Output shell script path.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rankings_path = Path(args.rankings)
    if not rankings_path.exists():
        raise SystemExit(f"Missing rankings: {rankings_path}")
    rankings = json.loads(rankings_path.read_text(encoding="utf-8"))
    groups = rankings.get("groups", {})

    symbols_filter = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""]

    for symbol in sorted(groups):
        if symbols_filter and symbol not in symbols_filter:
            continue
        lines.append(f"# === {symbol} ===")
        labels = groups.get(symbol, {})
        for label in sorted(labels):
            items = labels.get(label, [])
            if not items:
                continue
            lines.append(f"# -- {label} --")
            for item in items[: args.top_n]:
                path = item.get("file", "")
                if not path:
                    continue
                lines.append(
                    "python scripts/scenario_replay.py --scenario-file "
                    f"{path} --data-dir logs/backfill --strip-1000 --out-dir logs/replay --gzip"
                )
            lines.append("")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote runlist: {out_path}")


if __name__ == "__main__":
    main()
