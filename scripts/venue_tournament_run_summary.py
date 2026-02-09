#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScoreRow:
    candidate: str
    combined: float
    impulse: float
    transition: float
    calm: float


@dataclass(frozen=True)
class BlockResult:
    block: str
    base_symbol: str
    market_type: str
    rows: list[ScoreRow]


_SECTION_RE = re.compile(r"^==\s+(?P<sym>[A-Z0-9]+)\s+/\s+(?P<mkt>spot|perp)\s+==\s*$")
_RANK_HDR = "Venue rankings (avg pairwise win rate)"
_RANK_ROW_RE = re.compile(
    r"^(?P<cand>\S+)\s+(?P<combined>[0-9.]+)\s+(?P<impulse>[0-9.]+)\s+(?P<transition>[0-9.]+)\s+(?P<calm>[0-9.]+)\s*$"
)


def _parse_report(path: Path) -> list[tuple[tuple[str, str], list[ScoreRow]]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[tuple[tuple[str, str], list[ScoreRow]]] = []
    cur: tuple[str, str] | None = None
    in_rank = False
    cur_rows: list[ScoreRow] = []

    def flush() -> None:
        nonlocal cur, cur_rows
        if cur is not None and cur_rows:
            out.append((cur, cur_rows))
        cur = None
        cur_rows = []

    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            flush()
            cur = (m.group("sym"), m.group("mkt"))
            in_rank = False
            continue
        if line.strip() == _RANK_HDR:
            in_rank = True
            continue
        if not in_rank or cur is None:
            continue
        raw = line.strip()
        if not raw:
            in_rank = False
            continue
        if raw.startswith("weights:"):
            in_rank = False
            continue
        m2 = _RANK_ROW_RE.match(raw)
        if not m2:
            continue
        cur_rows.append(
            ScoreRow(
                candidate=m2.group("cand"),
                combined=float(m2.group("combined")),
                impulse=float(m2.group("impulse")),
                transition=float(m2.group("transition")),
                calm=float(m2.group("calm")),
            )
        )

    flush()
    return out


def _block_from_filename(path: Path) -> str:
    # "<block>_tb_<timebase>_all.txt"
    name = path.name
    if "_tb_" in name:
        return name.split("_tb_")[0]
    return name


def _fmt_pct(value: float) -> str:
    return f"{value*100:.0f}%"


_BLOCK_ORDER = {
    "utc_boundary_early_asia": 10,
    "asia_prime": 20,
    "eu_open_ramp": 30,
    "eu_active_pre_us": 40,
    "morning_overlap": 50,
    "late_morning": 60,
    "us_afternoon": 70,
}


def _block_sort_key(name: str) -> tuple[int, str]:
    return (_BLOCK_ORDER.get(name, 999), name)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def _row(cols: list[tuple[str, int]], *, sep: str = "  ") -> str:
    parts: list[str] = []
    for value, width in cols:
        parts.append(_truncate(value, width).ljust(width))
    return sep.join(parts).rstrip()


def _rule(width: int) -> str:
    return "-" * width


def summarize_run(run_dir: Path, *, timebase: str, top_n: int) -> str:
    files = sorted(run_dir.glob(f"*_tb_{timebase}_all.txt"))
    if not files:
        raise FileNotFoundError(f"No '*_tb_{timebase}_all.txt' files under {run_dir}")

    results: list[BlockResult] = []
    for f in files:
        block = _block_from_filename(f)
        for (sym, mkt), rows in _parse_report(f):
            results.append(BlockResult(block=block, base_symbol=sym, market_type=mkt, rows=rows))

    # Group by section (BTC/perp, etc.)
    grouped: dict[tuple[str, str], list[BlockResult]] = {}
    for r in results:
        grouped.setdefault((r.base_symbol, r.market_type), []).append(r)

    lines: list[str] = []
    lines.append("Venue Tournament Run Summary")
    lines.append(f"run_dir: {run_dir}")
    lines.append(f"timebase: {timebase}")
    lines.append(f"blocks: {len(files)}")
    lines.append("")

    for (sym, mkt) in sorted(grouped.keys()):
        blocks = grouped[(sym, mkt)]
        # candidate -> list of combined scores across blocks
        scores: dict[str, list[float]] = {}
        impulses: dict[str, list[float]] = {}
        calms: dict[str, list[float]] = {}
        top1: dict[str, int] = {}
        top2: dict[str, int] = {}
        margins_vs_2: list[float] = []
        impulse_top1: dict[str, int] = {}
        impulse_top2: dict[str, int] = {}

        for b in blocks:
            ranked = sorted(b.rows, key=lambda r: r.combined, reverse=True)
            if ranked:
                top1[ranked[0].candidate] = top1.get(ranked[0].candidate, 0) + 1
            if len(ranked) >= 2:
                margins_vs_2.append(ranked[0].combined - ranked[1].combined)
            if len(ranked) >= 2:
                top2[ranked[0].candidate] = top2.get(ranked[0].candidate, 0) + 1
                top2[ranked[1].candidate] = top2.get(ranked[1].candidate, 0) + 1

            ranked_imp = sorted(b.rows, key=lambda r: r.impulse, reverse=True)
            if ranked_imp:
                impulse_top1[ranked_imp[0].candidate] = impulse_top1.get(ranked_imp[0].candidate, 0) + 1
            if len(ranked_imp) >= 2:
                impulse_top2[ranked_imp[0].candidate] = impulse_top2.get(ranked_imp[0].candidate, 0) + 1
                impulse_top2[ranked_imp[1].candidate] = impulse_top2.get(ranked_imp[1].candidate, 0) + 1

            for row in b.rows:
                scores.setdefault(row.candidate, []).append(row.combined)
                impulses.setdefault(row.candidate, []).append(row.impulse)
                calms.setdefault(row.candidate, []).append(row.calm)

        avg = {c: sum(v) / len(v) for c, v in scores.items()}
        stdev = {c: (statistics.pstdev(v) if len(v) > 1 else 0.0) for c, v in scores.items()}
        imp_avg = {c: sum(v) / len(v) for c, v in impulses.items()}
        calm_avg = {c: sum(v) / len(v) for c, v in calms.items()}

        ranked = sorted(avg.items(), key=lambda kv: kv[1], reverse=True)
        best = ranked[: max(1, top_n)]

        lines.append(f"== {sym} / {mkt} ==")
        if len(ranked) >= 2:
            margin = ranked[0][1] - ranked[1][1]
            lines.append(f"margin_vs_#2: {margin:.3f}")
        if margins_vs_2:
            med_margin = statistics.median(margins_vs_2)
            lines.append(f"median_margin_vs_#2_by_block: {med_margin:.3f}")
            lines.append(f"top1_unique: {len(top1)}")

        # Overall table
        overall_cols = [
            ("candidate", 18),
            ("avg", 6),
            ("σ", 6),
            ("imp", 6),
            ("calm", 6),
            ("top1", 10),
            ("top2", 10),
        ]
        lines.append(_row(overall_cols))
        lines.append(_rule(len(_row(overall_cols))))
        for c, v in best:
            t1 = top1.get(c, 0)
            t2 = top2.get(c, 0)
            lines.append(
                _row(
                    [
                        (c, 18),
                        (f"{v:.3f}", 6),
                        (f"{stdev[c]:.3f}", 6),
                        (f"{imp_avg.get(c, 0.0):.3f}", 6),
                        (f"{calm_avg.get(c, 0.0):.3f}", 6),
                        (f"{t1}/{len(blocks)} {_fmt_pct(t1/len(blocks))}", 10),
                        (f"{t2}/{len(blocks)} {_fmt_pct(t2/len(blocks))}", 10),
                    ]
                )
            )

        lines.append("")

        # Per-block top2 table (combined)
        per_block_cols = [
            ("block", 20),
            ("top1", 18),
            ("s1", 6),
            ("top2", 18),
            ("s2", 6),
            ("margin", 6),
        ]
        lines.append("Per-block top2 (combined)")
        lines.append(_row(per_block_cols))
        lines.append(_rule(len(_row(per_block_cols))))
        for b in sorted(blocks, key=lambda br: _block_sort_key(br.block)):
            ranked_rows = sorted(b.rows, key=lambda r: r.combined, reverse=True)
            if not ranked_rows:
                continue
            if len(ranked_rows) == 1:
                a = ranked_rows[0]
                lines.append(
                    _row(
                        [
                            (b.block, 20),
                            (a.candidate, 18),
                            (f"{a.combined:.3f}", 6),
                            ("-", 18),
                            ("-", 6),
                            ("-", 6),
                        ]
                    )
                )
                continue
            a = ranked_rows[0]
            b2 = ranked_rows[1]
            lines.append(
                _row(
                    [
                        (b.block, 20),
                        (a.candidate, 18),
                        (f"{a.combined:.3f}", 6),
                        (b2.candidate, 18),
                        (f"{b2.combined:.3f}", 6),
                        (f"{a.combined - b2.combined:.3f}", 6),
                    ]
                )
            )
        lines.append("")

        # Impulse-focused view (this is what you want for "who leads when price moves")
        lines.append("Impulse view (who leads during impulse windows)")
        imp_ranked = sorted(imp_avg.items(), key=lambda kv: kv[1], reverse=True)
        imp_best = imp_ranked[: max(1, top_n)]
        imp_cols = [
            ("candidate", 18),
            ("imp", 6),
            ("avg", 6),
            ("calm", 6),
            ("imp#1", 10),
            ("imp#2", 10),
        ]
        lines.append(_row(imp_cols))
        lines.append(_rule(len(_row(imp_cols))))
        for c, v in imp_best:
            it1 = impulse_top1.get(c, 0)
            it2 = impulse_top2.get(c, 0)
            lines.append(
                _row(
                    [
                        (c, 18),
                        (f"{v:.3f}", 6),
                        (f"{avg.get(c, 0.0):.3f}", 6),
                        (f"{calm_avg.get(c, 0.0):.3f}", 6),
                        (f"{it1}/{len(blocks)} {_fmt_pct(it1/len(blocks))}", 10),
                        (f"{it2}/{len(blocks)} {_fmt_pct(it2/len(blocks))}", 10),
                    ]
                )
            )
        lines.append("")

        lines.append("Per-block top2 (impulse)")
        lines.append(_row(per_block_cols))
        lines.append(_rule(len(_row(per_block_cols))))
        for br in sorted(blocks, key=lambda r: _block_sort_key(r.block)):
            ranked_rows = sorted(br.rows, key=lambda r: r.impulse, reverse=True)
            if not ranked_rows:
                continue
            if len(ranked_rows) == 1:
                a = ranked_rows[0]
                lines.append(
                    _row(
                        [
                            (br.block, 20),
                            (a.candidate, 18),
                            (f"{a.impulse:.3f}", 6),
                            ("-", 18),
                            ("-", 6),
                            ("-", 6),
                        ]
                    )
                )
                continue
            a = ranked_rows[0]
            b2 = ranked_rows[1]
            lines.append(
                _row(
                    [
                        (br.block, 20),
                        (a.candidate, 18),
                        (f"{a.impulse:.3f}", 6),
                        (b2.candidate, 18),
                        (f"{b2.impulse:.3f}", 6),
                        (f"{a.impulse - b2.impulse:.3f}", 6),
                    ]
                )
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a scheduled venue tournament run directory into a single report.")
    parser.add_argument("--run-dir", required=True, help="Run diagnostics directory, e.g. docs/diagnostics/venue_tournament_scheduled/20260207_sat")
    parser.add_argument("--timebase", default="exchange_local", choices=["exchange_local", "exchange", "recv"])
    parser.add_argument("--top-n", type=int, default=6, help="Top N candidates per section (default: 6).")
    parser.add_argument("--out", default="", help="Output file path. Default writes into run-dir.")
    args = parser.parse_args()

    run_dir = Path(str(args.run_dir))
    text = summarize_run(run_dir, timebase=str(args.timebase), top_n=int(args.top_n))

    if str(args.out).strip():
        out_path = Path(str(args.out))
    else:
        out_path = run_dir / f"run_summary_tb_{args.timebase}.txt"
    out_path.write_text(text, encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
