#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Iterable, cast

DISP_RATE_MULTIPLIERS = (0.5, 1.0)
X_MIN_THRESHOLD = 0.2
EPSILON = 1e-12
EFF_REL_ACTIVE_MULTIPLIER = 1.0
K_RECO_TARGETS = (0.6, 0.7, 0.8)

# Persistence-release diagnostics:
# - "quiet hold": A_eff stays quiet but S remains elevated
# - "pivot neutralization": mode enters pivot and S is pulled toward neutral
# Thresholds must be dt-safe and should track current persistence amplitude.
PERSIST_S_ELEVATED_ABS_MIN = 0.15
PERSIST_A_ACTIVE_ABS_FALLBACK = 0.10
PERSIST_A_QUIET_ABS_FALLBACK = 0.05
PERSIST_PIVOT_NEUTRAL_ZONE_ABS_FALLBACK = 0.08


def _config_float(config: dict[str, object] | None, key: str, fallback: float) -> float:
    if not config:
        return fallback
    value = config.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


@dataclass
class SeriesStats:
    values: list[float]

    def add(self, value: float) -> None:
        self.values.append(value)

    def count(self) -> int:
        return len(self.values)

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {}
        values = sorted(self.values)
        return {
            "min": values[0],
            "p10": _percentile(values, 0.10),
            "p50": _percentile(values, 0.50),
            "p90": _percentile(values, 0.90),
            "max": values[-1],
            "mean": mean(values),
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return values[0]
    if pct >= 1:
        return values[-1]
    idx = int(round(pct * (len(values) - 1)))
    return values[idx]


def _sign(value: float) -> int:
    if value > EPSILON:
        return 1
    if value < -EPSILON:
        return -1
    return 0


def _format_summary(label: str, stats: dict[str, float]) -> str:
    if not stats:
        return f"{label}: n/a"
    return (
        f"{label}: min={stats['min']:.6f} "
        f"p10={stats['p10']:.6f} p50={stats['p50']:.6f} "
        f"p90={stats['p90']:.6f} max={stats['max']:.6f} "
        f"mean={stats['mean']:.6f}"
    )


def _atanh(value: float) -> float:
    from math import log

    return 0.5 * log((1.0 + value) / (1.0 - value))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _load_records_and_events(path: Path) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    events: list[dict] = []
    try:
        if path.suffix == ".gz":
            handle = gzip.open(path, "rt", encoding="utf-8")
        else:
            handle = path.open("r", encoding="utf-8")
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict) and "_meta" in record:
                    continue
                if isinstance(record, dict) and "event_type" in record:
                    events.append(record)
                else:
                    records.append(record)
    except (EOFError, gzip.BadGzipFile, OSError) as exc:
        print(f"warning: skipping unreadable log {path} ({exc})", file=sys.stderr)
        return [], []
    return records, events


def _load_records(path: Path) -> list[dict]:
    records, _ = _load_records_and_events(path)
    return records


