#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScenarioFile:
    scenario_id: str
    symbol: str
    label: str
    start_ms: int
    end_ms: int
    replay_start_ms: int
    replay_end_ms: int
    score: float
    metrics: dict[str, float]
    path: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split scenario index into per-scenario files.")
    parser.add_argument(
        "--in",
        dest="input_path",
        default="docs/diagnostics/scenarios-jan-raw.json",
        help="Scenario index JSON path.",
    )
    parser.add_argument(
        "--out-dir",
        default="docs/diagnostics/scenario_runs",
        help="Output directory for scenario files.",
    )
    parser.add_argument(
        "--pre-roll-min",
        type=float,
        default=10.0,
        help="Pre-roll minutes to include before scenario start.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    input_path = Path(args.input_path)
    if not input_path.exists():
        raise SystemExit(f"Missing scenario index: {input_path}")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit("No scenarios found in index.")

    pre_roll_ms = int(args.pre_roll_min * 60_000)
    manifest: list[dict[str, object]] = []

    for idx, scenario in enumerate(scenarios, 1):
        symbol = str(scenario.get("symbol", "")).upper()
        label = str(scenario.get("label", "unknown"))
        start_ms = int(scenario.get("start_ms", 0))
        end_ms = int(scenario.get("end_ms", 0))
        scenario_id = f"{idx:03d}"
        replay_start_ms = max(0, start_ms - pre_roll_ms)
        replay_end_ms = end_ms

        symbol_dir = out_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scenario_id}_{symbol}_{label}_{start_ms}_{end_ms}.json"
        path = symbol_dir / filename

        scenario_payload = {
            "id": scenario_id,
            "symbol": symbol,
            "label": label,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "pre_roll_ms": pre_roll_ms,
            "replay_start_ms": replay_start_ms,
            "replay_end_ms": replay_end_ms,
            "score": float(scenario.get("score", 0.0)),
            "metrics": scenario.get("metrics", {}),
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(scenario_payload, handle, indent=2)

        manifest.append(
            {
                "id": scenario_id,
                "symbol": symbol,
                "label": label,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "replay_start_ms": replay_start_ms,
                "replay_end_ms": replay_end_ms,
                "file": str(path),
                "command": (
                    "python scripts/scenario_replay.py "
                    f"--scenario-file {path} --data-dir logs/backfill "
                    "--strip-1000 --out-dir logs/replay --gzip"
                ),
            }
        )

    manifest_path = out_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "source_index": str(input_path),
                "pre_roll_ms": pre_roll_ms,
                "count": len(manifest),
                "scenarios": manifest,
            },
            handle,
            indent=2,
        )

    print(f"Wrote {len(manifest)} scenarios -> {out_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
