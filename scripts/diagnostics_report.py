#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Iterable, cast

DISP_RATE_MULTIPLIERS = (0.5, 1.0)
X_MIN_THRESHOLD = 0.2
EPSILON = 1e-12


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


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


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


def _stats_for_symbol(records: list[dict]) -> dict[str, object]:
    records.sort(key=lambda record: int(record.get("now_ms", 0)))
    out: dict[str, object] = {}
    out["records"] = len(records)

    now_ms_stats = SeriesStats([])
    window_stats = SeriesStats([])
    log_return_stats = SeriesStats([])
    disp_stats = SeriesStats([])
    disp_rate_stats = SeriesStats([])
    eff_raw_stats = SeriesStats([])
    gate_stats = SeriesStats([])
    y_raw_stats = SeriesStats([])
    y_stats = SeriesStats([])
    x_raw_stats = SeriesStats([])
    x_stats = SeriesStats([])
    size_raw_stats = SeriesStats([])
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

    price_series_counts: Counter[str] = Counter()
    top_source_counts: Counter[str] = Counter()
    size_bins: Counter[int] = Counter()

    spot_fresh = 0
    perp_fresh = 0
    spot_events = SeriesStats([])
    perp_events = SeriesStats([])
    y_near_zero = 0
    y_raw_near_zero = 0
    gate_low = 0
    perp_dominant_spot_missing = 0
    y_raw_disp_mismatch = 0
    y_raw_disp_mismatch_total = 0
    y_raw_disp_dir_mismatch = 0
    y_raw_disp_dir_mismatch_total = 0

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
        x_raw_stats.add(float(record.get("X_raw", 0.0)))
        x_stats.add(float(record.get("X", 0.0)))
        size_raw_stats.add(float(record.get("size_raw", 0.0)))
        size_bins[int(record.get("size_bin", 0))] += 1
        e_total_stats.add(float(record.get("E_total", 0.0)))
        e_rate_stats.add(float(record.get("E_rate", 0.0)))
        e_spot_share_stats.add(float(record.get("E_spot_share", 0.0)))
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
        disp_rate = float(record.get("disp_rate", 0.0))
        disp_rate_sign = _sign(disp_rate)
        dominance = float(record.get("D", 0.0))
        dominance_sign = _sign(dominance)
        disp_rate_dir_sign = _sign(disp_rate * dominance_sign)

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
    out["gate_low_rate"] = _ratio(gate_low, len(records))
    out["perp_dominant_spot_missing_rate"] = _ratio(
        perp_dominant_spot_missing, len(records)
    )
    out["price_series_switches"] = series_switches
    out["price_series_switch_rate_per_min"] = (
        series_switches / (total_duration_s / 60.0) if total_duration_s > 0 else 0.0
    )
    out["duration_s"] = total_duration_s
    out["duration_min"] = total_duration_s / 60.0 if total_duration_s > 0 else 0.0

    out["y_raw_disp_mismatch_rate"] = _ratio(
        y_raw_disp_mismatch, y_raw_disp_mismatch_total
    )
    out["y_raw_disp_dir_mismatch_rate"] = _ratio(
        y_raw_disp_dir_mismatch, y_raw_disp_dir_mismatch_total
    )

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
    out["X_raw"] = _format_summary("X_raw", x_raw_stats.summary())
    out["X"] = _format_summary("X", x_stats.summary())
    out["size_raw"] = _format_summary("size_raw", size_raw_stats.summary())
    out["E_total"] = _format_summary("E_total", e_total_stats.summary())
    out["E_rate"] = _format_summary("E_rate", e_rate_stats.summary())
    out["E_spot_share"] = _format_summary("E_spot_share", e_spot_share_stats.summary())
    out["effort_norm"] = _format_summary("effort_norm", effort_norm_stats.summary())
    out["effort_floor"] = _format_summary("effort_floor", effort_floor_stats.summary())
    out["disp_scale"] = _format_summary("disp_scale", disp_scale_stats.summary())
    out["E_scale"] = _format_summary("E_scale", e_scale_stats.summary())
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


def _output_path(symbols: set[str]) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ""
    if symbols:
        suffix = "-" + "-".join(sorted(symbols))
    base = f"diagnostics-report-{timestamp}{suffix}.txt"
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
        "--symbols",
        default="",
        help="Comma-separated list of symbols to include.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output file path. Defaults to docs/diagnostics with a unique name.",
    )
    args = parser.parse_args()

    path = Path(args.path) if args.path else _latest_log_path()
    if not path.exists():
        raise SystemExit(f"Missing log file: {path}")

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    records = _load_records(path)
    grouped = _iter_symbols(records, symbols if symbols else None)

    output_path = Path(args.out) if args.out else _output_path(symbols)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"source_log: {path}\n")
        handle.write(f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for symbol, entries in sorted(grouped.items()):
            handle.write(f"\n== {symbol} ==\n")
            stats = _stats_for_symbol(entries)
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
            handle.write(f"sign_flip_rate_per_min: {stats['sign_flip_rate_per_min']}\n")
            handle.write(
                "conditional_flip_rate_disp_rate_per_min: "
                f"{stats['conditional_flip_rate_disp_rate_per_min']}\n"
            )
            handle.write(
                "conditional_flip_rate_x_raw_per_min: "
                f"{stats['conditional_flip_rate_x_raw_per_min']}\n"
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
                "X_raw",
                "X",
                "size_raw",
                "E_total",
                "E_rate",
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
