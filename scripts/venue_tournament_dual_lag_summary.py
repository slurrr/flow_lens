#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SectionSummary:
    base_symbol: str
    market_type: str
    points: dict[str, int]
    dropped: dict[str, int]
    combined: dict[str, float]


_SECTION_RE = re.compile(r"^==\s+(?P<sym>[A-Z0-9]+)\s+/\s+(?P<mkt>spot|perp)\s+==\s*$")
_TIME_HYGIENE_HDR = "Time hygiene (from ts_recv_ms - ts_exchange_ms)"
_DROPS_HDR_PREFIX = "Stale-on-arrival drops (wire_lag_ms > "
_RANK_HDR = "Venue rankings (avg pairwise win rate)"


def _iter_table_rows(lines: list[str], *, start: int, header: str) -> tuple[int, list[str]]:
    i = start
    while i < len(lines) and lines[i].strip() != header:
        i += 1
    if i >= len(lines):
        return start, []
    i += 1  # header line
    if i < len(lines):
        i += 1  # column header
    rows: list[str] = []
    while i < len(lines):
        raw = lines[i].strip()
        if not raw:
            break
        rows.append(raw)
        i += 1
    return i, rows


def _parse_sections(text: str) -> list[SectionSummary]:
    lines = text.splitlines()
    idx = 0
    out: list[SectionSummary] = []

    while idx < len(lines):
        m = _SECTION_RE.match(lines[idx])
        if not m:
            idx += 1
            continue

        base_symbol = m.group("sym")
        market_type = m.group("mkt")
        idx += 1

        points: dict[str, int] = {}
        dropped: dict[str, int] = {}
        combined: dict[str, float] = {}

        # Time hygiene points table
        _, time_rows = _iter_table_rows(lines, start=idx, header=_TIME_HYGIENE_HDR)
        for raw in time_rows:
            parts = raw.split()
            if len(parts) < 2:
                continue
            candidate = parts[0]
            try:
                pts = int(parts[1])
            except ValueError:
                continue
            points[candidate] = pts

        # Drops table header is dynamic (contains threshold)
        i = idx
        while i < len(lines) and not lines[i].startswith(_DROPS_HDR_PREFIX):
            i += 1
        if i < len(lines):
            i += 1  # drops title line
            if i < len(lines):
                i += 1  # column header
            while i < len(lines):
                raw = lines[i].strip()
                if not raw:
                    break
                parts = raw.split()
                if len(parts) >= 2:
                    candidate = parts[0]
                    try:
                        d = int(parts[1])
                    except ValueError:
                        d = 0
                    dropped[candidate] = d
                i += 1

        # Rankings table
        _, rank_rows = _iter_table_rows(lines, start=idx, header=_RANK_HDR)
        for raw in rank_rows:
            parts = raw.split()
            if len(parts) < 2:
                continue
            candidate = parts[0]
            try:
                combined_score = float(parts[1])
            except ValueError:
                continue
            combined[candidate] = combined_score

        out.append(
            SectionSummary(
                base_symbol=base_symbol,
                market_type=market_type,
                points=points,
                dropped=dropped,
                combined=combined,
            )
        )
        idx += 1

    return out


def _run_analyzer(
    *,
    python: str,
    analyzer_path: Path,
    input_path: Path,
    out_path: Path,
    timebase: str,
    max_wire_lag_ms: int,
    extra_args: list[str],
) -> None:
    argv = [
        python,
        str(analyzer_path),
        "--input",
        str(input_path),
        "--timebase",
        timebase,
        "--drop-stale",
        "--max-wire-lag-ms",
        str(max_wire_lag_ms),
        "--out",
        str(out_path),
    ]
    argv.extend(extra_args)
    proc = subprocess.run(argv, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Analyzer failed:\n"
            + f"argv={argv}\n"
            + f"exit={proc.returncode}\n"
            + f"stdout:\n{proc.stdout}\n"
            + f"stderr:\n{proc.stderr}\n"
        )


def _format_summary(
    sections: list[SectionSummary],
    *,
    top_n: int,
) -> str:
    lines: list[str] = []
    for s in sections:
        lines.append(f"{s.base_symbol}/{s.market_type}")

        ranked = sorted(s.combined.items(), key=lambda kv: kv[1], reverse=True)[: max(1, top_n)]
        lines.append("  top: " + ", ".join(f"{c}={v:.3f}" for c, v in ranked))

        offenders: list[tuple[str, float, int, int]] = []
        for cid, pts in s.points.items():
            d = s.dropped.get(cid, 0)
            if pts <= 0:
                continue
            frac = d / pts
            offenders.append((cid, frac, d, pts))
        offenders.sort(key=lambda x: x[1], reverse=True)
        worst = offenders[:3]
        lines.append("  drops: " + ", ".join(f"{cid}={d}/{pts}({frac:.1%})" for cid, frac, d, pts in worst))

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run venue tournament analysis at two wire-lag thresholds (e.g. 2s and 4s) and print a compact summary."
    )
    parser.add_argument("--input", required=True, help="Path to trades capture jsonl(.gz).")
    parser.add_argument("--timebase", default="exchange_local", choices=["exchange", "recv", "exchange_local"])
    parser.add_argument("--thresholds-ms", default="2000,4000", help="Comma-separated max wire lag thresholds (default: 2000,4000).")
    parser.add_argument("--top-n", type=int, default=5, help="Top N venues to show per section (default: 5).")
    parser.add_argument("--out-dir", default="docs/diagnostics", help="Directory to write full reports (default: docs/diagnostics).")
    parser.add_argument(
        "--extra",
        default="",
        help="Extra args passed through to the analyzer, e.g. \"--bucket-ms 100 --return-horizon-ms 800\".",
    )
    args = parser.parse_args()

    input_path = Path(str(args.input))
    out_dir = Path(str(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    analyzer_path = Path("scripts") / "venue_discovery_tournament_trades.py"
    python = sys.executable

    thresholds: list[int] = []
    for part in str(args.thresholds_ms).split(","):
        part = part.strip()
        if not part:
            continue
        thresholds.append(max(0, int(part)))
    if not thresholds:
        raise SystemExit("No thresholds provided.")

    extra_args = shlex.split(str(args.extra)) if str(args.extra).strip() else []

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"venue-tournament-{args.timebase}-wirelag_{'_'.join(str(t) for t in thresholds)}ms-{stamp}.txt"
    _run_analyzer(
        python=python,
        analyzer_path=analyzer_path,
        input_path=input_path,
        out_path=out_path,
        timebase=str(args.timebase),
        max_wire_lag_ms=thresholds[0] if thresholds else 0,
        extra_args=["--wire-lag-thresholds-ms", ",".join(str(t) for t in thresholds), *extra_args],
    )
    text = out_path.read_text(encoding="utf-8", errors="replace")

    # Split by threshold blocks.
    blocks = re.split(r"^=== wire_lag_ms<=\\d+ ===\\s*$", text, flags=re.MULTILINE)
    headers = re.findall(r"^=== wire_lag_ms<=(\\d+) ===\\s*$", text, flags=re.MULTILINE)
    if not headers:
        headers = [str(thresholds[0])]
        blocks = [text]
    # First split chunk is preamble; remaining blocks map to headers.
    content_blocks = blocks[1:] if len(blocks) > 1 else blocks

    for thr_s, block in zip(headers, content_blocks, strict=False):
        sections = _parse_sections(block)
        print(f"=== wire_lag_ms<={thr_s} timebase={args.timebase} out={out_path} ===")
        print(_format_summary(sections, top_n=int(args.top_n)))
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
