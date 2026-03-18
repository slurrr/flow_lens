#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import random
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "docs/diagnostics/scenario_runs/BTC"
DEFAULT_CONFIG = ROOT / "config/app_btc.toml"
DEFAULT_DATA_DIR = ROOT / "logs/backfill"
DEFAULT_OUT_ROOT = ROOT / "logs/tuning_runs/btc_auto_tune"
PY = str(ROOT / ".venv/bin/python")

REPLAY_RE = re.compile(
    r"^flow_lens_replay-(?P<symbol>[^-]+)-(?P<regime>.+)-(?P<scenario_id>\d+)-\d{8}-\d{6}\.jsonl(?:\.gz)?$"
)
SUMMARY_RE = re.compile(
    r"^BTC (?P<regime>\w+) \(runs=(?P<runs>\d+), records=(?P<records>\d+)\)$"
)
DEFAULT_NEUTRAL_ABS_Y_RAW = 0.10
DEFAULT_SUSTAINED_ESCAPE_UPDATES = 3


@dataclass(frozen=True)
class Candidate:
    name: str
    overrides: dict[str, Any]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-tune BTC Flow Lens config via scenario replays.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--stage1-count", type=int, default=18)
    parser.add_argument("--stage1-top", type=int, default=4)
    parser.add_argument("--stage2-local", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--fixed-update-window-seconds",
        type=float,
        default=None,
        help="If set, lock update_window_seconds to this value during search.",
    )
    parser.add_argument(
        "--allow-update-window-search",
        action="store_true",
        help="Allow update_window_seconds to vary during search.",
    )
    parser.add_argument(
        "--update-window-min-seconds",
        type=float,
        default=0.69,
        help="Minimum update_window_seconds when cadence search is enabled.",
    )
    parser.add_argument(
        "--update-window-max-seconds",
        type=float,
        default=1.5,
        help="Maximum update_window_seconds when cadence search is enabled.",
    )
    parser.add_argument(
        "--neutral-abs-y-raw",
        type=float,
        default=DEFAULT_NEUTRAL_ABS_Y_RAW,
        help="Absolute Y_raw threshold treated as neutral for flip/hold analysis.",
    )
    parser.add_argument(
        "--sustained-escape-updates",
        type=int,
        default=DEFAULT_SUSTAINED_ESCAPE_UPDATES,
        help="Consecutive same-sign non-neutral updates needed to declare a sustained escape.",
    )
    return parser.parse_args()


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