def _event_counts_by_symbol(events: Iterable[dict]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        symbol = str(event.get("symbol", "")).upper()
        if not symbol:
            continue
        event_type = str(event.get("event_type", ""))
        if not event_type:
            continue
        counts[symbol][event_type] += 1
    return counts


def _load_config_from_log(path: Path) -> dict[str, object]:
    if path.suffix == ".gz":
        handle = gzip.open(path, "rt", encoding="utf-8")
    else:
        handle = path.open("r", encoding="utf-8")
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            meta = record.get("_meta")
            if not isinstance(meta, dict):
                continue
            if meta.get("type") != "config":
                continue
            config = meta.get("config")
            if isinstance(config, dict):
                return config
    return {}


def _load_replay_meta(path: Path) -> dict[str, object]:
    if path.suffix == ".gz":
        handle = gzip.open(path, "rt", encoding="utf-8")
    else:
        handle = path.open("r", encoding="utf-8")
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            meta = record.get("_meta")
            if not isinstance(meta, dict):
                continue
            if meta.get("type") != "config":
                continue
            replay = meta.get("replay")
            if isinstance(replay, dict):
                return replay
    return {}


def _load_config_summary(path: Path) -> dict[str, object]:
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    runtime: dict[str, object] = {}
    in_runtime = False
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_runtime = line == "[runtime]"
            continue
        if not in_runtime:
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        runtime[key.strip()] = value.strip()
    ordered_keys = [
        "update_window_seconds",
        "tbt_window_multiplier",
        "price_selector_policy",
        "price_selector_stale_failover_ms",
        "price_selector_recovery_confirm_cycles",
        "price_selector_switch_cooldown_cycles",
        "tanh_k",
        "scale_window_seconds",
        "persist_enabled",
        "persist_input",
        "persist_input_deadband",
        "persist_neutral_dir_abs_flash",
        "persist_neutral_dir_abs_persist",
        "persist_tau_eff_active",
        "persist_tau_dir_active",
        "persist_pivot_active_abs",
        "persist_pivot_confirm_s",
        "persist_pivot_neutralize_tau",
        "persist_pivot_neutral_zone_abs",
        "persist_rebuild_confirm_s",
        "persist_pivot_cooldown_s",
        "persist_pivot_max_s",
        "persist_max_delta_s_eff_per_second",
        "persist_tau_dir_pivot",
        "persist_dormant_quiet_abs",
        "persist_dormant_active_abs",
        "persist_dormant_quiet_s",
        "persist_tau_dormant",
        "persist_dormant_effort_norm_threshold",
        "disp_scale_multiplier",
        "disp_scale_percentile",
        "disp_scale_min_samples",
        "disp_scale_floor_percentile",
        "effort_scale_percentile",
        "effort_scale_min_samples",
        "size_scale_percentile",
        "effort_floor_multiplier",
        "effort_floor_ticks",
        "smoothing_dominance_alpha",
        "smoothing_effectiveness_alpha",
        "dispersion_metric",
        "halo_growth_rate",
        "halo_decay_rate",
        "binning_dot_size_thresholds",
        "binning_halo_thresholds",
        "binning_hysteresis_band",
        "tui_min_width",
        "tui_min_height",
        "tui_max_width",
        "tui_max_height",
        "tui_dot_radii",
        "tui_halo_radii",
        "tui_frame_enabled",
        "tui_frame_inset_px",
        "tui_frame_band_inner",
        "tui_frame_band_outer",
    ]
    return {key: runtime[key] for key in ordered_keys if key in runtime}


def _load_scenario_manifest(path: Path) -> dict[str, tuple[float, float]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        return {}
    manifest_pre_roll = float(payload.get("pre_roll_ms", 0.0))
    out: dict[str, tuple[float, float]] = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_id = str(scenario.get("id", ""))
        if not scenario_id:
            continue
        start_ms = float(scenario.get("start_ms", 0.0))
        end_ms = float(scenario.get("end_ms", 0.0))
        pre_roll_ms = float(scenario.get("pre_roll_ms", manifest_pre_roll))
        duration_s = max(0.0, (end_ms - start_ms) / 1000.0)
        pre_roll_s = max(0.0, pre_roll_ms / 1000.0)
        out[scenario_id] = (duration_s, pre_roll_s)
    return out


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_float_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in value.items():
        if isinstance(key, str):
            out[key] = _as_float(val)
    return out


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _parse_replay_label(path: Path) -> tuple[str, str, str | None]:
    name = path.stem
    if name.endswith(".jsonl"):
        name = Path(name).stem
    if name.startswith("flow_lens_replay-"):
        parts = name.split("-")
        if len(parts) >= 5:
            symbol = parts[1]
            regime = parts[2]
            scenario_id = parts[3]
            return symbol.upper(), regime, scenario_id
        if len(parts) >= 3:
            symbol = parts[1]
            regime = parts[2]
            return symbol.upper(), regime, None
    return "UNKNOWN", "unknown", None


def _write_summary(
    *,
    paths: list[Path],
    out_path: Path,
    config_values: dict[str, object],
    scenario_info: dict[str, tuple[float, float]],
) -> None:
    aggregated: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    runs: dict[tuple[str, str], int] = defaultdict(int)
    record_counts: dict[tuple[str, str], int] = defaultdict(int)
    scenario_durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    pre_roll_lengths: dict[tuple[str, str], list[float]] = defaultdict(list)

    for path in paths:
        records, events = _load_records_and_events(path)
        grouped = _iter_symbols(records, None)
        event_counts = _event_counts_by_symbol(events)
        symbol_label, regime_label, scenario_id = _parse_replay_label(path)
        replay_meta = _load_replay_meta(path)
        for symbol, entries in grouped.items():
            stats = _stats_for_symbol(
                entries,
                config=config_values,
                event_counts=event_counts,
            )
            label_symbol = symbol_label if symbol_label != "UNKNOWN" else symbol
            key = (label_symbol, regime_label)
            runs[key] += 1
            record_counts[key] += _as_int(stats.get("records"))
            flip_rates = _as_float_map(stats.get("sign_flip_rate_per_min"))
            aggregated[key]["y_raw_abs_p95"].append(_as_float(stats.get("y_raw_abs_p95")))
            aggregated[key]["y_raw_abs_p99"].append(_as_float(stats.get("y_raw_abs_p99")))
            aggregated[key]["flip_rate_y_raw"].append(_as_float(flip_rates.get("Y_raw")))
            aggregated[key]["flip_rate_y"].append(_as_float(flip_rates.get("Y")))
            aggregated[key]["deadband_active_rate"].append(
                _as_float(stats.get("disp_deadband_active_rate"))
            )
            aggregated[key]["disp_ratio_p50"].append(_as_float(stats.get("disp_ratio_p50")))
            aggregated[key]["e_dir_persistence_p50"].append(
                _as_float(stats.get("e_dir_persistence_p50"))
            )
            aggregated[key]["price_series_switch_rate"].append(
                _as_float(stats.get("price_series_switch_rate_per_min"))
            )
            aggregated[key]["price_series_base_switch_rate"].append(
                _as_float(stats.get("price_series_base_switch_rate_per_min"))
            )
            aggregated[key]["price_series_unavailable_rate"].append(
                _as_float(stats.get("price_series_unavailable_per_min"))
            )
            aggregated[key]["air_pocket_active_rate"].append(
                _as_float(stats.get("air_pocket_active_rate"))
            )
            aggregated[key]["y_raw_saturation_rate"].append(
                _as_float(stats.get("y_raw_saturation_rate"))
            )
            aggregated[key]["y_raw_disp_dir_mismatch_rate"].append(
                _as_float(stats.get("y_raw_disp_dir_mismatch_rate"))
            )
            mode_fracs = _as_float_map(stats.get("persist_update_mode_fractions"))
            aggregated[key]["persist_mode_active_frac"].append(_as_float(mode_fracs.get("active")))
            aggregated[key]["persist_mode_pivot_frac"].append(_as_float(mode_fracs.get("pivot")))
            aggregated[key]["persist_mode_dormant_frac"].append(
                _as_float(mode_fracs.get("dormant"))
            )
            aggregated[key]["persist_activity_rate"].append(
                _as_float(stats.get("persist_activity_rate"))
            )
            aggregated[key]["persist_abs_p95"].append(_as_float(stats.get("persist_abs_p95")))
            aggregated[key]["persist_abs_p99"].append(_as_float(stats.get("persist_abs_p99")))
            aggregated[key]["persist_abs_max"].append(_as_float(stats.get("persist_abs_max")))
            aggregated[key]["persist_dt_p50_s"].append(_as_float(stats.get("persist_dt_p50_s")))
            aggregated[key]["persist_dt_p90_s"].append(_as_float(stats.get("persist_dt_p90_s")))
            aggregated[key]["persist_stale_hold_max_s"].append(
                _as_float(stats.get("persist_stale_hold_max_s"))
            )
            aggregated[key]["persist_stale_hold_p95_s"].append(
                _as_float(stats.get("persist_stale_hold_p95_s"))
            )
            aggregated[key]["persist_pivot_half_life_p50_s"].append(
                _as_float(stats.get("persist_pivot_half_life_p50_s"))
            )
            aggregated[key]["persist_pivot_to_neutral_p50_s"].append(
                _as_float(stats.get("persist_pivot_to_neutral_p50_s"))
            )
            aggregated[key]["persist_pivot_to_neutral_rate"].append(
                _as_float(stats.get("persist_pivot_to_neutral_rate"))
            )
            aggregated[key]["persist_quiet_release_half_life_p50_s"].append(
                _as_float(stats.get("persist_quiet_release_half_life_p50_s"))
            )
            aggregated[key]["gate_low_rate"].append(_as_float(stats.get("gate_low_rate")))
            aggregated[key]["x_raw_mean"].append(_as_float(stats.get("x_raw_mean")))
            aggregated[key]["x_mean"].append(_as_float(stats.get("x_mean")))
            aggregated[key]["eff_rel_abs_p95_all"].append(
                _as_float(stats.get("eff_rel_abs_p95_all"))
            )
            aggregated[key]["eff_rel_abs_p95_active"].append(
                _as_float(stats.get("eff_rel_abs_p95_active"))
            )
            aggregated[key]["k_reco_target_0.6"].append(
                _as_float(stats.get("k_reco_target_0.6"))
            )
            aggregated[key]["k_reco_target_0.7"].append(
                _as_float(stats.get("k_reco_target_0.7"))
            )
            aggregated[key]["k_reco_target_0.8"].append(
                _as_float(stats.get("k_reco_target_0.8"))
            )

            duration_s: float | None = None
            pre_roll_s: float | None = None
            if scenario_id and scenario_id in scenario_info:
                duration_s, pre_roll_s = scenario_info[scenario_id]
            else:
                scenario_start = _as_float(replay_meta.get("scenario_start_ms"))
                scenario_end = _as_float(replay_meta.get("scenario_end_ms"))
                pre_roll_ms = _as_float(replay_meta.get("pre_roll_ms"))
                if scenario_start > 0 and scenario_end > scenario_start:
                    duration_s = (scenario_end - scenario_start) / 1000.0
                if pre_roll_ms > 0:
                    pre_roll_s = pre_roll_ms / 1000.0
            if duration_s and duration_s > 0:
                scenario_durations[key].append(duration_s)
            if pre_roll_s and pre_roll_s > 0:
                pre_roll_lengths[key].append(pre_roll_s)

    with out_path.open("w", encoding="utf-8") as handle:
        handle.write(f"source_dir: {paths[0].parent if paths else ''}\n")
        handle.write(f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if config_values:
            handle.write("config:\n")
            for key, value in config_values.items():
                handle.write(f"  {key}: {value}\n")
        handle.write(f"eff_rel_active_multiplier: {EFF_REL_ACTIVE_MULTIPLIER:.2f}\n")
        handle.write("\n== summary ==\n")
        for key in sorted(aggregated.keys()):
            symbol, regime = key
            duration_s = _median(scenario_durations.get(key, []))
            pre_roll_s = _median(pre_roll_lengths.get(key, []))
            duration_text = f"{duration_s:.0f}s" if duration_s > 0 else "n/a"
            pre_roll_text = f"{pre_roll_s:.0f}s" if pre_roll_s > 0 else "n/a"
            handle.write(
                f"\n{symbol} {regime} (runs={runs[key]}, records={record_counts[key]})\n"
            )
            handle.write(f"scenario_duration {duration_text}  pre_roll {pre_roll_text}\n")
            handle.write(
                "p95|Y_raw| "
                f"{_median(aggregated[key]['y_raw_abs_p95']):.3f}  "
                "p99|Y_raw| "
                f"{_median(aggregated[key]['y_raw_abs_p99']):.3f}  "
                "Y_raw_sat "
                f"{_median(aggregated[key]['y_raw_saturation_rate']):.2f}  "
                "Flip Y_raw "
                f"{_median(aggregated[key]['flip_rate_y_raw']):.2f}/m  "
                "Y "
                f"{_median(aggregated[key]['flip_rate_y']):.2f}/m  "
                "Deadband "
                f"{_median(aggregated[key]['deadband_active_rate']):.2f}  "
                "Gate low "
                f"{_median(aggregated[key]['gate_low_rate']):.2f}  "
                "Y_raw_dir_mismatch "
                f"{_median(aggregated[key]['y_raw_disp_dir_mismatch_rate']):.2f}\n"
            )
            handle.write(
                "p95|S| "
                f"{_median(aggregated[key]['persist_abs_p95']):.3f}  "
                "p99|S| "
                f"{_median(aggregated[key]['persist_abs_p99']):.3f}  "
                "max|S| "
                f"{_median(aggregated[key]['persist_abs_max']):.3f}  "
                "Mode a/p/d "
                f"{_median(aggregated[key]['persist_mode_active_frac']):.2f}/"
                f"{_median(aggregated[key]['persist_mode_pivot_frac']):.2f}/"
                f"{_median(aggregated[key]['persist_mode_dormant_frac']):.2f}  "
                "Act "
                f"{_median(aggregated[key]['persist_activity_rate']):.2f}  "
                "dt_p50/p90 "
                f"{_median(aggregated[key]['persist_dt_p50_s']):.2f}/"
                f"{_median(aggregated[key]['persist_dt_p90_s']):.2f}s\n"
            )
            handle.write(
                "eff_rel_p95_all "
                f"{_median(aggregated[key]['eff_rel_abs_p95_all']):.3f}  "
                "eff_rel_p95_active "
                f"{_median(aggregated[key]['eff_rel_abs_p95_active']):.3f}  "
                "k_reco_0.6 "
                f"{_median(aggregated[key]['k_reco_target_0.6']):.3f}  "
                "k_reco_0.7 "
                f"{_median(aggregated[key]['k_reco_target_0.7']):.3f}  "
                "k_reco_0.8 "
                f"{_median(aggregated[key]['k_reco_target_0.8']):.3f}\n"
            )
            handle.write(
                "|disp|/scale "
                f"{_median(aggregated[key]['disp_ratio_p50']):.2f}  "
                "E_dir persist "
                f"{_median(aggregated[key]['e_dir_persistence_p50']):.0f}  "
                "Series switch "
                f"{_median(aggregated[key]['price_series_switch_rate']):.2f}/m  "
                "Series unavailable "
                f"{_median(aggregated[key]['price_series_unavailable_rate']):.2f}/m  "
                "Spot/perp switch "
                f"{_median(aggregated[key]['price_series_base_switch_rate']):.2f}/m  "
                "Air pocket "
                f"{_median(aggregated[key]['air_pocket_active_rate']):.2f}  "
                "S_hold_max/p95 "
                f"{_median(aggregated[key]['persist_stale_hold_max_s']):.0f}/"
                f"{_median(aggregated[key]['persist_stale_hold_p95_s']):.0f}s  "
                "Pivot_to0_p50 "
                f"{_median(aggregated[key]['persist_pivot_to_neutral_p50_s']):.0f}s  "
                "Pivot_half_p50 "
                f"{_median(aggregated[key]['persist_pivot_half_life_p50_s']):.0f}s  "
                "Pivot_to0_hit "
                f"{_median(aggregated[key]['persist_pivot_to_neutral_rate']):.2f}  "
                "Quiet_half_p50 "
                f"{_median(aggregated[key]['persist_quiet_release_half_life_p50_s']):.0f}s  "
                "X_raw mean "
                f"{_median(aggregated[key]['x_raw_mean']):.2f}\n"
            )
            handle.write(
                "X mean "
                f"{_median(aggregated[key]['x_mean']):.2f}\n"
            )


def _iter_symbols(records: Iterable[dict], symbols: set[str] | None) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        symbol = str(record.get("symbol", "")).upper()
        if not symbol:
            continue
        if symbols and symbol not in symbols:
            continue
        grouped[symbol].append(record)
    return grouped


def _stats_for_symbol(
    records: list[dict],
    *,
    config: dict[str, object] | None = None,
    event_counts: dict[str, Counter[str]] | None = None,
) -> dict[str, object]:
    records.sort(key=lambda record: int(record.get("now_ms", 0)))
    out: dict[str, object] = {}
    out["records"] = len(records)

    pivot_neutral_zone_abs = _config_float(
        config,
        "persist_pivot_neutral_zone_abs",
        PERSIST_PIVOT_NEUTRAL_ZONE_ABS_FALLBACK,
    )
    pivot_active_abs = _config_float(
        config,
        "persist_pivot_active_abs",
        PERSIST_A_ACTIVE_ABS_FALLBACK,
    )
    dormant_quiet_abs = _config_float(
        config,
        "persist_dormant_quiet_abs",
        PERSIST_A_QUIET_ABS_FALLBACK,
    )
    # Define "elevated S" in a way that adapts to the configured neutral zone.
    s_elevated_abs = max(PERSIST_S_ELEVATED_ABS_MIN, 2.0 * pivot_neutral_zone_abs)

    now_ms_stats = SeriesStats([])
    window_stats = SeriesStats([])
    log_return_stats = SeriesStats([])
    disp_stats = SeriesStats([])
    disp_rate_stats = SeriesStats([])
    eff_raw_stats = SeriesStats([])
    gate_stats = SeriesStats([])
    y_raw_stats = SeriesStats([])
    y_stats = SeriesStats([])
    persist_raw_stats = SeriesStats([])
    persist_slope_stats = SeriesStats([])
    persist_sign_stats = SeriesStats([])
    persist_input_value_stats = SeriesStats([])
    persist_dt_s_stats = SeriesStats([])
    persist_gain_per_second_stats = SeriesStats([])
    persist_input_deadband_stats = SeriesStats([])
    persist_step_coeff_stats = SeriesStats([])
    x_raw_stats = SeriesStats([])
    x_stats = SeriesStats([])
    size_raw_stats = SeriesStats([])
    size_effort_norm_stats = SeriesStats([])
    size_scale_stats = SeriesStats([])
    e_total_stats = SeriesStats([])
    e_rate_stats = SeriesStats([])
    e_spot_share_stats = SeriesStats([])
    price_delta_stats = SeriesStats([])
    effort_norm_stats = SeriesStats([])
    effort_floor_stats = SeriesStats([])
    disp_scale_stats = SeriesStats([])
    e_scale_stats = SeriesStats([])
    halo_stats = SeriesStats([])
    halo_raw_stats = SeriesStats([])
    source_count_stats = SeriesStats([])
    max_source_share_stats = SeriesStats([])
    tanh_k_stats = SeriesStats([])
    e_dir_stats = SeriesStats([])
    e_dir_sign_stats = SeriesStats([])
    disp_deadband_active = 0
    eff_rel_abs_all: list[float] = []
    eff_rel_abs_active: list[float] = []
    disp_ratio_values: list[float] = []
    e_dir_persist_runs: list[int] = []

    price_series_counts: Counter[str] = Counter()
    top_source_counts: Counter[str] = Counter()
    size_bins: Counter[int] = Counter()
    persist_input_counts: Counter[str] = Counter()
    persist_update_mode_counts: Counter[str] = Counter()

    spot_fresh = 0
    perp_fresh = 0
    spot_events = SeriesStats([])
    perp_events = SeriesStats([])
    y_near_zero = 0
    y_raw_near_zero = 0
    y_raw_saturated = 0
    gate_low = 0
    perp_dominant_spot_missing = 0
    y_raw_disp_mismatch = 0
    y_raw_disp_mismatch_total = 0
    y_raw_disp_dir_mismatch = 0
    y_raw_disp_dir_mismatch_total = 0

    persist_activity_true = 0

    stale_hold_run_s = 0.0
    stale_hold_runs_s: list[float] = []
    stale_hold_max_s = 0.0

    pivot_run_active = False
    pivot_run_elapsed_s = 0.0
    pivot_run_start_abs: float | None = None
    pivot_run_starts = 0
    pivot_run_half_found = False
    pivot_half_life_s: list[float] = []
    pivot_to_neutral_s: list[float] = []

    quiet_release_active = False
    quiet_release_elapsed_s = 0.0
    quiet_release_start_abs: float | None = None
    quiet_release_starts = 0
    quiet_release_half_found = False
    quiet_release_half_life_s: list[float] = []

    last_now_ms: int | None = None
    tick_intervals: list[float] = []
    total_duration_s = 0.0

    flip_counts = {"Y_raw": 0, "Y": 0, "X_raw": 0, "X": 0}
    flip_counts_disp: dict[float, dict[str, int]] = {
        m: {"Y_raw": 0, "Y": 0} for m in DISP_RATE_MULTIPLIERS
    }
    disp_durations: dict[float, float] = {m: 0.0 for m in DISP_RATE_MULTIPLIERS}
    flip_counts_dom = {"Y_raw": 0, "Y": 0}
    flip_counts_neutral = {"Y_raw": 0, "Y": 0}
    dom_duration_s = 0.0
    neutral_duration_s = 0.0

    last_nonzero_signs = {"Y_raw": 0, "Y": 0, "X_raw": 0, "X": 0}
    last_nonzero_signs_disp: dict[float, dict[str, int]] = {
        m: {"Y_raw": 0, "Y": 0} for m in DISP_RATE_MULTIPLIERS
    }
    in_disp_regime: dict[float, bool] = {m: False for m in DISP_RATE_MULTIPLIERS}
    last_nonzero_signs_dom = {"Y_raw": 0, "Y": 0}
    last_nonzero_signs_neutral = {"Y_raw": 0, "Y": 0}
    in_dom_regime = False
    in_neutral_regime = False
    prev_series: str | None = None
    series_switches = 0
    prev_base_series: str | None = None
    base_series_switches = 0
    last_e_dir_sign = 0
    e_dir_run = 0
    prev_persist_s: float | None = None

    for record in records:
        dt_s = 0.0
        now_ms = int(record.get("now_ms", 0))
        if last_now_ms is not None and now_ms > last_now_ms:
            tick_intervals.append((now_ms - last_now_ms) / 1000.0)
            dt_s = (now_ms - last_now_ms) / 1000.0
            total_duration_s += dt_s
        last_now_ms = now_ms

        series = str(record.get("price_series_used", "unknown"))
        if prev_series is not None and series != prev_series:
            series_switches += 1
        prev_series = series
        if series.startswith("spot"):
            base_series = "spot"
        elif series.startswith("perp"):
            base_series = "perp"
        else:
            base_series = series
        if prev_base_series is not None and base_series != prev_base_series:
            base_series_switches += 1
        prev_base_series = base_series

        now_ms_stats.add(float(now_ms))
        window_stats.add(float(record.get("window_ms", 0)))
        log_return_stats.add(float(record.get("log_return", 0.0)))
        disp_stats.add(float(record.get("disp", 0.0)))
        disp_rate_stats.add(float(record.get("disp_rate", 0.0)))
        eff_raw_stats.add(float(record.get("eff_raw", 0.0)))
        gate_stats.add(float(record.get("gate", 0.0)))
        tanh_k_stats.add(float(record.get("tanh_k", 0.0)))
        y_raw_stats.add(float(record.get("Y_raw", 0.0)))
        y_stats.add(float(record.get("Y", 0.0)))
        persist_s_value = float(record.get("persist_raw", 0.0))
        persist_raw_stats.add(persist_s_value)
        persist_slope_stats.add(float(record.get("persist_slope", 0.0)))
        persist_sign_stats.add(float(record.get("persist_sign", 0.0)))
        persist_input = str(record.get("persist_input", "unknown")) or "unknown"
        persist_input_counts[persist_input] += 1
        raw_input_value = record.get("persist_input_value")
        if raw_input_value is None:
            if persist_input in {"Y_raw", "y_raw"}:
                persist_input_value = float(record.get("Y_raw", 0.0))
            elif persist_input in {"Y_gated", "y_gated"}:
                persist_input_value = float(record.get("Y_gated", 0.0))
            elif persist_input in {"Y", "y"}:
                persist_input_value = float(record.get("Y", 0.0))
            else:
                persist_input_value = 0.0
        else:
            persist_input_value = float(raw_input_value)
        persist_input_value_stats.add(persist_input_value)
        persist_dt_s_value_raw = record.get("persist_dt_s")
        if isinstance(persist_dt_s_value_raw, (int, float)):
            persist_dt_s_value = float(persist_dt_s_value_raw)
        else:
            persist_dt_s_value = dt_s
        if persist_dt_s_value > 0:
            persist_dt_s_stats.add(persist_dt_s_value)
        persist_gain_per_second_stats.add(float(record.get("persist_gain_per_second", 0.0)))
        persist_input_deadband_stats.add(float(record.get("persist_input_deadband", 0.0)))
        persist_step_coeff_stats.add(float(record.get("persist_step_coeff", 0.0)))
        persist_update_mode = str(record.get("persist_update_mode", "unknown")) or "unknown"
        persist_update_mode_counts[persist_update_mode] += 1
        persist_activity_raw = record.get("persist_activity_flag", False)
        persist_activity_flag = bool(persist_activity_raw)
        if isinstance(persist_activity_raw, (int, float)):
            persist_activity_flag = bool(int(persist_activity_raw))
        if persist_activity_flag:
            persist_activity_true += 1

        if abs(persist_input_value) <= dormant_quiet_abs and abs(persist_s_value) >= s_elevated_abs:
            stale_hold_run_s += persist_dt_s_value
        else:
            if stale_hold_run_s > 0:
                stale_hold_runs_s.append(stale_hold_run_s)
                stale_hold_max_s = max(stale_hold_max_s, stale_hold_run_s)
                stale_hold_run_s = 0.0

        # Pivot neutralization is the canonical “opposition unwind” in Experiment B.
        # Measure pivots that start meaningfully away from neutral.
        if (
            persist_update_mode == "pivot"
            and prev_persist_s is not None
            and abs(prev_persist_s) > pivot_neutral_zone_abs
        ):
            if not pivot_run_active:
                pivot_run_active = True
                pivot_run_elapsed_s = 0.0
                pivot_run_start_abs = abs(prev_persist_s)
                pivot_run_starts += 1
                pivot_run_half_found = False

            pivot_run_elapsed_s += persist_dt_s_value

            if (
                pivot_run_start_abs is not None
                and pivot_run_start_abs >= s_elevated_abs
                and not pivot_run_half_found
                and abs(persist_s_value) <= 0.5 * pivot_run_start_abs
            ):
                pivot_half_life_s.append(pivot_run_elapsed_s)
                pivot_run_half_found = True

            if abs(persist_s_value) <= pivot_neutral_zone_abs:
                pivot_to_neutral_s.append(pivot_run_elapsed_s)
                pivot_run_active = False
                pivot_run_elapsed_s = 0.0
                pivot_run_start_abs = None
                pivot_run_half_found = False
        else:
            if pivot_run_active:
                pivot_run_active = False
                pivot_run_elapsed_s = 0.0
                pivot_run_start_abs = None
                pivot_run_half_found = False

        # Quiet release: S should not overstay after support disappears (A_eff goes quiet).
        if (
            persist_update_mode == "active"
            and abs(persist_input_value) <= dormant_quiet_abs
            and prev_persist_s is not None
            and abs(prev_persist_s) >= s_elevated_abs
        ):
            if not quiet_release_active:
                quiet_release_active = True
                quiet_release_elapsed_s = 0.0
                quiet_release_start_abs = abs(prev_persist_s)
                quiet_release_starts += 1
                quiet_release_half_found = False

            quiet_release_elapsed_s += persist_dt_s_value

            if quiet_release_start_abs is not None and quiet_release_start_abs > 0:
                if (
                    not quiet_release_half_found
                    and abs(persist_s_value) <= 0.5 * quiet_release_start_abs
                ):
                    quiet_release_half_life_s.append(quiet_release_elapsed_s)
                    quiet_release_half_found = True
        else:
            if quiet_release_active:
                quiet_release_active = False
                quiet_release_elapsed_s = 0.0
                quiet_release_start_abs = None
                quiet_release_half_found = False
        prev_persist_s = persist_s_value
        x_raw_stats.add(float(record.get("X_raw", 0.0)))
        x_stats.add(float(record.get("X", 0.0)))
        size_raw_stats.add(float(record.get("size_raw", 0.0)))
        size_effort_norm_stats.add(float(record.get("size_effort_norm", 0.0)))
        size_scale_stats.add(float(record.get("size_scale", 0.0)))
        size_bins[int(record.get("size_bin", 0))] += 1
        e_total_stats.add(float(record.get("E_total", 0.0)))
        e_rate_stats.add(float(record.get("E_rate", 0.0)))
        e_spot_share_stats.add(float(record.get("E_spot_share", 0.0)))
        e_dir_stats.add(float(record.get("E_dir", 0.0)))
        e_dir_sign_stats.add(float(record.get("E_dir_sign", 0.0)))
        if record.get("disp_deadband_active"):
            disp_deadband_active += 1
        disp_rate_value = float(record.get("disp_rate", 0.0))
        disp_scale_value = float(record.get("disp_scale", 0.0))
        effort_rate_value = float(record.get("E_rate", 0.0))
        effort_scale_value = float(record.get("E_scale", 0.0))
        eff_rel = (disp_rate_value * effort_scale_value) / (
            effort_rate_value * disp_scale_value + EPSILON
        )
        eff_rel_abs = abs(eff_rel)
        eff_rel_abs_all.append(eff_rel_abs)
        if abs(disp_rate_value) > disp_scale_value * EFF_REL_ACTIVE_MULTIPLIER:
            eff_rel_abs_active.append(eff_rel_abs)
        if disp_scale_value > EPSILON:
            disp_ratio_values.append(abs(disp_rate_value) / disp_scale_value)
        price_delta_stats.add(float(record.get("delta_price", 0.0)))
        effort_norm_stats.add(float(record.get("effort_norm", 0.0)))
        effort_floor_stats.add(float(record.get("effort_floor", 0.0)))
        disp_scale_stats.add(float(record.get("disp_scale", 0.0)))
        e_scale_stats.add(float(record.get("E_scale", 0.0)))
        halo_stats.add(float(record.get("halo", 0.0)))
        halo_raw_stats.add(float(record.get("halo_raw", 0.0)))
        source_count_stats.add(float(record.get("source_count_active", 0)))
        max_source_share_stats.add(float(record.get("max_source_share", 0.0)))

        price_series_counts[str(record.get("price_series_used", "unknown"))] += 1
        top_source_id = record.get("top_source_id")
        if top_source_id:
            top_source_counts[str(top_source_id)] += 1

        if record.get("spot_fresh"):
            spot_fresh += 1
        if record.get("perp_fresh"):
            perp_fresh += 1

        spot_events.add(float(record.get("spot_event_count_window", 0)))
        perp_events.add(float(record.get("perp_event_count_window", 0)))

        if abs(float(record.get("Y", 0.0))) < 0.01:
            y_near_zero += 1
        if abs(float(record.get("Y_raw", 0.0))) < 0.01:
            y_raw_near_zero += 1
        if abs(float(record.get("Y_raw", 0.0))) > 0.9:
            y_raw_saturated += 1
        if float(record.get("gate", 0.0)) < 0.2:
            gate_low += 1

        if (
            float(record.get("E_spot_share", 0.5)) < 0.25
            and float(record.get("spot_event_count_window", 0)) == 0
        ):
            perp_dominant_spot_missing += 1

        y_raw_sign = _sign(float(record.get("Y_raw", 0.0)))
        y_sign = _sign(float(record.get("Y", 0.0)))
        x_raw_sign = _sign(float(record.get("X_raw", 0.0)))
        x_sign = _sign(float(record.get("X", 0.0)))
        disp_rate = disp_rate_value
        disp_rate_sign = _sign(disp_rate)
        e_dir_sign_value = record.get("E_dir_sign")
        if e_dir_sign_value is None:
            e_dir_sign_value = float(record.get("E_dir", 0.0))
        e_dir_sign = _sign(float(e_dir_sign_value))
        disp_rate_dir_sign = _sign(disp_rate * e_dir_sign)

        if e_dir_sign == 0:
            if e_dir_run > 0:
                e_dir_persist_runs.append(e_dir_run)
            e_dir_run = 0
        elif e_dir_sign == last_e_dir_sign:
            e_dir_run += 1
        else:
            if e_dir_run > 0:
                e_dir_persist_runs.append(e_dir_run)
            e_dir_run = 1
        last_e_dir_sign = e_dir_sign

        if y_raw_sign != 0 and disp_rate_sign != 0:
            y_raw_disp_mismatch_total += 1
            if y_raw_sign != disp_rate_sign:
                y_raw_disp_mismatch += 1
        if y_raw_sign != 0 and disp_rate_dir_sign != 0:
            y_raw_disp_dir_mismatch_total += 1
            if y_raw_sign != disp_rate_dir_sign:
                y_raw_disp_dir_mismatch += 1

        for key, current_sign in (
            ("Y_raw", y_raw_sign),
            ("Y", y_sign),
            ("X_raw", x_raw_sign),
            ("X", x_sign),
        ):
            if current_sign == 0:
                continue
            prior_sign = last_nonzero_signs[key]
            if prior_sign != 0 and prior_sign != current_sign:
                flip_counts[key] += 1
            last_nonzero_signs[key] = current_sign

        for multiplier in DISP_RATE_MULTIPLIERS:
            threshold = float(record.get("disp_scale", 0.0)) * multiplier
            in_regime = threshold > EPSILON and abs(disp_rate) > threshold
            if not in_regime:
                in_disp_regime[multiplier] = False
                last_nonzero_signs_disp[multiplier] = {"Y_raw": 0, "Y": 0}
                continue

            disp_durations[multiplier] += dt_s
            if not in_disp_regime[multiplier]:
                in_disp_regime[multiplier] = True
                continue

            for key, current_sign in (("Y_raw", y_raw_sign), ("Y", y_sign)):
                if current_sign == 0:
                    continue
                prior_sign = last_nonzero_signs_disp[multiplier][key]
                if prior_sign != 0 and prior_sign != current_sign:
                    flip_counts_disp[multiplier][key] += 1
                last_nonzero_signs_disp[multiplier][key] = current_sign

        in_dominant = abs(float(record.get("X_raw", 0.0))) > X_MIN_THRESHOLD
        if in_dominant:
            dom_duration_s += dt_s
            in_neutral_regime = False
            last_nonzero_signs_neutral = {"Y_raw": 0, "Y": 0}
            if not in_dom_regime:
                in_dom_regime = True
                last_nonzero_signs_dom = {"Y_raw": 0, "Y": 0}
                continue

            for key, current_sign in (("Y_raw", y_raw_sign), ("Y", y_sign)):
                if current_sign == 0:
                    continue
                prior_sign = last_nonzero_signs_dom[key]
                if prior_sign != 0 and prior_sign != current_sign:
                    flip_counts_dom[key] += 1
                last_nonzero_signs_dom[key] = current_sign
        else:
            neutral_duration_s += dt_s
            in_dom_regime = False
            last_nonzero_signs_dom = {"Y_raw": 0, "Y": 0}
            if not in_neutral_regime:
                in_neutral_regime = True
                last_nonzero_signs_neutral = {"Y_raw": 0, "Y": 0}
                continue

            for key, current_sign in (("Y_raw", y_raw_sign), ("Y", y_sign)):
                if current_sign == 0:
                    continue
                prior_sign = last_nonzero_signs_neutral[key]
                if prior_sign != 0 and prior_sign != current_sign:
                    flip_counts_neutral[key] += 1
                last_nonzero_signs_neutral[key] = current_sign

    if stale_hold_run_s > 0:
        stale_hold_runs_s.append(stale_hold_run_s)
        stale_hold_max_s = max(stale_hold_max_s, stale_hold_run_s)

    if tick_intervals:
        out["tick_interval_s"] = {
            "mean": mean(tick_intervals),
            "p50": median(tick_intervals),
            "max": max(tick_intervals),
        }
    else:
        out["tick_interval_s"] = {}

    out["price_series_counts"] = dict(price_series_counts)
    out["top_source_counts"] = dict(top_source_counts)
    out["size_bin_counts"] = dict(size_bins)
    out["spot_fresh_rate"] = _ratio(spot_fresh, len(records))
    out["perp_fresh_rate"] = _ratio(perp_fresh, len(records))
    out["y_near_zero_rate"] = _ratio(y_near_zero, len(records))
    out["y_raw_near_zero_rate"] = _ratio(y_raw_near_zero, len(records))
    out["y_raw_saturation_rate"] = _ratio(y_raw_saturated, len(records))
    out["gate_low_rate"] = _ratio(gate_low, len(records))
    out["perp_dominant_spot_missing_rate"] = _ratio(
        perp_dominant_spot_missing, len(records)
    )
    out["price_series_switches"] = series_switches
    out["price_series_switch_rate_per_min"] = (
        series_switches / (total_duration_s / 60.0) if total_duration_s > 0 else 0.0
    )
    out["price_series_base_switches"] = base_series_switches
    out["price_series_base_switch_rate_per_min"] = (
        base_series_switches / (total_duration_s / 60.0) if total_duration_s > 0 else 0.0
    )
    out["duration_s"] = total_duration_s
    out["duration_min"] = total_duration_s / 60.0 if total_duration_s > 0 else 0.0
    symbol = str(records[0].get("symbol", "")).upper() if records else ""
    unavailable_count = 0
    if event_counts is not None and symbol:
        unavailable_count = event_counts.get(symbol, Counter()).get(
            "price_series_unavailable", 0
        )
    duration_min = out["duration_min"]
    duration_min_value = float(duration_min) if isinstance(duration_min, (int, float)) else 0.0
    out["price_series_unavailable_count"] = unavailable_count
    out["price_series_unavailable_rate"] = (
        unavailable_count / len(records) if records else 0.0
    )
    out["price_series_unavailable_per_min"] = (
        unavailable_count / duration_min_value if duration_min_value > 0 else 0.0
    )

    out["persist_input_counts"] = dict(persist_input_counts)
    out["persist_update_mode_counts"] = dict(persist_update_mode_counts)
    out["persist_activity_rate"] = _ratio(persist_activity_true, len(records))
    persist_dt_sorted = sorted(persist_dt_s_stats.values)
    out["persist_dt_p50_s"] = _percentile(persist_dt_sorted, 0.50) if persist_dt_sorted else 0.0
    out["persist_dt_p90_s"] = _percentile(persist_dt_sorted, 0.90) if persist_dt_sorted else 0.0
    out["persist_dt_mean_s"] = mean(persist_dt_sorted) if persist_dt_sorted else 0.0
    out["persist_update_mode_fractions"] = {
        mode: _ratio(count, len(records)) for mode, count in persist_update_mode_counts.items()
    }

    persist_abs = [abs(value) for value in persist_raw_stats.values]
    out["persist_abs_p95"] = _percentile(sorted(persist_abs), 0.95) if persist_abs else 0.0
    out["persist_abs_p99"] = _percentile(sorted(persist_abs), 0.99) if persist_abs else 0.0
    out["persist_abs_max"] = max(persist_abs) if persist_abs else 0.0
    out["persist_stale_hold_max_s"] = stale_hold_max_s
    out["persist_stale_hold_p95_s"] = (
        _percentile(sorted(stale_hold_runs_s), 0.95) if stale_hold_runs_s else 0.0
    )
    out["persist_stale_hold_runs"] = len(stale_hold_runs_s)
    out["persist_stale_hold_a_quiet_abs"] = dormant_quiet_abs
    out["persist_stale_hold_s_elevated_abs"] = s_elevated_abs

    out["persist_pivot_half_life_p50_s"] = (
        _percentile(sorted(pivot_half_life_s), 0.50) if pivot_half_life_s else 0.0
    )
    out["persist_pivot_half_life_p90_s"] = (
        _percentile(sorted(pivot_half_life_s), 0.90) if pivot_half_life_s else 0.0
    )
    out["persist_pivot_to_neutral_p50_s"] = (
        _percentile(sorted(pivot_to_neutral_s), 0.50) if pivot_to_neutral_s else 0.0
    )
    out["persist_pivot_to_neutral_p90_s"] = (
        _percentile(sorted(pivot_to_neutral_s), 0.90) if pivot_to_neutral_s else 0.0
    )
    out["persist_pivot_runs"] = pivot_run_starts
    out["persist_pivot_half_life_rate"] = _ratio(len(pivot_half_life_s), pivot_run_starts)
    out["persist_pivot_to_neutral_rate"] = _ratio(len(pivot_to_neutral_s), pivot_run_starts)
    out["persist_pivot_a_active_abs"] = pivot_active_abs
    out["persist_pivot_neutral_zone_abs"] = pivot_neutral_zone_abs

    out["persist_quiet_release_half_life_p50_s"] = (
        _percentile(sorted(quiet_release_half_life_s), 0.50)
        if quiet_release_half_life_s
        else 0.0
    )
    out["persist_quiet_release_half_life_p90_s"] = (
        _percentile(sorted(quiet_release_half_life_s), 0.90)
        if quiet_release_half_life_s
        else 0.0
    )
    out["persist_quiet_release_runs"] = quiet_release_starts
    out["persist_quiet_release_half_life_rate"] = _ratio(
        len(quiet_release_half_life_s),
        quiet_release_starts,
    )

    out["y_raw_disp_mismatch_rate"] = _ratio(
        y_raw_disp_mismatch, y_raw_disp_mismatch_total
    )
    out["y_raw_disp_dir_mismatch_rate"] = _ratio(
        y_raw_disp_dir_mismatch, y_raw_disp_dir_mismatch_total
    )
    out["disp_deadband_active_rate"] = _ratio(disp_deadband_active, len(records))
    out["disp_ratio_p50"] = _percentile(sorted(disp_ratio_values), 0.50) if disp_ratio_values else 0.0
    if e_dir_run > 0:
        e_dir_persist_runs.append(e_dir_run)
    out["e_dir_persistence_p50"] = _percentile(sorted(e_dir_persist_runs), 0.50) if e_dir_persist_runs else 0.0

    y_raw_abs = [abs(value) for value in y_raw_stats.values]
    out["y_raw_abs_p95"] = _percentile(sorted(y_raw_abs), 0.95) if y_raw_abs else 0.0
    out["y_raw_abs_p99"] = _percentile(sorted(y_raw_abs), 0.99) if y_raw_abs else 0.0
    x_raw_summary = x_raw_stats.summary()
    x_summary = x_stats.summary()
    out["x_raw_mean"] = x_raw_summary.get("mean", 0.0)
    out["x_mean"] = x_summary.get("mean", 0.0)

    eff_rel_all_sorted = sorted(eff_rel_abs_all)
    eff_rel_active_sorted = sorted(eff_rel_abs_active)
    p95_all = _percentile(eff_rel_all_sorted, 0.95) if eff_rel_all_sorted else 0.0
    if eff_rel_active_sorted:
        p95_active = _percentile(eff_rel_active_sorted, 0.95)
    else:
        p95_active = p95_all

    out["eff_rel_abs_p95_all"] = p95_all
    out["eff_rel_abs_p95_active"] = p95_active
    for target in K_RECO_TARGETS:
        key = f"k_reco_target_{target}"
        if p95_active <= 0:
            out[key] = 0.0
        else:
            out[key] = _atanh(target) / p95_active

    out["sign_flip_rate_per_min"] = {
        "Y_raw": flip_counts["Y_raw"] / (total_duration_s / 60.0)
        if total_duration_s > 0
        else 0.0,
        "Y": flip_counts["Y"] / (total_duration_s / 60.0)
        if total_duration_s > 0
        else 0.0,
        "X_raw": flip_counts["X_raw"] / (total_duration_s / 60.0)
        if total_duration_s > 0
        else 0.0,
        "X": flip_counts["X"] / (total_duration_s / 60.0)
        if total_duration_s > 0
        else 0.0,
    }
    sign_flip_rates = out["sign_flip_rate_per_min"]
    out["sign_flip_rate_per_min_before_after"] = {
        "Y_raw": sign_flip_rates["Y_raw"],
        "Y": sign_flip_rates["Y"],
        "X_raw": sign_flip_rates["X_raw"],
        "X": sign_flip_rates["X"],
    }
    out["sign_flip_rate_per_min_delta_raw_minus_smoothed"] = {
        "Y": sign_flip_rates["Y_raw"] - sign_flip_rates["Y"],
        "X": sign_flip_rates["X_raw"] - sign_flip_rates["X"],
    }
    out["conditional_flip_rate_disp_rate_per_min"] = {
        str(multiplier): {
            "Y_raw": flip_counts_disp[multiplier]["Y_raw"] / (disp_durations[multiplier] / 60.0)
            if disp_durations[multiplier] > 0
            else 0.0,
            "Y": flip_counts_disp[multiplier]["Y"] / (disp_durations[multiplier] / 60.0)
            if disp_durations[multiplier] > 0
            else 0.0,
        }
        for multiplier in DISP_RATE_MULTIPLIERS
    }
    out["conditional_flip_rate_x_raw_per_min"] = {
        "dominant": {
            "Y_raw": flip_counts_dom["Y_raw"] / (dom_duration_s / 60.0)
            if dom_duration_s > 0
            else 0.0,
            "Y": flip_counts_dom["Y"] / (dom_duration_s / 60.0)
            if dom_duration_s > 0
            else 0.0,
        },
        "neutral": {
            "Y_raw": flip_counts_neutral["Y_raw"] / (neutral_duration_s / 60.0)
            if neutral_duration_s > 0
            else 0.0,
            "Y": flip_counts_neutral["Y"] / (neutral_duration_s / 60.0)
            if neutral_duration_s > 0
            else 0.0,
        },
        "x_min": X_MIN_THRESHOLD,
    }

    out["window_ms"] = _format_summary("window_ms", window_stats.summary())
    out["log_return"] = _format_summary("log_return", log_return_stats.summary())
    out["delta_price"] = _format_summary("delta_price", price_delta_stats.summary())
    out["disp"] = _format_summary("disp", disp_stats.summary())
    out["disp_rate"] = _format_summary("disp_rate", disp_rate_stats.summary())
    out["eff_raw"] = _format_summary("eff_raw", eff_raw_stats.summary())
    out["gate"] = _format_summary("gate", gate_stats.summary())
    out["Y_raw"] = _format_summary("Y_raw", y_raw_stats.summary())
    out["Y"] = _format_summary("Y", y_stats.summary())
    out["persist_raw"] = _format_summary("persist_raw", persist_raw_stats.summary())
    out["persist_slope"] = _format_summary("persist_slope", persist_slope_stats.summary())
    out["persist_sign"] = _format_summary("persist_sign", persist_sign_stats.summary())
    out["persist_input_value"] = _format_summary(
        "persist_input_value", persist_input_value_stats.summary()
    )
    out["persist_dt_s"] = _format_summary("persist_dt_s", persist_dt_s_stats.summary())
    out["persist_gain_per_second"] = _format_summary(
        "persist_gain_per_second", persist_gain_per_second_stats.summary()
    )
    out["persist_input_deadband"] = _format_summary(
        "persist_input_deadband", persist_input_deadband_stats.summary()
    )
    out["persist_step_coeff"] = _format_summary(
        "persist_step_coeff", persist_step_coeff_stats.summary()
    )
    out["X_raw"] = _format_summary("X_raw", x_raw_stats.summary())
    out["X"] = _format_summary("X", x_stats.summary())
    out["size_raw"] = _format_summary("size_raw", size_raw_stats.summary())
    out["size_effort_norm"] = _format_summary(
        "size_effort_norm", size_effort_norm_stats.summary()
    )
    out["size_scale"] = _format_summary("size_scale", size_scale_stats.summary())
    out["E_total"] = _format_summary("E_total", e_total_stats.summary())
    out["E_rate"] = _format_summary("E_rate", e_rate_stats.summary())
    out["E_spot_share"] = _format_summary("E_spot_share", e_spot_share_stats.summary())
    out["effort_norm"] = _format_summary("effort_norm", effort_norm_stats.summary())
    out["effort_floor"] = _format_summary("effort_floor", effort_floor_stats.summary())
    out["disp_scale"] = _format_summary("disp_scale", disp_scale_stats.summary())
    out["E_scale"] = _format_summary("E_scale", e_scale_stats.summary())
    out["E_dir"] = _format_summary("E_dir", e_dir_stats.summary())
    out["E_dir_sign"] = _format_summary("E_dir_sign", e_dir_sign_stats.summary())
    out["halo_raw"] = _format_summary("halo_raw", halo_raw_stats.summary())
    out["halo"] = _format_summary("halo", halo_stats.summary())
    out["source_count_active"] = _format_summary(
        "source_count_active", source_count_stats.summary()
    )
    out["max_source_share"] = _format_summary(
        "max_source_share", max_source_share_stats.summary()
    )
    out["tanh_k"] = _format_summary("tanh_k", tanh_k_stats.summary())
    out["spot_event_count_window"] = _format_summary(
        "spot_event_count_window", spot_events.summary()
    )
    out["perp_event_count_window"] = _format_summary(
        "perp_event_count_window", perp_events.summary()
    )
    return out


def _output_path(symbols: set[str], *, prefix: str = "diagnostics-report") -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ""
    if symbols:
        suffix = "-" + "-".join(sorted(symbols))
    base = f"{prefix}-{timestamp}{suffix}.txt"
    out_dir = Path("docs/diagnostics")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / base


def _latest_log_path() -> Path:
    log_dir = Path("logs")
    candidates = list(log_dir.glob("flow_lens_diagnostics*.jsonl"))
    if not candidates:
        return log_dir / "flow_lens_diagnostics.jsonl"
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Flow Lens diagnostics JSONL.")
    parser.add_argument(
        "--path",
        default="",
        help="Path to diagnostics JSONL log (defaults to latest in logs/).",
    )
    parser.add_argument(
        "--dir",
        nargs="?",
        const="logs/replay",
        default=None,
        help="Directory of diagnostics JSONL(.gz) replays to summarize.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated list of symbols to include.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output file path. Defaults to docs/diagnostics with a unique name.",
    )
    parser.add_argument(
        "--config",
        default="config/app.toml",
        help="Optional config TOML path to include in header.",
    )
    args = parser.parse_args()

    if args.dir is not None:
        replay_dir = Path(args.dir)
        if not replay_dir.exists():
            raise SystemExit(f"Missing replay dir: {replay_dir}")
        paths = sorted(
            list(replay_dir.glob("*.jsonl")) + list(replay_dir.glob("*.jsonl.gz"))
        )
        if not paths:
            raise SystemExit(f"No replay logs found in {replay_dir}")
        config_path = Path(args.config)
        config_values = _load_config_from_log(paths[0])
        if config_values:
            config_values = {**config_values, "config_source": "replay_meta"}
        else:
            config_values = _load_config_summary(config_path) if config_path.exists() else {}
            if config_values:
                config_values = {**config_values, "config_source": "app_toml"}
        scenario_manifest = Path("docs/diagnostics/scenario_runs/manifest.json")
        scenario_info = _load_scenario_manifest(scenario_manifest)
        output_path = (
            Path(args.out)
            if args.out
            else _output_path(set(), prefix="diagnostics-summary")
        )
        _write_summary(
            paths=paths,
            out_path=output_path,
            config_values=config_values,
            scenario_info=scenario_info,
        )
        print(f"Analyzed dir: {replay_dir}")
        print(f"Wrote diagnostics report to {output_path}")
        return

    path = Path(args.path) if args.path else _latest_log_path()
    if not path.exists():
        raise SystemExit(f"Missing log file: {path}")

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    records, events = _load_records_and_events(path)
    grouped = _iter_symbols(records, symbols if symbols else None)
    event_counts = _event_counts_by_symbol(events)

    output_path = Path(args.out) if args.out else _output_path(symbols)
    config_path = Path(args.config)
    config_values = _load_config_from_log(path)
    if config_values:
        config_values = {**config_values, "config_source": "replay_meta"}
    else:
        config_values = _load_config_summary(config_path) if config_path.exists() else {}
        if config_values:
            config_values = {**config_values, "config_source": "app_toml"}
    majors = {"BTC", "ETH", "SOL"}
    major_k_values: list[float] = []
    major_symbols: list[str] = []

    per_symbol_stats: dict[str, dict[str, object]] = {}
    for symbol, entries in sorted(grouped.items()):
        stats = _stats_for_symbol(entries, config=config_values, event_counts=event_counts)
        per_symbol_stats[symbol] = stats
        if symbol in majors:
            k_07_value = stats.get("k_reco_target_0.7", 0.0)
            k_07 = float(k_07_value) if isinstance(k_07_value, (int, float)) else 0.0
            if k_07 > 0:
                major_k_values.append(k_07)
                major_symbols.append(symbol)

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"source_log: {path}\n")
        handle.write(f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if config_values:
            handle.write("config:\n")
            for key, value in config_values.items():
                handle.write(f"  {key}: {value}\n")
        handle.write(f"eff_rel_active_multiplier: {EFF_REL_ACTIVE_MULTIPLIER:.2f}\n")
        if major_k_values:
            handle.write(
                "k_reco_target_0.7_median_majors: "
                f"{median(major_k_values):.6f}\n"
            )
            handle.write(f"majors: {','.join(sorted(set(major_symbols)))}\n")

        for symbol, stats in per_symbol_stats.items():
            handle.write(f"\n== {symbol} ==\n")
            handle.write(f"records: {stats['records']}\n")
            tick = stats.get("tick_interval_s")
            if isinstance(tick, dict) and tick:
                tick_values = cast(dict[str, float], tick)
                handle.write(
                    "tick_interval_s: mean="
                    f"{tick_values.get('mean', 0.0):.2f} p50="
                    f"{tick_values.get('p50', 0.0):.2f} max="
                    f"{tick_values.get('max', 0.0):.2f}\n"
                )
            duration_min = stats.get("duration_min", 0.0)
            duration_min_value = (
                float(duration_min) if isinstance(duration_min, int | float) else 0.0
            )
            handle.write(f"duration_min: {duration_min_value:.2f}\n")
            handle.write(f"price_series_counts: {stats['price_series_counts']}\n")
            handle.write(
                "price_series_unavailable: "
                f"{stats['price_series_unavailable_count']} "
                f"(per_min={stats['price_series_unavailable_per_min']:.2f})\n"
            )
            handle.write(
                f"spot_fresh_rate: {stats['spot_fresh_rate']:.2f} "
                f"perp_fresh_rate: {stats['perp_fresh_rate']:.2f}\n"
            )
            handle.write(f"size_bin_counts: {stats['size_bin_counts']}\n")
            handle.write(f"top_source_counts: {stats['top_source_counts']}\n")
            handle.write(f"y_near_zero_rate: {stats['y_near_zero_rate']:.2f}\n")
            handle.write(f"y_raw_near_zero_rate: {stats['y_raw_near_zero_rate']:.2f}\n")
            handle.write(f"gate_low_rate: {stats['gate_low_rate']:.2f}\n")
            handle.write(
                "perp_dominant_spot_missing_rate: "
                f"{stats['perp_dominant_spot_missing_rate']:.2f}\n"
            )
            handle.write(
                "price_series_switches: "
                f"{stats['price_series_switches']} "
                f"(rate_per_min={stats['price_series_switch_rate_per_min']:.2f})\n"
            )
            handle.write(
                "y_raw_disp_mismatch_rate: "
                f"{stats['y_raw_disp_mismatch_rate']:.2f}\n"
            )
            handle.write(
                "y_raw_disp_dir_mismatch_rate: "
                f"{stats['y_raw_disp_dir_mismatch_rate']:.2f}\n"
            )
            handle.write(
                "disp_deadband_active_rate: "
                f"{stats['disp_deadband_active_rate']:.2f}\n"
            )
            handle.write(
                "disp_ratio_p50: "
                f"{stats['disp_ratio_p50']:.3f}\n"
            )
            handle.write(
                "e_dir_persistence_p50: "
                f"{stats['e_dir_persistence_p50']:.1f}\n"
            )
            handle.write(
                "eff_rel_abs_p95_all: "
                f"{stats['eff_rel_abs_p95_all']:.6f}\n"
            )
            handle.write(
                "eff_rel_abs_p95_active: "
                f"{stats['eff_rel_abs_p95_active']:.6f}\n"
            )
            handle.write(
                "k_reco_target_0.6: "
                f"{stats['k_reco_target_0.6']:.6f}\n"
            )
            handle.write(
                "k_reco_target_0.7: "
                f"{stats['k_reco_target_0.7']:.6f}\n"
            )
            handle.write(
                "k_reco_target_0.8: "
                f"{stats['k_reco_target_0.8']:.6f}\n"
            )
            handle.write(f"sign_flip_rate_per_min: {stats['sign_flip_rate_per_min']}\n")
            handle.write(
                "conditional_flip_rate_disp_rate_per_min: "
                f"{stats['conditional_flip_rate_disp_rate_per_min']}\n"
            )
            handle.write(
                "conditional_flip_rate_x_raw_per_min: "
                f"{stats['conditional_flip_rate_x_raw_per_min']}\n"
            )
            handle.write(
                "sign_flip_rate_per_min_before_after: "
                f"{stats['sign_flip_rate_per_min_before_after']}\n"
            )
            handle.write(
                "sign_flip_rate_per_min_delta_raw_minus_smoothed: "
                f"{stats['sign_flip_rate_per_min_delta_raw_minus_smoothed']}\n"
            )
            handle.write(f"persist_input_counts: {stats['persist_input_counts']}\n")
            handle.write(f"persist_update_mode_counts: {stats['persist_update_mode_counts']}\n")
            handle.write(
                "persist_update_mode_fractions: "
                f"{stats['persist_update_mode_fractions']}\n"
            )
            handle.write(
                "persist_activity_rate: "
                f"{stats['persist_activity_rate']:.2f}\n"
            )
            handle.write(
                "persist_dt_p50_s/p90_s/mean_s: "
                f"{stats['persist_dt_p50_s']:.2f}/"
                f"{stats['persist_dt_p90_s']:.2f}/"
                f"{stats['persist_dt_mean_s']:.2f}\n"
            )
            handle.write(
                "persist_abs_p95/p99/max: "
                f"{stats['persist_abs_p95']:.2f}/"
                f"{stats['persist_abs_p99']:.2f}/"
                f"{stats['persist_abs_max']:.2f}\n"
            )
            handle.write(
                "persist_stale_hold_max_s/p95_s/runs: "
                f"{stats['persist_stale_hold_max_s']:.1f}/"
                f"{stats['persist_stale_hold_p95_s']:.1f}/"
                f"{stats['persist_stale_hold_runs']}\n"
            )
            handle.write(
                "persist_pivot_half_life_p50_s/p90_s/to_neutral_p50_s: "
                f"{stats['persist_pivot_half_life_p50_s']:.1f}/"
                f"{stats['persist_pivot_half_life_p90_s']:.1f}/"
                f"{stats['persist_pivot_to_neutral_p50_s']:.1f}\n"
            )
            handle.write(
                "persist_pivot_runs/to_neutral_rate: "
                f"{stats['persist_pivot_runs']}/"
                f"{stats['persist_pivot_to_neutral_rate']:.2f}\n"
            )
            handle.write(
                "persist_quiet_release_half_life_p50_s/p90_s/runs: "
                f"{stats['persist_quiet_release_half_life_p50_s']:.1f}/"
                f"{stats['persist_quiet_release_half_life_p90_s']:.1f}/"
                f"{stats['persist_quiet_release_runs']}\n"
            )
            for key in (
                "window_ms",
                "log_return",
                "delta_price",
                "disp",
                "disp_rate",
                "eff_raw",
                "gate",
                "tanh_k",
                "Y_raw",
                "Y",
                "persist_raw",
                "persist_slope",
                "persist_sign",
                "persist_input_value",
                "persist_dt_s",
                "persist_gain_per_second",
                "persist_input_deadband",
                "persist_step_coeff",
                "X_raw",
                "X",
                "size_raw",
                "size_effort_norm",
                "size_scale",
                "E_total",
                "E_rate",
                "E_dir",
                "E_dir_sign",
                "E_spot_share",
                "effort_norm",
                "effort_floor",
                "disp_scale",
                "E_scale",
                "halo_raw",
                "halo",
                "source_count_active",
                "max_source_share",
                "spot_event_count_window",
                "perp_event_count_window",
            ):
                handle.write(f"{stats[key]}\n")

    print(f"Analyzed log: {path}")
    print(f"Wrote diagnostics report to {output_path}")


if __name__ == "__main__":
    main()
