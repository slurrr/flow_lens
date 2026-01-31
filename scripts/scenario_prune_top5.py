#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune scenario files to top-N per label.")
    parser.add_argument(
        "--rankings",
        default="docs/diagnostics/scenario_runs/top5_by_label.json",
        help="Ranking JSON produced by scenario_rank.py.",
    )
    parser.add_argument(
        "--runs-dir",
        default="docs/diagnostics/scenario_runs",
        help="Scenario runs directory.",
    )
    parser.add_argument(
        "--symbols",
        default="BTC,SOL",
        help="Comma-separated symbols to prune.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rankings_path = Path(args.rankings)
    runs_dir = Path(args.runs_dir)
    if not rankings_path.exists():
        raise SystemExit(f"Missing rankings: {rankings_path}")
    if not runs_dir.exists():
        raise SystemExit(f"Missing runs dir: {runs_dir}")

    rankings = json.loads(rankings_path.read_text(encoding="utf-8"))
    groups = rankings.get("groups", {})
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    keep_files: set[Path] = set()
    for symbol in symbols:
        label_map = groups.get(symbol, {})
        for items in label_map.values():
            for item in items:
                file_path = Path(item.get("file", ""))
                if file_path:
                    keep_files.add(file_path)

    deleted = 0
    for symbol in symbols:
        symbol_dir = runs_dir / symbol
        if not symbol_dir.exists():
            continue
        for path in symbol_dir.glob("*.json"):
            if path.name == "manifest.json":
                continue
            if path not in keep_files:
                path.unlink()
                deleted += 1

    manifest_path = runs_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenarios = manifest.get("scenarios", [])
        filtered = []
        for entry in scenarios:
            file_path = Path(entry.get("file", ""))
            if file_path.exists():
                filtered.append(entry)
        manifest["count"] = len(filtered)
        manifest["scenarios"] = filtered
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Pruned {deleted} scenarios.")


if __name__ == "__main__":
    main()
