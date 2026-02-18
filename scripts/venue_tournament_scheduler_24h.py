#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Block:
    label: str
    start_hhmm: str  # MST (UTC-7)
    end_hhmm: str  # MST (UTC-7)

    def start_minutes(self) -> int:
        h, m = _parse_hhmm(self.start_hhmm)
        return h * 60 + m

    def end_minutes(self) -> int:
        h, m = _parse_hhmm(self.end_hhmm)
        return h * 60 + m

    def duration_s(self) -> int:
        start = self.start_minutes()
        end = self.end_minutes()
        if end <= start:
            raise ValueError(f"Block end must be after start: {self.label} {self.start_hhmm}-{self.end_hhmm}")
        return int((end - start) * 60)


_MST = timezone(timedelta(hours=-7))


def _parse_hhmm(value: str) -> tuple[int, int]:
    raw = value.strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid hh:mm: {value!r}")
    h = int(parts[0])
    m = int(parts[1])
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f"Invalid hh:mm: {value!r}")
    return h, m


def _dt_for_hhmm(anchor_day: date, *, hhmm: str, day_offset: int) -> datetime:
    h, m = _parse_hhmm(hhmm)
    return datetime(anchor_day.year, anchor_day.month, anchor_day.day, h, m, tzinfo=_MST) + timedelta(days=day_offset)


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _sleep_until(target: datetime) -> None:
    while True:
        now = datetime.now(_MST)
        delta = (target - now).total_seconds()
        if delta <= 0:
            return
        time.sleep(min(30.0, max(0.5, delta)))


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def _allowed_day_name(dt: datetime) -> str:
    return dt.strftime("%a").lower()


def _find_latest_capture_file(out_root: Path) -> Path:
    if not out_root.exists():
        raise FileNotFoundError(f"Capture output root missing: {out_root}")
    candidates = sorted(out_root.glob("*_trades_capture"), key=lambda p: p.name, reverse=True)
    for d in candidates:
        p_gz = d / "trades.jsonl.gz"
        if p_gz.exists():
            return p_gz
        p = d / "trades.jsonl"
        if p.exists():
            return p
    raise FileNotFoundError(f"No trades.jsonl(.gz) found under: {out_root}")


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


def _run_cmd(argv: list[str]) -> None:
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + f"argv={argv}\n"
            + f"exit={proc.returncode}\n"
            + f"stdout:\n{proc.stdout}\n"
            + f"stderr:\n{proc.stderr}\n"
        )


def _schedule_blocks() -> list[Block]:
    # Locked plan from docs/reference/venue-discovery-tournament-spec-02-04-2026.md (§7.1.2).
    return [
        Block(label="utc_boundary_early_asia", start_hhmm="16:00", end_hhmm="18:00"),
        Block(label="asia_prime", start_hhmm="19:00", end_hhmm="21:00"),
        Block(label="eu_open_ramp", start_hhmm="00:45", end_hhmm="01:45"),
        Block(label="eu_active_pre_us", start_hhmm="02:30", end_hhmm="05:30"),
        Block(label="morning_overlap", start_hhmm="06:00", end_hhmm="09:00"),
        Block(label="late_morning", start_hhmm="09:00", end_hhmm="12:00"),
        Block(label="us_afternoon", start_hhmm="13:00", end_hhmm="16:00"),
    ]


def _slice_ranges(block_start: datetime, block_end: datetime, *, slice_minutes: int, step_minutes: int) -> list[tuple[datetime, datetime]]:
    if slice_minutes <= 0 or step_minutes <= 0:
        return []
    out: list[tuple[datetime, datetime]] = []
    t = block_start
    slice_delta = timedelta(minutes=slice_minutes)
    step_delta = timedelta(minutes=step_minutes)
    while t + slice_delta <= block_end:
        out.append((t, t + slice_delta))
        t += step_delta
    return out


