from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DistCloseRecord:
    source_id: str
    tf: str
    close_ms: int
    p_status: str
    p_missing_reason: str | None
    selection_source: str | None
    oi_tolerance_ms: int | None
    selected_abs_offset_ms: int | None
    selected_tolerance_margin_ms: int | None
    best_candidate_abs_offset_ms: int | None
    best_candidate_tolerance_margin_ms: int | None


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = int(round((len(sorted_values) - 1) * max(0.0, min(1.0, p))))
    return sorted_values[index]


def _fmt(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _load_records(path: Path) -> tuple[dict[str, Any], list[DistCloseRecord]]:
    meta: dict[str, Any] = {}
    records: list[DistCloseRecord] = []
    with _open_text(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if "_meta" in payload:
                meta = payload["_meta"].get("config", {})
                continue
            if payload.get("event_type") != "dist_state_close":
                continue
            if not payload.get("processed"):
                continue
            records.append(
                DistCloseRecord(
                    source_id=str(payload.get("source_id", "")),
                    tf=str(payload.get("tf", "")),
                    close_ms=int(payload.get("kline_close_ms", 0)),
                    p_status=str(payload.get("p_status", "missing")),
                    p_missing_reason=payload.get("p_missing_reason"),
                    selection_source=payload.get("selection_source"),
                    oi_tolerance_ms=(
                        int(payload["oi_tolerance_ms"])
                        if payload.get("oi_tolerance_ms") is not None
                        else None
                    ),
                    selected_abs_offset_ms=(
                        int(payload["selected_abs_offset_ms"])
                        if payload.get("selected_abs_offset_ms") is not None
                        else None
                    ),
                    selected_tolerance_margin_ms=(
                        int(payload["selected_tolerance_margin_ms"])
                        if payload.get("selected_tolerance_margin_ms") is not None
                        else None
                    ),
                    best_candidate_abs_offset_ms=(
                        int(payload["best_candidate_abs_offset_ms"])
                        if payload.get("best_candidate_abs_offset_ms") is not None
                        else None
                    ),
                    best_candidate_tolerance_margin_ms=(
                        int(payload["best_candidate_tolerance_margin_ms"])
                        if payload.get("best_candidate_tolerance_margin_ms") is not None
                        else None
                    ),
                )
            )
    return meta, records


def _latest_dist_diag_file() -> Path:
    diagnostics_dir = Path("docs/diagnostics")
    replay_dir = Path("logs/replay_dist")
    candidates: list[Path] = []
    for pattern in (
        "dist_state_diagnostics-*.jsonl",
        "dist_state_diagnostics-*.jsonl.gz",
        "dist_state_replay-*.jsonl",
        "dist_state_replay-*.jsonl.gz",
    ):
        candidates.extend(sorted(diagnostics_dir.glob(pattern)))
        candidates.extend(sorted(replay_dir.glob(pattern)))
    if not candidates:
        raise SystemExit("No dist-state diagnostics/replay files found.")
    def _name_ts(path: Path) -> str:
        # Prefer run-start timestamp encoded in filename so "latest run" wins,
        # even when multiple files are being appended concurrently.
        match = re.search(r"(\d{8}-\d{6})", path.name)
        return match.group(1) if match else ""

    return max(candidates, key=lambda p: (_name_ts(p), p.name))


def _summary_output_path(input_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    stem = input_path.name
    for suffix in (".jsonl.gz", ".jsonl", ".gz"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    safe = stem.replace("dist_state_diagnostics-", "")[:48]
    return Path("docs/diagnostics") / f"dist_state-summary-{timestamp}-{safe}.txt"


def _fill_missing_from_close_id(records: list[DistCloseRecord]) -> dict[tuple[str, int], dict[str, int]]:
    by_close: dict[tuple[str, int], dict[str, int]] = {}
    for record in records:
        close_id = (record.source_id, record.close_ms)
        slot = by_close.setdefault(close_id, {})
        if (
            "best_candidate_abs_offset_ms" not in slot
            and record.best_candidate_abs_offset_ms is not None
        ):
            slot["best_candidate_abs_offset_ms"] = record.best_candidate_abs_offset_ms
        if (
            "best_candidate_tolerance_margin_ms" not in slot
            and record.best_candidate_tolerance_margin_ms is not None
        ):
            slot["best_candidate_tolerance_margin_ms"] = record.best_candidate_tolerance_margin_ms
        if "selected_abs_offset_ms" not in slot and record.selected_abs_offset_ms is not None:
            slot["selected_abs_offset_ms"] = record.selected_abs_offset_ms
        if (
            "selected_tolerance_margin_ms" not in slot
            and record.selected_tolerance_margin_ms is not None
        ):
            slot["selected_tolerance_margin_ms"] = record.selected_tolerance_margin_ms
    return by_close


def _build_report(meta: dict[str, Any], records: list[DistCloseRecord], input_path: Path) -> str:
    if not records:
        return "No processed dist_state_close records found."

    close_fallback = _fill_missing_from_close_id(records)
    by_tf: dict[str, list[DistCloseRecord]] = defaultdict(list)
    for record in records:
        by_tf[record.tf].append(record)

    lines: list[str] = []
    lines.append("== dist_state summary ==")
    lines.append(f"input_file: {input_path}")
    lines.append(f"processed_closes: {len(records)}")
    lines.append(
        "config: "
        f"mode={meta.get('dist_state_p_availability_mode','n/a')} "
        f"tolerance_ms={meta.get('dist_state_oi_tolerance_ms','n/a')} "
        f"poll_ms={meta.get('dist_state_oi_poll_interval_ms','n/a')} "
        f"verify={meta.get('dist_state_oi_verify_enabled','n/a')} "
        f"verify_tfs={meta.get('dist_state_oi_verify_timeframes','n/a')}"
    )
    lines.append("")

    total = len(records)
    computed_total = sum(1 for r in records if r.p_status == "computed")
    missing_total = total - computed_total
    miss_rates = (computed_total / total * 100.0) if total else 0.0
    miss_out_all: list[float] = []
    selected_offsets_all: list[float] = []
    verify_selected = 0
    sampler_selected = 0
    for record in records:
        close_id = (record.source_id, record.close_ms)
        selected_abs = record.selected_abs_offset_ms
        best_margin = record.best_candidate_tolerance_margin_ms
        if selected_abs is None:
            selected_abs = close_fallback.get(close_id, {}).get("selected_abs_offset_ms")
        if best_margin is None:
            best_margin = close_fallback.get(close_id, {}).get("best_candidate_tolerance_margin_ms")
        if selected_abs is not None:
            selected_offsets_all.append(float(selected_abs))
        if record.p_status != "computed" and best_margin is not None and best_margin < 0:
            miss_out_all.append(float(-best_margin))
        if record.selection_source == "verify":
            verify_selected += 1
        elif record.selection_source == "sampler":
            sampler_selected += 1
    selected_total = verify_selected + sampler_selected
    verify_share = (verify_selected / selected_total * 100.0) if selected_total else 0.0

    lines.append("at_a_glance:")
    lines.append(
        f"- p_coverage: {computed_total}/{total} ({_fmt(miss_rates,1)}%)"
    )
    lines.append(
        "- misses_outside_tolerance: "
        f"{missing_total} closes, avg={_fmt(statistics.fmean(miss_out_all) if miss_out_all else None,1)}ms, "
        f"p95={_fmt(_percentile(miss_out_all,0.95),1)}ms, max={_fmt(max(miss_out_all) if miss_out_all else None,1)}ms"
    )
    lines.append(
        "- selected_abs_offset: "
        f"avg={_fmt(statistics.fmean(selected_offsets_all) if selected_offsets_all else None,1)}ms, "
        f"p95={_fmt(_percentile(selected_offsets_all,0.95),1)}ms, "
        f"max={_fmt(max(selected_offsets_all) if selected_offsets_all else None,1)}ms"
    )
    lines.append(
        f"- selection_bias: verify={verify_selected}, sampler={sampler_selected}, verify_share={_fmt(verify_share,1)}%"
    )
    lines.append("")

    global_miss_out_ms: list[float] = []
    global_good_margin_ms: list[float] = []
    global_offsets_ms: list[float] = []
    global_selection = Counter()
    global_missing_reasons = Counter()

    for tf in sorted(by_tf):
        tf_records = by_tf[tf]
        total = len(tf_records)
        computed = sum(1 for r in tf_records if r.p_status == "computed")
        selection_counts = Counter(r.selection_source for r in tf_records)
        missing_reasons = Counter(r.p_missing_reason for r in tf_records if r.p_status != "computed")

        for key, value in selection_counts.items():
            global_selection[(tf, key)] += value
        for key, value in missing_reasons.items():
            global_missing_reasons[(tf, key)] += value

        selected_offsets: list[float] = []
        good_margins: list[float] = []
        miss_outs: list[float] = []
        for record in tf_records:
            close_id = (record.source_id, record.close_ms)
            selected_abs = record.selected_abs_offset_ms
            selected_margin = record.selected_tolerance_margin_ms
            best_margin = record.best_candidate_tolerance_margin_ms
            if selected_abs is None:
                selected_abs = close_fallback.get(close_id, {}).get("selected_abs_offset_ms")
            if selected_margin is None:
                selected_margin = close_fallback.get(close_id, {}).get("selected_tolerance_margin_ms")
            if best_margin is None:
                best_margin = close_fallback.get(close_id, {}).get("best_candidate_tolerance_margin_ms")

            if selected_abs is not None:
                selected_offsets.append(float(selected_abs))
                global_offsets_ms.append(float(selected_abs))
            if selected_margin is not None and selected_margin >= 0:
                good_margins.append(float(selected_margin))
                global_good_margin_ms.append(float(selected_margin))
            if record.p_status != "computed" and best_margin is not None and best_margin < 0:
                miss_outs.append(float(-best_margin))
                global_miss_out_ms.append(float(-best_margin))

        lines.append(f"[{tf}]")
        lines.append(f"- closes: {total}")
        lines.append(f"- p_computed: {computed}/{total} ({_fmt(computed/total*100,1)}%)")
        lines.append(
            "- selection_source: "
            + ", ".join(
                f"{('none' if k is None else k)}:{v}"
                for k, v in sorted(selection_counts.items(), key=lambda item: str(item[0]))
            )
        )
        if missing_reasons:
            lines.append(
                "- missing_reasons: "
                + ", ".join(
                    f"{('none' if k is None else k)}:{v}"
                    for k, v in sorted(missing_reasons.items(), key=lambda item: str(item[0]))
                )
            )
        else:
            lines.append("- missing_reasons: none")

        lines.append(
            "- selected_abs_offset_ms: "
            f"avg={_fmt(statistics.fmean(selected_offsets) if selected_offsets else None,1)} "
            f"p50={_fmt(_percentile(selected_offsets,0.50),1)} "
            f"p95={_fmt(_percentile(selected_offsets,0.95),1)} "
            f"max={_fmt(max(selected_offsets) if selected_offsets else None,1)}"
        )
        lines.append(
            "- in_tolerance_margin_ms: "
            f"avg={_fmt(statistics.fmean(good_margins) if good_margins else None,1)} "
            f"p50={_fmt(_percentile(good_margins,0.50),1)} "
            f"p95={_fmt(_percentile(good_margins,0.95),1)} "
            f"min={_fmt(min(good_margins) if good_margins else None,1)}"
        )
        lines.append(
            "- miss_outside_by_ms: "
            f"avg={_fmt(statistics.fmean(miss_outs) if miss_outs else None,1)} "
            f"p50={_fmt(_percentile(miss_outs,0.50),1)} "
            f"p95={_fmt(_percentile(miss_outs,0.95),1)} "
            f"max={_fmt(max(miss_outs) if miss_outs else None,1)}"
        )
        lines.append("")

    lines.append("[global]")
    lines.append(
        "- selected_abs_offset_ms: "
        f"avg={_fmt(statistics.fmean(global_offsets_ms) if global_offsets_ms else None,1)} "
        f"p50={_fmt(_percentile(global_offsets_ms,0.50),1)} "
        f"p95={_fmt(_percentile(global_offsets_ms,0.95),1)} "
        f"p99={_fmt(_percentile(global_offsets_ms,0.99),1)} "
        f"max={_fmt(max(global_offsets_ms) if global_offsets_ms else None,1)}"
    )
    lines.append(
        "- in_tolerance_margin_ms: "
        f"avg={_fmt(statistics.fmean(global_good_margin_ms) if global_good_margin_ms else None,1)} "
        f"p50={_fmt(_percentile(global_good_margin_ms,0.50),1)} "
        f"p95={_fmt(_percentile(global_good_margin_ms,0.95),1)} "
        f"min={_fmt(min(global_good_margin_ms) if global_good_margin_ms else None,1)}"
    )
    lines.append(
        "- miss_outside_by_ms: "
        f"avg={_fmt(statistics.fmean(global_miss_out_ms) if global_miss_out_ms else None,1)} "
        f"p50={_fmt(_percentile(global_miss_out_ms,0.50),1)} "
        f"p95={_fmt(_percentile(global_miss_out_ms,0.95),1)} "
        f"max={_fmt(max(global_miss_out_ms) if global_miss_out_ms else None,1)}"
    )
    lines.append(
        "- selection_source_counts: "
        + ", ".join(
            f"{tf}:{('none' if src is None else src)}:{n}"
            for (tf, src), n in sorted(global_selection.items(), key=lambda item: (item[0][0], str(item[0][1])))
        )
    )
    lines.append(
        "- missing_reason_counts: "
        + (
            ", ".join(
                f"{tf}:{('none' if reason is None else reason)}:{n}"
                for (tf, reason), n in sorted(
                    global_missing_reasons.items(),
                    key=lambda item: (item[0][0], str(item[0][1])),
                )
            )
            if global_missing_reasons
            else "none"
        )
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize dist-state diagnostics.")
    parser.add_argument(
        "--input",
        default="",
        help="Path to dist_state_diagnostics JSONL(.gz). Defaults to latest in docs/diagnostics.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output report path. Defaults to docs/diagnostics/dist_state-summary-*.txt",
    )
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else _latest_dist_diag_file()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    meta, records = _load_records(input_path)
    report = _build_report(meta, records, input_path)
    out_path = Path(args.out) if args.out else _summary_output_path(input_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote dist_state summary to {out_path}")


if __name__ == "__main__":
    main()