def _write_config(base_path: Path, out_path: Path, overrides: dict[str, Any]) -> None:
    base_data = tomllib.loads(base_path.read_text(encoding="utf-8"))
    adapters = base_data.get("adapters")
    sources = base_data.get("sources")
    runtime = base_data.get("runtime")
    if not isinstance(adapters, dict) or not isinstance(sources, dict) or not isinstance(runtime, dict):
        raise SystemExit(f"Invalid base config: {base_path}")
    runtime_effective = {
        key: value
        for key, value in runtime.items()
        if key not in {"hygiene", "dist_state"}
    }
    runtime_effective.update(overrides)

    lines: list[str] = []
    for adapter_name in sorted(adapters):
        adapter = adapters[adapter_name]
        if not isinstance(adapter, dict):
            continue
        lines.append(f"[adapters.{adapter_name}]")
        lines.append(f'type = {_format_toml_value(str(adapter.get("type", "")))}')
        symbols = adapter.get("symbols", [])
        lines.append(f"symbols = {_format_toml_value(list(symbols) if isinstance(symbols, list) else [])}")
        lines.append("")

    for source_id in sorted(sources):
        source = sources[source_id]
        if not isinstance(source, dict):
            continue
        lines.append(f"[sources.{source_id}]")
        for key in sorted(source):
            lines.append(f"{key} = {_format_toml_value(source[key])}")
        lines.append("")

    lines.append("[runtime]")
    for key in sorted(runtime_effective):
        lines.append(f"{key} = {_format_toml_value(runtime_effective[key])}")
    lines.append("")

    for section_name in ("runtime.hygiene", "runtime.dist_state"):
        section = _nested_get(base_data, section_name.split("."))
        if not isinstance(section, dict):
            continue
        lines.append(f"[{section_name}]")
        for key in sorted(section):
            lines.append(f"{key} = {_format_toml_value(section[key])}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _nested_get(data: dict[str, Any], path: Sequence[str]) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("*.json"))


def _scenario_label(path: Path) -> str:
    parts = path.stem.split("_")
    if len(parts) > 3 and parts[2] == "trend":
        return f"{parts[2]}_{parts[3]}"
    return parts[2] if len(parts) > 2 else "unknown"


def _filter_scenarios(paths: list[Path], regimes: set[str]) -> list[Path]:
    return [path for path in paths if _scenario_label(path) in regimes]


def _run_scenario(
    scenario_file: Path,
    *,
    config_path: Path,
    out_dir: Path,
    data_dir: Path,
) -> None:
    cmd = [
        PY,
        "scripts/scenario_replay.py",
        "--scenario-file",
        str(scenario_file),
        "--data-dir",
        str(data_dir),
        "--out-dir",
        str(out_dir),
        "--config",
        str(config_path),
        "--gzip",
    ]
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run_suite(
    *,
    candidate: Candidate,
    scenarios: list[Path],
    base_config: Path,
    data_dir: Path,
    out_root: Path,
    workers: int,
) -> tuple[Path, dict[str, dict[str, float]]]:
    run_dir = out_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{candidate.name}"
    run_dir.mkdir(parents=True, exist_ok=False)
    config_path = run_dir / "app_effective.toml"
    _write_config(base_config, config_path, candidate.overrides)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _run_scenario,
                scenario,
                config_path=config_path,
                out_dir=run_dir,
                data_dir=data_dir,
            )
            for scenario in scenarios
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    summary_path = run_dir / "summary.txt"
    subprocess.run(
        [
            PY,
            "scripts/diagnostics_report.py",
            "--dir",
            str(run_dir),
            "--out",
            str(summary_path),
            "--config",
            str(config_path),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return run_dir, _analyze_run_dir(
        run_dir,
        neutral_abs_y_raw=SCORING_PARAMS["neutral_abs_y_raw"],
        sustained_escape_updates=int(SCORING_PARAMS["sustained_escape_updates"]),
    )


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _parse_replay_path(path: Path) -> tuple[str, str, str] | None:
    match = REPLAY_RE.match(path.name)
    if not match:
        return None
    return (
        match.group("symbol").upper(),
        match.group("regime"),
        match.group("scenario_id"),
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return values[0]
    if pct >= 1:
        return values[-1]
    idx = int(round(pct * (len(values) - 1)))
    return values[idx]


def _sign_with_neutral(value: float, neutral_abs: float) -> int:
    if value >= neutral_abs:
        return 1
    if value <= -neutral_abs:
        return -1
    return 0


def _flip_fraction(signs: list[int]) -> float:
    if len(signs) < 2:
        return 0.0
    flips = sum(1 for prev, cur in zip(signs, signs[1:]) if prev != cur)
    return flips / (len(signs) - 1)


def _run_lengths(values: list[int]) -> list[int]:
    if not values:
        return []
    runs: list[int] = []
    current = values[0]
    run = 1
    for value in values[1:]:
        if value == current:
            run += 1
            continue
        runs.append(run)
        current = value
        run = 1
    runs.append(run)
    return runs


def _first_sustained_escape(
    signs: list[int],
    *,
    sustained_updates: int,
) -> tuple[int | None, int]:
    if sustained_updates <= 1:
        for idx, sign in enumerate(signs):
            if sign != 0:
                return idx, sign
        return None, 0
    run_sign = 0
    run_len = 0
    run_start = 0
    for idx, sign in enumerate(signs):
        if sign == 0:
            run_sign = 0
            run_len = 0
            continue
        if sign == run_sign:
            run_len += 1
        else:
            run_sign = sign
            run_len = 1
            run_start = idx
        if run_len >= sustained_updates:
            return run_start, run_sign
    return None, 0


def _scenario_metrics(
    path: Path,
    *,
    neutral_abs_y_raw: float,
    sustained_escape_updates: int,
) -> dict[str, float]:
    records: list[dict[str, Any]] = []
    with _open_text(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            if "_meta" in obj or "event_type" in obj:
                continue
            records.append(obj)

    y_raw = [float(record.get("Y_raw", 0.0)) for record in records]
    if not y_raw:
        return {
            "records": 0.0,
            "neutral_rate": 1.0,
            "non_neutral_rate": 0.0,
            "p95_abs_y_raw": 0.0,
            "p95_abs_y_raw_non_neutral": 0.0,
            "p99_abs_y_raw": 0.0,
            "sat_rate": 0.0,
            "flip_per_update_all": 0.0,
            "flip_per_update_non_neutral": 0.0,
            "dominant_sign_share_non_neutral": 0.0,
            "post_escape_hold_rate": 0.0,
            "escape_found_rate": 0.0,
            "non_neutral_run_p95": 0.0,
        }

    abs_y = sorted(abs(value) for value in y_raw)
    non_neutral_abs_y = sorted(abs(value) for value in y_raw if abs(value) >= neutral_abs_y_raw)
    signs = [_sign_with_neutral(value, neutral_abs_y_raw) for value in y_raw]
    non_neutral_signs = [sign for sign in signs if sign != 0]

    positive = sum(1 for sign in non_neutral_signs if sign > 0)
    negative = sum(1 for sign in non_neutral_signs if sign < 0)
    dominant_sign_share = (
        max(positive, negative) / len(non_neutral_signs) if non_neutral_signs else 0.0
    )

    escape_start, escape_sign = _first_sustained_escape(
        signs, sustained_updates=sustained_escape_updates
    )
    if escape_start is None or escape_sign == 0:
        post_escape_hold_rate = 0.0
        escape_found_rate = 0.0
    else:
        post_escape_non_neutral = [
            sign for sign in signs[escape_start:] if sign != 0
        ]
        post_escape_hold_rate = (
            sum(1 for sign in post_escape_non_neutral if sign == escape_sign)
            / len(post_escape_non_neutral)
            if post_escape_non_neutral
            else 0.0
        )
        escape_found_rate = 1.0

    non_neutral_runs = _run_lengths(non_neutral_signs)
    return {
        "records": float(len(records)),
        "neutral_rate": signs.count(0) / len(signs),
        "non_neutral_rate": len(non_neutral_signs) / len(signs),
        "p95_abs_y_raw": _percentile(abs_y, 0.95),
        "p95_abs_y_raw_non_neutral": _percentile(non_neutral_abs_y, 0.95),
        "p99_abs_y_raw": _percentile(abs_y, 0.99),
        "sat_rate": sum(1 for value in abs_y if value >= 0.95) / len(abs_y),
        "flip_per_update_all": _flip_fraction(signs),
        "flip_per_update_non_neutral": _flip_fraction(non_neutral_signs),
        "dominant_sign_share_non_neutral": dominant_sign_share,
        "post_escape_hold_rate": post_escape_hold_rate,
        "escape_found_rate": escape_found_rate,
        "non_neutral_run_p95": _percentile(sorted(float(run) for run in non_neutral_runs), 0.95),
    }


def _median_metric(
    items: list[dict[str, float]],
    key: str,
) -> float:
    values = [item[key] for item in items if key in item]
    if not values:
        return 0.0
    return float(median(values))


def _analyze_run_dir(
    run_dir: Path,
    *,
    neutral_abs_y_raw: float,
    sustained_escape_updates: int,
) -> dict[str, dict[str, float]]:
    per_regime: dict[str, list[dict[str, float]]] = {}
    for path in sorted(list(run_dir.glob("*.jsonl")) + list(run_dir.glob("*.jsonl.gz"))):
        parsed = _parse_replay_path(path)
        if parsed is None:
            continue
        _symbol, regime, _scenario_id = parsed
        per_regime.setdefault(regime, []).append(
            _scenario_metrics(
                path,
                neutral_abs_y_raw=neutral_abs_y_raw,
                sustained_escape_updates=sustained_escape_updates,
            )
        )
    out: dict[str, dict[str, float]] = {}
    for regime, metrics in per_regime.items():
        out[regime] = {
            "records": _median_metric(metrics, "records"),
            "neutral_rate": _median_metric(metrics, "neutral_rate"),
            "non_neutral_rate": _median_metric(metrics, "non_neutral_rate"),
            "p95_abs_y_raw": _median_metric(metrics, "p95_abs_y_raw"),
            "p95_abs_y_raw_non_neutral": _median_metric(metrics, "p95_abs_y_raw_non_neutral"),
            "p99_abs_y_raw": _median_metric(metrics, "p99_abs_y_raw"),
            "sat_rate": _median_metric(metrics, "sat_rate"),
            "flip_per_update_all": _median_metric(metrics, "flip_per_update_all"),
            "flip_per_update_non_neutral": _median_metric(metrics, "flip_per_update_non_neutral"),
            "dominant_sign_share_non_neutral": _median_metric(
                metrics, "dominant_sign_share_non_neutral"
            ),
            "post_escape_hold_rate": _median_metric(metrics, "post_escape_hold_rate"),
            "escape_found_rate": _median_metric(metrics, "escape_found_rate"),
            "non_neutral_run_p95": _median_metric(metrics, "non_neutral_run_p95"),
        }
    return out


def _parse_summary(path: Path) -> dict[str, dict[str, float]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: dict[str, dict[str, float]] = {}
    idx = 0
    while idx < len(lines):
        match = SUMMARY_RE.match(lines[idx].strip())
        if not match:
            idx += 1
            continue
        regime = match.group("regime")
        metrics_line = lines[idx + 2].strip()
        persist_line = lines[idx + 3].strip()
        aux_line = lines[idx + 4].strip()
        metrics = {
            "p95_y_raw": _extract(metrics_line, r"p95\|Y_raw\| ([0-9.]+)"),
            "p99_y_raw": _extract(metrics_line, r"p99\|Y_raw\| ([0-9.]+)"),
            "sat": _extract(metrics_line, r"Y_raw_sat ([0-9.]+)"),
            "flip_y_raw": _extract(metrics_line, r"Flip Y_raw ([0-9.]+)/m"),
            "flip_y": _extract(metrics_line, r"Y ([0-9.]+)/m"),
            "deadband": _extract(metrics_line, r"Deadband ([0-9.]+)"),
            "gate_low": _extract(metrics_line, r"Gate low ([0-9.]+)"),
            "dir_mismatch": _extract(metrics_line, r"Y_raw_dir_mismatch ([0-9.]+)"),
            "persist_p95": _extract(persist_line, r"p95\|S\| ([0-9.]+)"),
            "mode_active": _extract(persist_line, r"Mode a/p/d ([0-9.]+)/"),
            "mode_pivot": _extract(persist_line, r"Mode a/p/d [0-9.]+/([0-9.]+)/"),
            "mode_dormant": _extract(persist_line, r"Mode a/p/d [0-9.]+/[0-9.]+/([0-9.]+)"),
            "disp_ratio_p50": _extract(aux_line, r"\|disp\|/scale ([0-9.]+)"),
        }
        out[regime] = metrics
        idx += 5
    return out


def _extract(text: str, pattern: str) -> float:
    match = re.search(pattern, text)
    if not match:
        return 0.0
    return float(match.group(1))


def _interval_penalty(value: float, low: float, high: float) -> float:
    if low <= value <= high:
        return 0.0
    if value < low:
        return ((low - value) / max(low, 1e-9)) ** 2
    return ((value - high) / max(high, 1e-9)) ** 2


def _upper_penalty(value: float, limit: float) -> float:
    if value <= limit:
        return 0.0
    return ((value - limit) / max(limit, 1e-9)) ** 2


def _lower_penalty(value: float, limit: float) -> float:
    if value >= limit:
        return 0.0
    return ((limit - value) / max(limit, 1e-9)) ** 2


def _score_trend(metrics: dict[str, float]) -> float:
    score = 0.0
    score += 2.0 * _interval_penalty(metrics["p95_abs_y_raw_non_neutral"], 0.62, 0.82)
    score += 2.8 * _interval_penalty(metrics["p99_abs_y_raw"], 0.92, 0.97)
    score += 1.6 * _interval_penalty(metrics["sat_rate"], 0.01, 0.05)
    score += 1.4 * _upper_penalty(metrics["flip_per_update_non_neutral"], 0.10)
    score += 1.8 * _lower_penalty(metrics["dominant_sign_share_non_neutral"], 0.72)
    score += 2.0 * _lower_penalty(metrics["post_escape_hold_rate"], 0.75)
    score += 1.2 * _lower_penalty(metrics["escape_found_rate"], 1.0)
    score += 0.8 * _upper_penalty(metrics["neutral_rate"], 0.55)
    return score


def _score_soft(metrics: dict[str, float], *, regime: str) -> float:
    score = 0.0
    if regime == "chop":
        score += 0.7 * _interval_penalty(metrics["sat_rate"], 0.00, 0.03)
        score += 0.8 * _upper_penalty(metrics["p99_abs_y_raw"], 0.96)
        score += 0.6 * _upper_penalty(metrics["dominant_sign_share_non_neutral"], 0.78)
        score += 0.6 * _upper_penalty(metrics["post_escape_hold_rate"], 0.82)
        score += 0.4 * _lower_penalty(metrics["neutral_rate"], 0.20)
    else:
        score += 0.8 * _interval_penalty(metrics["sat_rate"], 0.04, 0.08)
        score += 0.9 * _interval_penalty(metrics["p99_abs_y_raw"], 0.95, 1.00)
        score += 0.6 * _lower_penalty(metrics["p95_abs_y_raw_non_neutral"], 0.70)
        score += 0.4 * _upper_penalty(metrics["flip_per_update_non_neutral"], 0.12)
    return score


def _score_summary(summary: dict[str, dict[str, float]], *, include_soft: bool) -> float:
    score = 0.0
    for regime in ("trend_up", "trend_down"):
        score += _score_trend(summary.get(regime, {}))
    if include_soft:
        for regime in ("chop", "impulse"):
            if regime in summary:
                score += _score_soft(summary[regime], regime=regime)
    return score


def _round_value(key: str, value: float) -> float:
    if key.endswith("_ticks"):
        return int(round(value))
    if key in {"scale_window_seconds"}:
        return int(round(value))
    return round(float(value), 4)


def _candidate_name(prefix: str, overrides: dict[str, Any]) -> str:
    parts = [prefix]
    for key in sorted(overrides):
        value = overrides[key]
        if isinstance(value, float):
            parts.append(f"{key}={value:.3f}")
        else:
            parts.append(f"{key}={value}")
    name = "__".join(parts)
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "=", "."} else "_" for ch in name)[:180]


def _seed_candidates(base: dict[str, Any], rng: random.Random, count: int) -> list[Candidate]:
    fixed_update_window = SCORING_PARAMS["fixed_update_window_seconds"]
    search_update_window = bool(SCORING_PARAMS["allow_update_window_search"])
    search_default_update_window = SCORING_PARAMS["search_default_update_window"]

    handpicked: list[dict[str, Any]] = [
        {},
        {
            "tanh_k": 0.16,
        },
        {
            "tanh_k": 0.14,
        },
        {
            "tanh_k": 0.12,
        },
        {
            "tanh_k": 0.14,
        },
        {
            "tanh_k": 0.12,
        },
        {
            "tanh_k": 0.16,
            "disp_scale_multiplier": 0.14,
        },
        {
            "tanh_k": 0.14,
            "disp_scale_multiplier": 0.14,
        },
        {
            "tanh_k": 0.12,
            "disp_scale_multiplier": 0.14,
        },
        {
            "tanh_k": 0.14,
            "scale_window_seconds": 420.0,
            "effort_scale_percentile": 0.60,
            "effort_floor_multiplier": 0.22,
            "effort_floor_ticks": 60,
        },
        {
            "tanh_k": 0.12,
            "scale_window_seconds": 420.0,
            "effort_scale_percentile": 0.60,
            "effort_floor_multiplier": 0.22,
            "effort_floor_ticks": 60,
        },
    ]
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for overrides in handpicked:
        if fixed_update_window is not None:
            overrides["update_window_seconds"] = fixed_update_window
        elif search_update_window:
            overrides["update_window_seconds"] = search_default_update_window
        name = _candidate_name("seed", overrides)
        seen.add(json.dumps(overrides, sort_keys=True))
        candidates.append(Candidate(name=name, overrides=overrides))
    ranges: dict[str, list[Any]] = {
        "tanh_k": [0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.24, 0.30],
        "disp_scale_multiplier": [0.08, 0.10, 0.12, 0.14, 0.16],
        "disp_scale_percentile": [0.45, 0.60, 0.80],
        "scale_window_seconds": [240.0, 300.0, 420.0, 600.0],
        "effort_scale_percentile": [0.45, 0.60, 0.80],
        "effort_floor_multiplier": [0.10, 0.15, 0.22, 0.30],
        "effort_floor_ticks": [45, 60, 90],
        "smoothing_effectiveness_alpha": [0.06, 0.08, 0.10, 0.12, 0.15],
    }
    if search_update_window:
        update_values = [0.69, 0.85, 1.0, 1.15, 1.25, 1.35, 1.5]
        min_value = float(SCORING_PARAMS["update_window_min_seconds"])
        max_value = float(SCORING_PARAMS["update_window_max_seconds"])
        ranges["update_window_seconds"] = [
            value for value in update_values if min_value <= value <= max_value
        ]
    while len(candidates) < count:
        overrides: dict[str, Any] = {}
        if search_update_window:
            overrides["update_window_seconds"] = search_default_update_window
        keys = list(ranges)
        rng.shuffle(keys)
        mutate_count = rng.randint(2, min(5, len(keys)))
        for key in keys[:mutate_count]:
            values = ranges[key]
            choice = rng.choice(values)
            if choice != base.get(key):
                overrides[key] = choice
        if fixed_update_window is not None:
            overrides["update_window_seconds"] = fixed_update_window
        encoded = json.dumps(overrides, sort_keys=True)
        if encoded in seen:
            continue
        seen.add(encoded)
        candidates.append(Candidate(name=_candidate_name("stage1", overrides), overrides=overrides))
    return candidates


def _local_neighbors(base_candidate: Candidate) -> list[Candidate]:
    search_update_window = bool(SCORING_PARAMS["allow_update_window_search"])
    fixed_update_window = SCORING_PARAMS["fixed_update_window_seconds"]
    search_default_update_window = SCORING_PARAMS["search_default_update_window"]
    steps: dict[str, list[float]] = {
        "tanh_k": [-0.08, -0.05, -0.03, 0.03, 0.05, 0.08],
        "disp_scale_multiplier": [-0.04, -0.02, 0.02, 0.04],
        "disp_scale_percentile": [-0.15, 0.15],
        "scale_window_seconds": [-120.0, 120.0, 180.0],
        "effort_scale_percentile": [-0.15, 0.15],
        "effort_floor_multiplier": [-0.08, 0.08],
        "effort_floor_ticks": [-15.0, 15.0, 30.0],
        "smoothing_effectiveness_alpha": [-0.05, -0.03, 0.03, 0.05],
    }
    if search_update_window:
        steps["update_window_seconds"] = [-0.30, -0.15, 0.15, 0.30]
    bounds: dict[str, tuple[float, float]] = {
        "tanh_k": (0.08, 0.35),
        "update_window_seconds": (
            float(SCORING_PARAMS["update_window_min_seconds"]),
            float(SCORING_PARAMS["update_window_max_seconds"]),
        ),
        "disp_scale_multiplier": (0.04, 0.18),
        "disp_scale_percentile": (0.30, 0.90),
        "scale_window_seconds": (240.0, 720.0),
        "effort_scale_percentile": (0.35, 0.90),
        "effort_floor_multiplier": (0.05, 0.40),
        "effort_floor_ticks": (30.0, 120.0),
        "smoothing_effectiveness_alpha": (0.05, 0.20),
    }
    neighbors: list[Candidate] = []
    seen: set[str] = set()
    current = dict(base_candidate.overrides)
    if search_update_window and "update_window_seconds" not in current:
        current["update_window_seconds"] = search_default_update_window
    for key, deltas in steps.items():
        base_value = float(current.get(key, DEFAULTS[key]))
        low, high = bounds[key]
        for delta in deltas:
            proposed = min(high, max(low, base_value + delta))
            rounded = _round_value(key, proposed)
            if rounded == current.get(key, DEFAULTS[key]):
                continue
            overrides = dict(current)
            overrides[key] = rounded
            if fixed_update_window is not None:
                overrides["update_window_seconds"] = fixed_update_window
            encoded = json.dumps(overrides, sort_keys=True)
            if encoded in seen:
                continue
            seen.add(encoded)
            neighbors.append(Candidate(name=_candidate_name("local", overrides), overrides=overrides))
    return neighbors


def _load_runtime_defaults(config_path: Path) -> dict[str, Any]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        raise SystemExit(f"Invalid runtime section in {config_path}")
    return dict(runtime)


def _evaluate_candidates(
    *,
    candidates: list[Candidate],
    scenarios: list[Path],
    base_config: Path,
    data_dir: Path,
    out_root: Path,
    workers: int,
    include_soft: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates, start=1):
        run_dir, summary = _run_suite(
            candidate=candidate,
            scenarios=scenarios,
            base_config=base_config,
            data_dir=data_dir,
            out_root=out_root,
            workers=workers,
        )
        score = _score_summary(summary, include_soft=include_soft)
        results.append(
            {
                "candidate": candidate,
                "run_dir": str(run_dir),
                "summary": summary,
                "score": score,
                "stage": "full" if include_soft else "trend",
            }
        )
        print(f"[{idx}/{len(candidates)}] {candidate.name} score={score:.4f}", flush=True)
    results.sort(key=lambda item: float(item["score"]))
    return results


def _print_top(title: str, results: list[dict[str, Any]], top_n: int) -> None:
    print(f"\n== {title} ==", flush=True)
    for item in results[:top_n]:
        candidate = item["candidate"]
        summary = item["summary"]
        trend_up = summary.get("trend_up", {})
        trend_down = summary.get("trend_down", {})
        print(
            f"{candidate.name} score={item['score']:.4f} "
            f"up(p95_nn={trend_up.get('p95_abs_y_raw_non_neutral', 0):.3f}, "
            f"flip_u={trend_up.get('flip_per_update_non_neutral', 0):.3f}, "
            f"hold={trend_up.get('post_escape_hold_rate', 0):.2f}) "
            f"down(p95_nn={trend_down.get('p95_abs_y_raw_non_neutral', 0):.3f}, "
            f"flip_u={trend_down.get('flip_per_update_non_neutral', 0):.3f}, "
            f"hold={trend_down.get('post_escape_hold_rate', 0):.2f})"
        , flush=True)


def main() -> None:
    global DEFAULTS
    global SCORING_PARAMS

    args = _parse_args()
    rng = random.Random(args.seed)
    base_config = Path(args.config)
    data_dir = Path(args.data_dir)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    DEFAULTS = _load_runtime_defaults(base_config)
    SCORING_PARAMS = {
        "neutral_abs_y_raw": args.neutral_abs_y_raw,
        "sustained_escape_updates": args.sustained_escape_updates,
        "allow_update_window_search": args.allow_update_window_search,
        "update_window_min_seconds": args.update_window_min_seconds,
        "update_window_max_seconds": args.update_window_max_seconds,
        "fixed_update_window_seconds": (
            args.fixed_update_window_seconds
            if args.fixed_update_window_seconds is not None
            else (
                None if args.allow_update_window_search else DEFAULTS.get("update_window_seconds")
            )
        ),
        "search_default_update_window": min(
            max(
                float(DEFAULTS.get("update_window_seconds", args.update_window_min_seconds)),
                args.update_window_min_seconds,
            ),
            args.update_window_max_seconds,
        ),
    }
    all_scenarios = _scenario_paths()
    trend_scenarios = _filter_scenarios(all_scenarios, {"trend_up", "trend_down"})

    stage1_candidates = _seed_candidates(DEFAULTS, rng, args.stage1_count)
    stage1_results = _evaluate_candidates(
        candidates=stage1_candidates,
        scenarios=trend_scenarios,
        base_config=base_config,
        data_dir=data_dir,
        out_root=out_root,
        workers=args.workers,
        include_soft=False,
    )
    _print_top("Stage 1", stage1_results, args.stage1_top)

    local_candidates: list[Candidate] = []
    for item in stage1_results[: args.stage1_top]:
        local_candidates.extend(_local_neighbors(item["candidate"]))
    # De-duplicate and cap.
    deduped: list[Candidate] = []
    seen: set[str] = set()
    for candidate in local_candidates:
        encoded = json.dumps(candidate.overrides, sort_keys=True)
        if encoded in seen:
            continue
        seen.add(encoded)
        deduped.append(candidate)
        if len(deduped) >= args.stage2_local:
            break

    finalists = [item["candidate"] for item in stage1_results[: args.stage1_top]] + deduped
    final_results = _evaluate_candidates(
        candidates=finalists,
        scenarios=all_scenarios,
        base_config=base_config,
        data_dir=data_dir,
        out_root=out_root,
        workers=args.workers,
        include_soft=True,
    )
    _print_top("Final", final_results, 6)

    report_path = out_root / f"results-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = []
    for item in final_results:
        payload.append(
            {
                "name": item["candidate"].name,
                "overrides": item["candidate"].overrides,
                "score": item["score"],
                "run_dir": item["run_dir"],
                "summary": item["summary"],
            }
        )
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote ranked results to {report_path}", flush=True)


DEFAULTS: dict[str, Any] = {}
SCORING_PARAMS: dict[str, Any] = {}


if __name__ == "__main__":
    main()