def _clamp_slices(
    slices: list[tuple[datetime, datetime]],
    *,
    clamp_start_ms: int,
    clamp_end_ms: int,
) -> list[tuple[datetime, datetime]]:
    if clamp_start_ms <= 0 or clamp_end_ms <= 0 or clamp_end_ms <= clamp_start_ms:
        return slices
    clamp_start = datetime.fromtimestamp(clamp_start_ms / 1000.0, tz=_MST)
    clamp_end = datetime.fromtimestamp(clamp_end_ms / 1000.0, tz=_MST)
    out: list[tuple[datetime, datetime]] = []
    for s0, s1 in slices:
        a = max(s0, clamp_start)
        b = min(s1, clamp_end)
        if b <= a:
            continue
        out.append((a, b))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the venue discovery tournament capture plan over a 24h MST window.")
    parser.add_argument("--symbols", default="BTC,SOL", help="Base symbols for capture (default: BTC,SOL).")
    parser.add_argument("--candidates", default="", help="Candidate ids for capture (default: capture script default list).")
    parser.add_argument("--gzip", action="store_true", help="Gzip capture logs (recommended).")
    parser.add_argument("--out-root", default="logs/venue_tournament_scheduled", help="Root directory for scheduled outputs.")
    parser.add_argument("--diagnostics-root", default="docs/diagnostics/venue_tournament_scheduled", help="Root for tournament reports.")
    parser.add_argument(
        "--hyperliquid-ts-mode",
        choices=["recv", "venue"],
        default="venue",
        help=(
            "How to populate ts_exchange_ms for hyperliquid_perp in the capture logs. "
            "Use 'venue' (default) to keep the Hyperliquid-provided timestamp as ts_exchange_ms; "
            "use 'recv' to force local receive time into ts_exchange_ms."
        ),
    )
    parser.add_argument(
        "--hyperliquid-transport",
        choices=["sdk", "raw"],
        default="sdk",
        help="How to connect for hyperliquid_perp during capture (default: sdk).",
    )
    parser.add_argument(
        "--analysis-phase",
        choices=["inline", "deferred", "none"],
        default="deferred",
        help=(
            "When to run tournament analysis. "
            "'inline' runs analysis immediately after each capture (can cause missed blocks if analysis runs long). "
            "'deferred' (default) captures all blocks first, then runs analysis after the schedule finishes. "
            "'none' captures only (analysis can be run later)."
        ),
    )
    parser.add_argument(
        "--anchor-hhmm",
        default="22:00",
        help="Anchor start time in MST for the 24h pass (default: 22:00).",
    )
    parser.add_argument(
        "--allowed-days",
        default="sat,sun,tue,thu",
        help=(
            "Comma-separated allowed anchor days (default: sat,sun,tue,thu). "
            "Note: with the 22:00 MST anchor, these starts capture most of the next day's windows "
            "(Sat→Sun, Sun→Mon, Tue→Wed, Thu→Fri)."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Run even if anchor day is not in allowed-days.")
    parser.add_argument("--dry-run", action="store_true", help="Print schedule and exit.")
    parser.add_argument("--slice-minutes", type=int, default=60, help="Slice size minutes for per-block analysis (default: 60).")
    parser.add_argument("--slice-step-minutes", type=int, default=30, help="Slice step minutes for per-block analysis (default: 30).")
    parser.add_argument(
        "--analysis-drop-stale",
        action="store_true",
        help=(
            "Enable stale-on-arrival filtering during analysis (recommended). "
            "Drops trades where wire_lag_ms=(ts_recv_ms - ts_venue_ms|ts_exchange_ms) exceeds --analysis-max-wire-lag-ms."
        ),
    )
    parser.add_argument(
        "--no-analysis-drop-stale",
        action="store_false",
        dest="analysis_drop_stale",
        help="Disable stale-on-arrival filtering during analysis.",
    )
    parser.set_defaults(analysis_drop_stale=True)
    parser.add_argument(
        "--analysis-max-wire-lag-ms",
        type=int,
        default=5000,
        help="Max wire lag in ms for stale-on-arrival filtering (default: 5000).",
    )
    parser.add_argument(
        "--write-run-summary",
        action="store_true",
        help="Write run summary files at end of analysis phase (default: enabled; use --no-write-run-summary to disable).",
    )
    parser.add_argument(
        "--no-write-run-summary",
        action="store_false",
        dest="write_run_summary",
        help="Disable writing run summary files at end of analysis phase.",
    )
    parser.set_defaults(write_run_summary=True)
    args = parser.parse_args()

    anchor_h, anchor_m = _parse_hhmm(str(args.anchor_hhmm))
    now = datetime.now(_MST)
    anchor_day = date(now.year, now.month, now.day)
    # Use the most recent occurrence of the anchor time (today). This allows late starts
    # to still run the remaining blocks in the current 24h plan window instead of rolling
    # anchor to tomorrow and waiting ~24h.
    anchor_dt = datetime(anchor_day.year, anchor_day.month, anchor_day.day, anchor_h, anchor_m, tzinfo=_MST)

    allowed = {x.strip().lower() for x in str(args.allowed_days).split(",") if x.strip()}
    anchor_day_name = _allowed_day_name(anchor_dt)
    if not args.force and anchor_day_name not in allowed:
        raise SystemExit(f"Anchor day {anchor_day_name!r} not in allowed-days={sorted(allowed)} (use --force to override).")

    blocks = _schedule_blocks()
    anchor_minutes = anchor_dt.hour * 60 + anchor_dt.minute

    schedule: list[tuple[Block, datetime, datetime]] = []
    for b in blocks:
        start_min = b.start_minutes()
        day_offset = 0 if start_min >= anchor_minutes else 1
        start_dt = _dt_for_hhmm(anchor_day, hhmm=b.start_hhmm, day_offset=day_offset)
        end_dt = _dt_for_hhmm(anchor_day, hhmm=b.end_hhmm, day_offset=day_offset)
        if end_dt <= start_dt:
            raise ValueError(f"Invalid block range: {b.label} {b.start_hhmm}-{b.end_hhmm}")
        schedule.append((b, start_dt, end_dt))

    schedule.sort(key=lambda x: x[1])

    print("Venue tournament scheduler (24h)")
    print(f"anchor: {_format_dt(anchor_dt)} (allowed_days={sorted(allowed)})")
    print(f"out_root: {args.out_root}")
    print(f"diagnostics_root: {args.diagnostics_root}")
    print("")
    for b, start_dt, end_dt in schedule:
        print(f"- {b.label}: {_format_dt(start_dt)} -> {_format_dt(end_dt)}  (duration_s={int((end_dt-start_dt).total_seconds())})")
    print("")
    if args.dry_run:
        return 0

    if datetime.now(_MST) < anchor_dt:
        _sleep_until(anchor_dt)

    run_id = anchor_dt.strftime("%Y%m%d") + f"_{anchor_day_name}"
    run_root = Path(str(args.out_root)) / run_id
    diag_root = Path(str(args.diagnostics_root)) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    diag_root.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    capture_script = str(Path("scripts") / "venue_trades_capture.py")
    tournament_script = str(Path("scripts") / "venue_discovery_tournament_trades.py")
    run_summary_script = str(Path("scripts") / "venue_tournament_run_summary.py")

    captured: list[tuple[Block, datetime, datetime, Path, int, int]] = []

    for block, start_dt, end_dt in schedule:
        now_dt = datetime.now(_MST)
        if now_dt >= end_dt:
            print(f"skip (already ended): {block.label}")
            continue

        if now_dt < start_dt:
            _sleep_until(start_dt)

        capture_start = datetime.now(_MST)
        duration_s = int((end_dt - capture_start).total_seconds())
        if duration_s < 60:
            print(f"skip (too little time remaining): {block.label}")
            continue

        block_root = run_root / block.label
        block_root.mkdir(parents=True, exist_ok=True)

        capture_argv = [
            python,
            capture_script,
            "--symbols",
            str(args.symbols),
            "--candidates",
            str(args.candidates),
            "--duration-s",
            str(duration_s),
            "--out-root",
            str(block_root),
            "--hyperliquid-ts-mode",
            str(args.hyperliquid_ts_mode),
            "--hyperliquid-transport",
            str(args.hyperliquid_transport),
        ]
        if args.gzip:
            capture_argv.append("--gzip")

        print(f"capture start: {block.label} ({duration_s}s)")
        _run_cmd(capture_argv)
        capture_path = _find_latest_capture_file(block_root)
        cap_created_ms, cap_stop_ms = _read_capture_meta_ms(capture_path)
        print(f"capture done: {block.label} input={capture_path}")

        captured.append((block, start_dt, end_dt, capture_path, cap_created_ms, cap_stop_ms))

        if str(args.analysis_phase) == "none":
            continue

        if str(args.analysis_phase) == "deferred":
            # Capture is the priority. Defer analysis so we don't miss subsequent blocks.
            continue

        # All-up block reports + sliced "bookend" reports.
        slices = _slice_ranges(start_dt, end_dt, slice_minutes=int(args.slice_minutes), step_minutes=int(args.slice_step_minutes))
        if not slices:
            slices = [(start_dt, end_dt)]
        slices = _clamp_slices(slices, clamp_start_ms=cap_created_ms, clamp_end_ms=cap_stop_ms)
        if not slices:
            slices = [(start_dt, end_dt)]

        if str(args.analysis_phase) == "inline":
            for timebase in ("exchange", "recv", "exchange_local"):
                # All-up report
                out_all = diag_root / f"{block.label}_tb_{timebase}_all.txt"
                argv = [
                    python,
                    tournament_script,
                    "--input",
                    str(capture_path),
                    "--timebase",
                    timebase,
                    "--out",
                    str(out_all),
                ]
                if bool(args.analysis_drop_stale):
                    argv.extend(["--drop-stale", "--max-wire-lag-ms", str(int(args.analysis_max_wire_lag_ms))])
                _run_cmd(argv)

                # Slices: 60m slices with 30m step (default)
                for idx, (s0, s1) in enumerate(slices, start=1):
                    out_slice = diag_root / f"{block.label}_tb_{timebase}_slice_{idx:02d}.txt"
                    argv = [
                        python,
                        tournament_script,
                        "--input",
                        str(capture_path),
                        "--timebase",
                        timebase,
                        "--start-ms",
                        str(_epoch_ms(s0)),
                        "--end-ms",
                        str(_epoch_ms(s1)),
                        "--out",
                        str(out_slice),
                    ]
                    if bool(args.analysis_drop_stale):
                        argv.extend(["--drop-stale", "--max-wire-lag-ms", str(int(args.analysis_max_wire_lag_ms))])
                    _run_cmd(argv)

            print(f"reports done: {block.label} outputs={diag_root}")

    print(f"done: {run_id}")

    if str(args.analysis_phase) == "deferred" and captured:
        print(f"analysis start (deferred): blocks={len(captured)} outputs={diag_root}")
        for block, start_dt, end_dt, capture_path, cap_created_ms, cap_stop_ms in captured:
            slices = _slice_ranges(
                start_dt,
                end_dt,
                slice_minutes=int(args.slice_minutes),
                step_minutes=int(args.slice_step_minutes),
            )
            if not slices:
                slices = [(start_dt, end_dt)]
            slices = _clamp_slices(slices, clamp_start_ms=cap_created_ms, clamp_end_ms=cap_stop_ms)
            if not slices:
                slices = [(start_dt, end_dt)]

            for timebase in ("exchange", "recv", "exchange_local"):
                out_all = diag_root / f"{block.label}_tb_{timebase}_all.txt"
                argv = [
                    python,
                    tournament_script,
                    "--input",
                    str(capture_path),
                    "--timebase",
                    timebase,
                    "--out",
                    str(out_all),
                ]
                if bool(args.analysis_drop_stale):
                    argv.extend(["--drop-stale", "--max-wire-lag-ms", str(int(args.analysis_max_wire_lag_ms))])
                _run_cmd(argv)
                for idx, (s0, s1) in enumerate(slices, start=1):
                    out_slice = diag_root / f"{block.label}_tb_{timebase}_slice_{idx:02d}.txt"
                    argv = [
                        python,
                        tournament_script,
                        "--input",
                        str(capture_path),
                        "--timebase",
                        timebase,
                        "--start-ms",
                        str(_epoch_ms(s0)),
                        "--end-ms",
                        str(_epoch_ms(s1)),
                        "--out",
                        str(out_slice),
                    ]
                    if bool(args.analysis_drop_stale):
                        argv.extend(["--drop-stale", "--max-wire-lag-ms", str(int(args.analysis_max_wire_lag_ms))])
                    _run_cmd(argv)
            print(f"reports done (deferred): {block.label}")

        print(f"analysis done (deferred): outputs={diag_root}")

    if str(args.analysis_phase) in ("inline", "deferred") and bool(args.write_run_summary):
        print("run summary start")
        for timebase in ("exchange_local", "exchange", "recv"):
            _run_cmd([python, run_summary_script, "--run-dir", str(diag_root), "--timebase", timebase])
        print(f"run summary done: outputs={diag_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
