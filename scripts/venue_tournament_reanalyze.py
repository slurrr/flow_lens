#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import subprocess
import sys
from pathlib import Path

_BLOCK_ORDER = [
    "utc_boundary_early_asia",
    "asia_prime",
    "eu_open_ramp",
    "eu_active_pre_us",
    "morning_overlap",
    "late_morning",
    "us_afternoon",
]

_BLOCK_SLICE_COUNTS = {
    "utc_boundary_early_asia": 3,
    "asia_prime": 3,
    "eu_open_ramp": 1,
    "eu_active_pre_us": 5,
    "morning_overlap": 5,
    "late_morning": 5,
    "us_afternoon": 5,
}


def _find_capture(block_dir: Path) -> Path:
    candidates = sorted(block_dir.glob("*_trades_capture"), key=lambda p: p.name, reverse=True)
    for d in candidates:
        gz = d / "trades.jsonl.gz"
        if gz.exists():
            return gz
        plain = d / "trades.jsonl"
        if plain.exists():
            return plain
    raise FileNotFoundError(f"No capture found under: {block_dir}")


def _read_capture_meta_ms(path: Path) -> tuple[int, int]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[call-overload]
        first = f.readline().strip()
    if not first:
        return 0, 0
    try:
        payload = json.loads(first)
    except json.JSONDecodeError:
        return 0, 0
    if not isinstance(payload, dict):
        return 0, 0
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return 0, 0
    if meta.get("type") != "venue_trade_capture":
        return 0, 0
    try:
        created_at_ms = int(meta.get("created_at_ms") or 0)
        stop_at_ms = int(meta.get("stop_at_ms") or 0)
    except (TypeError, ValueError):
        return 0, 0
    return created_at_ms, stop_at_ms


def _run_cmd(argv: list[str], *, nice: int) -> None:
    cmd = argv if nice == 0 else ["nice", "-n", str(nice), *argv]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + f"argv={cmd}\n"
            + f"exit={proc.returncode}\n"
            + f"stdout:\n{proc.stdout}\n"
            + f"stderr:\n{proc.stderr}\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-run venue tournament analysis from existing captures.")
    parser.add_argument("--run-id", required=True, help="Scheduled run id (e.g. 20260210_tue).")
    parser.add_argument(
        "--captures-root",
        default="logs/venue_tournament_scheduled",
        help="Root path containing captured blocks.",
    )
    parser.add_argument(
        "--diagnostics-root",
        default="docs/diagnostics/venue_tournament_scheduled",
        help="Root path for analysis outputs.",
    )
    parser.add_argument(
        "--out-run-id",
        default="",
        help="Optional output run id. Default: same as --run-id.",
    )
    parser.add_argument(
        "--analysis-detail",
        choices=["all_only", "all_and_slices"],
        default="all_only",
        help="all_only writes block-level reports only; all_and_slices also writes slice reports.",
    )
    parser.add_argument(
        "--timebases",
        default="exchange_local,exchange,recv",
        help="Comma-separated timebases to run.",
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=5,
        help="Nice level for analysis subprocesses (default: 5).",
    )
    parser.add_argument(
        "--profile",
        choices=["standard", "fast"],
        default="fast",
        help=(
            "Analysis profile. 'fast' reduces compute load (coarser buckets, fewer calm windows, "
            "shorter windows) while staying self-consistent. Default: fast."
        ),
    )
    parser.add_argument(
        "--write-run-summary",
        action="store_true",
        help="Also write run summaries for selected timebases.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=3,
        help="Max concurrent report jobs (default: 3).",
    )
    args = parser.parse_args()

    in_run = Path(args.captures_root) / args.run_id
    out_run_id = args.out_run_id.strip() or args.run_id
    out_run = Path(args.diagnostics_root) / out_run_id
    out_run.mkdir(parents=True, exist_ok=True)

    if not in_run.exists():
        raise SystemExit(f"Missing capture run: {in_run}")

    timebases = [part.strip() for part in str(args.timebases).split(",") if part.strip()]
    for tb in timebases:
        if tb not in {"exchange", "recv", "exchange_local"}:
            raise SystemExit(f"Invalid timebase: {tb}")

    python = sys.executable
    tournament_script = str(Path("scripts") / "venue_discovery_tournament_trades.py")
    run_summary_script = str(Path("scripts") / "venue_tournament_run_summary.py")
    profile_args: list[str] = []
    if str(args.profile) == "fast":
        profile_args = [
            "--bucket-ms",
            "500",
            "--return-horizon-ms",
            "1000",
            "--dir-horizon-ms",
            "1000",
            "--pre-s",
            "1.0",
            "--post-s",
            "4.0",
            "--cooldown-s",
            "2.0",
            "--calm-count",
            "12",
            "--confirm-primary-s",
            "1.5",
            "--confirm-secondary-s",
            "3.0",
            "--max-stale-s",
            "1.0",
        ]

    report_tasks: list[tuple[str, list[str]]] = []
    for block in _BLOCK_ORDER:
        block_dir = in_run / block
        if not block_dir.exists():
            continue
        capture = _find_capture(block_dir)
        cap_created_ms, cap_stop_ms = _read_capture_meta_ms(capture)
        if cap_created_ms <= 0 or cap_stop_ms <= cap_created_ms:
            raise RuntimeError(f"Missing/invalid capture meta in {capture}")

        duration_ms = cap_stop_ms - cap_created_ms
        slice_count = _BLOCK_SLICE_COUNTS.get(block, 1)
        slice_ms = max(1, duration_ms // slice_count)

        for timebase in timebases:
            out_all = out_run / f"{block}_tb_{timebase}_all.txt"
            report_tasks.append(
                (
                    f"all: {block} {timebase}",
                    [
                        python,
                        tournament_script,
                        "--input",
                        str(capture),
                        "--timebase",
                        timebase,
                        *profile_args,
                        "--out",
                        str(out_all),
                    ],
                )
            )

            if str(args.analysis_detail) != "all_and_slices":
                continue

            for idx in range(slice_count):
                start_ms = cap_created_ms + idx * slice_ms
                end_ms = cap_stop_ms if idx == slice_count - 1 else cap_created_ms + (idx + 1) * slice_ms
                out_slice = out_run / f"{block}_tb_{timebase}_slice_{idx + 1:02d}.txt"
                report_tasks.append(
                    (
                        f"slice: {block} {timebase} {idx + 1:02d}",
                        [
                            python,
                            tournament_script,
                            "--input",
                            str(capture),
                            "--timebase",
                            timebase,
                            *profile_args,
                            "--start-ms",
                            str(start_ms),
                            "--end-ms",
                            str(end_ms),
                            "--out",
                            str(out_slice),
                        ],
                    )
                )

    def _run_task(task: tuple[str, list[str]]) -> str:
        label, argv = task
        _run_cmd(argv, nice=max(0, int(args.nice)))
        return label

    max_workers = max(1, int(args.jobs))
    print(f"report jobs: {len(report_tasks)} workers={max_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_run_task, task) for task in report_tasks]
        for fut in concurrent.futures.as_completed(futures):
            label = fut.result()
            print(f"done {label}")

    if args.write_run_summary:
        for timebase in timebases:
            _run_cmd(
                [
                    python,
                    run_summary_script,
                    "--run-dir",
                    str(out_run),
                    "--timebase",
                    timebase,
                ],
                nice=max(0, int(args.nice)),
            )
            print(f"done summary: {timebase}")

    print(f"done rerun: {out_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
