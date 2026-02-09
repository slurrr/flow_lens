#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SuiteConfig:
    base_config: Path
    out_root: Path
    docs_out: Path
    runlist: Path
    data_dir: Path
    strip_1000: bool
    gzip: bool
    tag: str
    overrides: dict[str, str]


def _parse_args() -> SuiteConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Run the BTC+SOL top1 replay suite into a unique directory and generate a "
            "diagnostics summary. Avoids destructive cleanup by never deleting old runs."
        )
    )
    parser.add_argument(
        "--base-config",
        default="config/app.toml",
        help="Base app config TOML to start from (default: config/app.toml).",
    )
    parser.add_argument(
        "--out-root",
        default="logs/tuning_runs",
        help="Directory to store replay outputs (default: logs/tuning_runs).",
    )
    parser.add_argument(
        "--docs-out",
        default="docs/diagnostics",
        help="Directory to store summary outputs (default: docs/diagnostics).",
    )
    parser.add_argument(
        "--runlist",
        default="docs/diagnostics/scenario_runs/top1_runlist_btc_sol.sh",
        help="Shell runlist to parse for scenario files (default: top1_runlist_btc_sol.sh).",
    )
    parser.add_argument(
        "--data-dir",
        default="logs/backfill",
        help="Backfill data directory (default: logs/backfill).",
    )
    parser.add_argument(
        "--strip-1000",
        action="store_true",
        help="Map perp 1000-prefixed symbols back to base symbol (passed to scenario_replay).",
    )
    parser.add_argument(
        "--no-gzip",
        action="store_true",
        help="Disable gzip output (default: gzip enabled).",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="Optional tag for the run directory / summary filename.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Override a [runtime] key in the generated config (repeatable). "
            "Examples: --set tanh_k=0.24 --set effort_scale_percentile=0.5"
        ),
    )
    args = parser.parse_args()

    overrides: dict[str, str] = {}
    for item in args.set:
        if "=" not in item:
            raise SystemExit(f"Invalid --set (expected KEY=VALUE): {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(f"Invalid --set (empty key): {item}")
        overrides[key] = value

    return SuiteConfig(
        base_config=Path(args.base_config),
        out_root=Path(args.out_root),
        docs_out=Path(args.docs_out),
        runlist=Path(args.runlist),
        data_dir=Path(args.data_dir),
        strip_1000=bool(args.strip_1000),
        gzip=not bool(args.no_gzip),
        tag=str(args.tag).strip(),
        overrides=overrides,
    )


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(float(value))
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value)}")


def _write_overridden_config(*, base_path: Path, out_path: Path, overrides: dict[str, str]) -> None:
    base_data = tomllib.loads(base_path.read_text(encoding="utf-8"))
    adapters = base_data.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        raise SystemExit(f"Missing or invalid [adapters] in {base_path}")
    sources = base_data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise SystemExit(f"Missing or invalid [sources] in {base_path}")
    runtime = base_data.get("runtime")
    if not isinstance(runtime, dict):
        raise SystemExit(f"Missing or invalid [runtime] in {base_path}")

    runtime_effective: dict[str, Any] = dict(runtime)
    for key, raw in overrides.items():
        # Minimal type inference for convenience; user can still pass quoted TOML strings via raw.
        lowered = raw.lower()
        parsed: Any
        if lowered in {"true", "false"}:
            parsed = lowered == "true"
        else:
            try:
                if "." in raw or "e" in lowered:
                    parsed = float(raw)
                else:
                    parsed = int(raw)
            except ValueError:
                parsed = raw
        runtime_effective[key] = parsed

    lines: list[str] = []
    for adapter_name in sorted(adapters.keys()):
        adapter = adapters.get(adapter_name)
        if not isinstance(adapter, dict):
            continue
        lines.append(f"[adapters.{adapter_name}]")
        adapter_type = adapter.get("type", "")
        symbols = adapter.get("symbols", [])
        lines.append(f'type = {_format_toml_value(str(adapter_type))}')
        lines.append(f"symbols = {_format_toml_value(list(symbols) if isinstance(symbols, list) else [])}")
        lines.append("")

    for source_id in sorted(sources.keys()):
        source = sources.get(source_id)
        if not isinstance(source, dict):
            continue
        lines.append(f"[sources.{source_id}]")
        for key in sorted(source.keys()):
            lines.append(f"{key} = {_format_toml_value(source[key])}")
        lines.append("")

    lines.append("[runtime]")
    for key in sorted(runtime_effective.keys()):
        lines.append(f"{key} = {_format_toml_value(runtime_effective[key])}")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _parse_scenarios_from_runlist(runlist_path: Path) -> list[Path]:
    text = runlist_path.read_text(encoding="utf-8")
    scenarios: list[Path] = []
    token = "--scenario-file"
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or token not in line:
            continue
        parts = line.split()
        if token not in parts:
            continue
        idx = parts.index(token)
        if idx + 1 >= len(parts):
            continue
        scenario = Path(parts[idx + 1])
        scenarios.append(scenario)
    if not scenarios:
        raise SystemExit(f"No scenario files found in runlist: {runlist_path}")
    return scenarios


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    cfg = _parse_args()
    if not cfg.base_config.exists():
        raise SystemExit(f"Missing base config: {cfg.base_config}")
    if not cfg.runlist.exists():
        raise SystemExit(f"Missing runlist: {cfg.runlist}")
    if not cfg.data_dir.exists():
        raise SystemExit(f"Missing data dir: {cfg.data_dir}")

    scenarios = _parse_scenarios_from_runlist(cfg.runlist)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    tag = cfg.tag or "-".join(
        [f"{key}={value}".replace("/", "_").replace(" ", "") for key, value in cfg.overrides.items()]
    )
    tag = tag or "run"
    safe_tag = "".join(ch if ch.isalnum() or ch in {"-", "_", "=", "."} else "_" for ch in tag)[:120]
    run_dir = cfg.out_root / f"{timestamp}_{safe_tag}"
    run_dir.mkdir(parents=True, exist_ok=False)

    effective_config_path = run_dir / "app_effective.toml"
    _write_overridden_config(
        base_path=cfg.base_config,
        out_path=effective_config_path,
        overrides=cfg.overrides,
    )

    py = str(Path(".venv/bin/python"))
    for scenario_file in scenarios:
        cmd = [
            py,
            "scripts/scenario_replay.py",
            "--scenario-file",
            str(scenario_file),
            "--data-dir",
            str(cfg.data_dir),
            "--out-dir",
            str(run_dir),
            "--config",
            str(effective_config_path),
        ]
        if cfg.strip_1000:
            cmd.append("--strip-1000")
        if cfg.gzip:
            cmd.append("--gzip")
        _run(cmd)

    summary_name = f"diagnostics-summary-{timestamp}_{safe_tag}.txt"
    cfg.docs_out.mkdir(parents=True, exist_ok=True)
    summary_path = cfg.docs_out / summary_name
    _run(
        [
            py,
            "scripts/diagnostics_report.py",
            "--dir",
            str(run_dir),
            "--config",
            str(effective_config_path),
            "--out",
            str(summary_path),
        ]
    )

    print(f"run_dir: {run_dir}")
    print(f"effective_config: {effective_config_path}")
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
