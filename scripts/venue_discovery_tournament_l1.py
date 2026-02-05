#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

Regime = Literal["impulse", "transition", "calm"]
ScoreKey = Literal["impulse", "transition", "calm", "combined"]
Timebase = Literal["exchange", "recv", "exchange_local"]


@dataclass(frozen=True)
class CaptureMeta:
    created_at_ms: int
    stop_at_ms: int
    symbols: list[str]
    candidates: list[str]


@dataclass
class SeriesPoint:
    mid_px: float
    ts_exchange_ms: int
    ts_recv_ms: int


@dataclass
class VenueSeries:
    candidate_id: str
    venue: str
    market_type: str
    base_symbol: str
    points: list[SeriesPoint]
    recv_minus_exchange_ms: list[float]


@dataclass(frozen=True)
class EventWindow:
    regime: Regime
    t0_bucket: int
    start_bucket: int
    end_bucket: int


@dataclass
class PairCounts:
    contests: int = 0
    a_wins: int = 0
    b_wins: int = 0
    ties: int = 0
    no_contest: int = 0
    lead_times_ms_a: list[int] = field(default_factory=list)
    lead_times_ms_b: list[int] = field(default_factory=list)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _open_maybe_gz(path: Path) -> Iterable[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from f
        return
    with path.open("rt", encoding="utf-8") as f:
        yield from f


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 1:
        return sorted_values[-1]
    idx = int(round(pct * (len(sorted_values) - 1)))
    return sorted_values[idx]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    try:
        return statistics.median(values)
    except statistics.StatisticsError:
        return None


def _log_return(p_new: float, p_old: float) -> float:
    if p_new <= 0 or p_old <= 0:
        return 0.0
    return math.log(p_new / p_old)


def _weights(value: str) -> dict[Regime, float]:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("weights must be comma-separated: impulse,transition,calm")
    w = [float(x) for x in parts]
    total = sum(w)
    if total <= 0:
        raise ValueError("weights must sum > 0")
    return {"impulse": w[0] / total, "transition": w[1] / total, "calm": w[2] / total}


def _parse_capture(
    path: Path,
) -> tuple[CaptureMeta, dict[tuple[str, str], VenueSeries]]:
    meta: CaptureMeta | None = None
    by_key: dict[tuple[str, str], VenueSeries] = {}

    for lineno, line in enumerate(_open_maybe_gz(path), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if meta is None:
            m = payload.get("_meta")
            if isinstance(m, dict) and m.get("type") == "venue_l1_prefilter":
                created_at_ms = int(m.get("created_at_ms") or 0)
                stop_at_ms = int(m.get("stop_at_ms") or 0)
                symbols = [str(x) for x in (m.get("symbols") or []) if isinstance(x, str)]
                candidates = [str(x) for x in (m.get("candidates") or []) if isinstance(x, str)]
                meta = CaptureMeta(
                    created_at_ms=created_at_ms,
                    stop_at_ms=stop_at_ms,
                    symbols=symbols,
                    candidates=candidates,
                )
            continue

        # BboUpdate dict
        candidate_id = payload.get("candidate_id")
        venue = payload.get("venue")
        market_type = payload.get("market_type")
        base_symbol = payload.get("base_symbol")
        ts_exchange_ms = payload.get("ts_exchange_ms")
        ts_recv_ms = payload.get("ts_recv_ms")
        bid_px = payload.get("bid_px")
        ask_px = payload.get("ask_px")
        if not isinstance(candidate_id, str) or not isinstance(venue, str) or not isinstance(market_type, str) or not isinstance(base_symbol, str):
            continue
        if not isinstance(ts_exchange_ms, int) or not isinstance(ts_recv_ms, int):
            continue
        if not isinstance(bid_px, (int, float)) or not isinstance(ask_px, (int, float)):
            continue
        if bid_px <= 0 or ask_px <= 0:
            continue

        mid = 0.5 * (float(bid_px) + float(ask_px))
        if mid <= 0:
            continue
        key = (candidate_id, base_symbol)
        s = by_key.get(key)
        if s is None:
            s = VenueSeries(
                candidate_id=candidate_id,
                venue=venue,
                market_type=market_type,
                base_symbol=base_symbol,
                points=[],
                recv_minus_exchange_ms=[],
            )
            by_key[key] = s
        s.points.append(SeriesPoint(mid_px=mid, ts_exchange_ms=ts_exchange_ms, ts_recv_ms=ts_recv_ms))
        s.recv_minus_exchange_ms.append(float(ts_recv_ms - ts_exchange_ms))

        if lineno % 2_000_000 == 0:
            # safety: avoid silent huge runs without feedback if used interactively
            pass

    if meta is None:
        raise ValueError(f"Capture file missing meta line: {path}")

    # Sort points per series; keep last per bucket (de-dupe heavy feeds).
    for s in by_key.values():
        s.points.sort(key=lambda p: p.ts_exchange_ms)

    return meta, by_key


def _series_time_offset_ms(recv_minus_exchange_ms: list[float]) -> float:
    if not recv_minus_exchange_ms:
        return 0.0
    try:
        return float(statistics.median(recv_minus_exchange_ms))
    except statistics.StatisticsError:
        return 0.0


def _effective_ts_ms(
    p: SeriesPoint,
    *,
    timebase: Timebase,
    offset_ms: float,
) -> int:
    if timebase == "exchange":
        return p.ts_exchange_ms
    if timebase == "recv":
        return p.ts_recv_ms
    return int(p.ts_exchange_ms + offset_ms)


def _build_aligned_arrays(
    series: VenueSeries,
    *,
    start_bucket: int,
    end_bucket: int,
    max_stale_buckets: int,
    bucket_ms: int,
    offset_ms: float,
    timebase: Timebase,
) -> tuple[list[float | None], list[int]]:
    n = end_bucket - start_bucket + 1
    px: list[float | None] = [None] * n
    age: list[int] = [max_stale_buckets + 1] * n

    point_by_bucket: dict[int, float] = {}
    for p in series.points:
        effective_ms = _effective_ts_ms(p, timebase=timebase, offset_ms=offset_ms)
        b = effective_ms // bucket_ms
        point_by_bucket[b] = p.mid_px

    last_px: float | None = None
    last_seen_idx: int | None = None
    for i in range(n):
        b = start_bucket + i
        v = point_by_bucket.get(b)
        if v is not None and v > 0:
            last_px = v
            last_seen_idx = i
            px[i] = v
            age[i] = 0
            continue

        if last_px is None or last_seen_idx is None:
            continue

        gap = i - last_seen_idx
        age[i] = gap
        if gap <= max_stale_buckets:
            px[i] = last_px

    return px, age


def _jitter_p95_ms(recv_minus_exchange_ms: list[float]) -> float:
    if not recv_minus_exchange_ms:
        return 0.0
    try:
        center = statistics.median(recv_minus_exchange_ms)
    except statistics.StatisticsError:
        center = 0.0
    deviations = [abs(x - center) for x in recv_minus_exchange_ms]
    deviations.sort()
    return _pct(deviations, 0.95)


def _composite_ref(
    series_arrays: dict[str, list[float | None]],
    *,
    exclude_candidate_ids: set[str],
) -> list[float | None]:
    any_series = next(iter(series_arrays.values()))
    n = len(any_series)
    ref: list[float | None] = [None] * n
    for i in range(n):
        values: list[float] = []
        for cid, arr in series_arrays.items():
            if cid in exclude_candidate_ids:
                continue
            v = arr[i]
            if isinstance(v, float) and v > 0:
                values.append(v)
        ref[i] = _median(values)
    return ref


def _compute_returns(
    px: list[float | None],
    *,
    horizon_buckets: int,
) -> list[float | None]:
    n = len(px)
    out: list[float | None] = [None] * n
    for i in range(horizon_buckets, n):
        p0 = px[i - horizon_buckets]
        p1 = px[i]
        if not isinstance(p0, float) or not isinstance(p1, float):
            continue
        out[i] = _log_return(p1, p0)
    return out


def _extract_events(
    r_ref: list[float | None],
    *,
    impulse_q: float,
    transition_q: float,
    micro_q: float,
    cooldown_buckets: int,
    pre_buckets: int,
    post_buckets: int,
    calm_count: int,
) -> tuple[list[EventWindow], dict[str, float]]:
    abs_r = [abs(x) for x in r_ref if isinstance(x, float)]
    abs_r.sort()
    if not abs_r:
        return [], {"impulse_thr": 0.0, "transition_thr": 0.0, "micro_thr": 0.0}

    impulse_thr = _pct(abs_r, impulse_q)
    transition_thr = _pct(abs_r, transition_q)
    micro_thr = _pct(abs_r, micro_q)

    events: list[EventWindow] = []
    last_event_t: int | None = None

    def can_place(t: int) -> bool:
        if last_event_t is None:
            return True
        return (t - last_event_t) >= cooldown_buckets

    # Impulse events: threshold |r|
    for t, r in enumerate(r_ref):
        if not isinstance(r, float):
            continue
        if abs(r) < impulse_thr:
            continue
        if not can_place(t):
            continue
        events.append(EventWindow(regime="impulse", t0_bucket=t, start_bucket=max(0, t - pre_buckets), end_bucket=t + post_buckets))
        last_event_t = t

    # Transition events: sign flip + |r| threshold
    for t in range(1, len(r_ref)):
        r0 = r_ref[t - 1]
        r1 = r_ref[t]
        if not isinstance(r0, float) or not isinstance(r1, float):
            continue
        if r0 == 0 or r1 == 0:
            continue
        if (r0 > 0) == (r1 > 0):
            continue
        if abs(r1) < transition_thr:
            continue
        if not can_place(t):
            continue
        events.append(EventWindow(regime="transition", t0_bucket=t, start_bucket=max(0, t - pre_buckets), end_bucket=t + post_buckets))
        last_event_t = t

    # Calm/chop control windows: choose low-activity segments (|r| below micro_thr) away from other events.
    if calm_count > 0:
        selected = 0
        span = max(1, len(r_ref) // max(1, calm_count * 3))
        t = 0
        while t < len(r_ref) and selected < calm_count:
            r = r_ref[t]
            if isinstance(r, float) and abs(r) <= micro_thr and can_place(t):
                events.append(
                    EventWindow(regime="calm", t0_bucket=t, start_bucket=max(0, t - pre_buckets), end_bucket=t + post_buckets)
                )
                last_event_t = t
                selected += 1
                t += cooldown_buckets
                continue
            t += span

    events.sort(key=lambda e: (e.start_bucket, e.t0_bucket))
    return events, {"impulse_thr": impulse_thr, "transition_thr": transition_thr, "micro_thr": micro_thr}


def _first_crossing(
    px: list[float | None],
    age: list[int],
    *,
    t0: int,
    start: int,
    end: int,
    dir_sign: int,
    required_move: float,
    cross_frac: float,
    max_stale_buckets: int,
) -> int | None:
    if t0 < 0 or t0 >= len(px):
        return None
    p0 = px[t0]
    if not isinstance(p0, float):
        return None
    if age[t0] > max_stale_buckets:
        return None
    threshold = required_move * cross_frac
    if threshold <= 0:
        return None
    for t in range(max(t0, start), min(end, len(px) - 1) + 1):
        p = px[t]
        if not isinstance(p, float):
            continue
        if age[t] > max_stale_buckets:
            continue
        move = _log_return(p, p0) * float(dir_sign)
        if move >= threshold:
            return t
    return None


def _pairwise_tournament(
    *,
    bucket_ms: int,
    group_key: tuple[str, str],
    candidates: list[str],
    series_px: dict[str, list[float | None]],
    series_age: dict[str, list[int]],
    series_jitter_p95: dict[str, float],
    ref_px: list[float | None],
    events: list[EventWindow],
    pre_buckets: int,
    post_buckets: int,
    dir_horizon_buckets: int,
    jitter_guard_ms_default: int,
    confirm_primary_buckets: int,
    confirm_secondary_buckets: int,
    cross_frac: float,
    max_stale_buckets: int,
) -> dict[tuple[str, str], dict[Regime, PairCounts]]:
    # Results indexed by ordered pair (A,B)
    results: dict[tuple[str, str], dict[Regime, PairCounts]] = {}

    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            results[(a, b)] = {"impulse": PairCounts(), "transition": PairCounts(), "calm": PairCounts()}

    # Precompute composite returns for direction/magnitude
    r_ref = _compute_returns(ref_px, horizon_buckets=dir_horizon_buckets)

    for e in events:
        t0 = e.t0_bucket
        # Direction from reference horizon move
        if t0 + dir_horizon_buckets >= len(ref_px):
            continue
        p0 = ref_px[t0]
        p1 = ref_px[t0 + dir_horizon_buckets]
        if not isinstance(p0, float) or not isinstance(p1, float):
            continue
        composite_move = _log_return(p1, p0)
        if composite_move == 0:
            continue
        dir_sign = 1 if composite_move > 0 else -1
        required_move = abs(composite_move)
        if required_move <= 0:
            continue

        # We still want a minimum event magnitude; otherwise calm windows become noise contests.
        r_now = r_ref[t0 + dir_horizon_buckets]
        if not isinstance(r_now, float):
            continue
        if e.regime in {"impulse", "transition"} and abs(r_now) <= 0:
            continue

        for i, a in enumerate(candidates):
            for b in candidates[i + 1 :]:
                key = (a, b)
                rc = results[key][e.regime]

                px_a = series_px.get(a)
                px_b = series_px.get(b)
                age_a = series_age.get(a)
                age_b = series_age.get(b)
                if px_a is None or px_b is None or age_a is None or age_b is None:
                    rc.no_contest += 1
                    continue

                t_a = _first_crossing(
                    px_a,
                    age_a,
                    t0=t0,
                    start=e.start_bucket,
                    end=e.end_bucket,
                    dir_sign=dir_sign,
                    required_move=required_move,
                    cross_frac=cross_frac,
                    max_stale_buckets=max_stale_buckets,
                )
                t_b = _first_crossing(
                    px_b,
                    age_b,
                    t0=t0,
                    start=e.start_bucket,
                    end=e.end_bucket,
                    dir_sign=dir_sign,
                    required_move=required_move,
                    cross_frac=cross_frac,
                    max_stale_buckets=max_stale_buckets,
                )

                if t_a is None or t_b is None:
                    rc.no_contest += 1
                    continue

                rc.contests += 1
                jitter_guard_ms = max(
                    jitter_guard_ms_default,
                    int(series_jitter_p95.get(a, 0.0)),
                    int(series_jitter_p95.get(b, 0.0)),
                )
                jitter_guard_buckets = max(0, jitter_guard_ms // bucket_ms)

                dt = t_b - t_a
                if dt == 0:
                    rc.ties += 1
                    continue

                # Confirmation horizon: primary + secondary (reported via the same counters for now).
                confirm_buckets = confirm_primary_buckets if e.regime != "calm" else confirm_secondary_buckets

                if dt > 0:
                    # A earlier than B
                    if (t_a + jitter_guard_buckets) < t_b and dt <= confirm_buckets:
                        rc.a_wins += 1
                        rc.lead_times_ms_a.append(dt * bucket_ms)
                    else:
                        rc.ties += 1
                else:
                    # B earlier than A
                    if (t_b + jitter_guard_buckets) < t_a and (-dt) <= confirm_buckets:
                        rc.b_wins += 1
                        rc.lead_times_ms_b.append((-dt) * bucket_ms)
                    else:
                        rc.ties += 1

    return results


def _venue_scores(
    candidates: list[str],
    pair_results: dict[tuple[str, str], dict[Regime, PairCounts]],
    *,
    weights: dict[Regime, float],
) -> dict[str, dict[ScoreKey, float]]:
    # Per venue: average win-rate vs others (by regime), then weighted.
    scores: dict[str, dict[ScoreKey, float]] = {
        c: {"impulse": 0.0, "transition": 0.0, "calm": 0.0, "combined": 0.0} for c in candidates
    }
    denom: dict[str, dict[Regime, int]] = {c: {"impulse": 0, "transition": 0, "calm": 0} for c in candidates}

    for (a, b), by_regime in pair_results.items():
        for regime, c in by_regime.items():
            if c.contests <= 0:
                continue
            # Symmetric win rates
            scores[a][regime] += c.a_wins / c.contests
            scores[b][regime] += c.b_wins / c.contests
            denom[a][regime] += 1
            denom[b][regime] += 1

    for v in candidates:
        for regime in ("impulse", "transition", "calm"):
            d = denom[v][regime]
            if d > 0:
                scores[v][regime] /= float(d)

    for v in candidates:
        combined = 0.0
        for regime, w in weights.items():
            combined += w * scores[v][regime]
        scores[v]["combined"] = combined

    return scores


def _format_pair_table(
    candidates: list[str],
    pair_results: dict[tuple[str, str], dict[Regime, PairCounts]],
    *,
    regime: Regime,
) -> str:
    lines: list[str] = []
    lines.append(f"Pairwise results ({regime})")
    lines.append("A                     B                     contests  A_wins  B_wins  ties   med_lead_ms(A)  med_lead_ms(B)")
    for (a, b), by_regime in sorted(pair_results.items()):
        c = by_regime[regime]
        if c.contests <= 0:
            continue
        med_a = int(statistics.median(c.lead_times_ms_a)) if c.lead_times_ms_a else 0
        med_b = int(statistics.median(c.lead_times_ms_b)) if c.lead_times_ms_b else 0
        lines.append(
            f"{a[:22].ljust(22)}  {b[:22].ljust(22)}  {str(c.contests).rjust(8)}  "
            f"{str(c.a_wins).rjust(5)}  {str(c.b_wins).rjust(5)}  {str(c.ties).rjust(4)}  "
            f"{str(med_a).rjust(13)}  {str(med_b).rjust(13)}"
        )
    lines.append("")
    return "\n".join(lines)


def _format_rankings(
    candidates: list[str],
    scores: dict[str, dict[ScoreKey, float]],
    *,
    weights: dict[Regime, float],
) -> str:
    lines: list[str] = []
    lines.append("Venue rankings (win-rate avg vs others)")
    lines.append("candidate                combined   impulse   transition   calm")
    ranked = sorted(candidates, key=lambda v: float(scores[v]["combined"]), reverse=True)
    for v in ranked:
        s = scores[v]
        lines.append(
            f"{v[:22].ljust(22)}  {s['combined']:8.3f}  {s['impulse']:7.3f}  {s['transition']:10.3f}  {s['calm']:6.3f}"
        )
    lines.append("")
    lines.append(f"weights: impulse={weights['impulse']:.2f}, transition={weights['transition']:.2f}, calm={weights['calm']:.2f}")
    lines.append("")
    return "\n".join(lines)


def _format_time_hygiene(
    candidates: list[str],
    offsets_ms: dict[str, float],
    jitter_p95_ms: dict[str, float],
    series_points: dict[str, int],
) -> str:
    lines: list[str] = []
    lines.append("Time hygiene (from ts_recv_ms - ts_exchange_ms)")
    lines.append("candidate                points   med_offset_ms   jitter_p95_ms")
    for cid in sorted(candidates):
        lines.append(
            f"{cid[:22].ljust(22)}  {str(series_points.get(cid, 0)).rjust(6)}  {offsets_ms.get(cid, 0.0):13.0f}  {jitter_p95_ms.get(cid, 0.0):12.0f}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pairwise venue discovery tournament (L1 proxy): consume multi-venue BBO capture and compute regime-windowed lead scoring."
    )
    parser.add_argument("--input", required=True, help="Path to a venue_l1_prefilter capture log (jsonl or jsonl.gz).")
    parser.add_argument("--bucket-ms", type=int, default=200, help="Time bucket size in milliseconds (default: 200).")
    parser.add_argument("--return-horizon-ms", type=int, default=1000, help="Return horizon for event extraction (default: 1000).")
    parser.add_argument("--dir-horizon-ms", type=int, default=1000, help="Direction horizon for composite move (default: 1000).")
    parser.add_argument("--pre-s", type=float, default=2.0, help="Event pre-window seconds (default: 2.0).")
    parser.add_argument("--post-s", type=float, default=8.0, help="Event post-window seconds (default: 8.0).")
    parser.add_argument("--cooldown-s", type=float, default=4.0, help="Cooldown between extracted events (default: 4.0).")
    parser.add_argument("--impulse-q", type=float, default=0.95, help="Impulse threshold quantile on |r_ref| (default: 0.95).")
    parser.add_argument("--transition-q", type=float, default=0.85, help="Transition threshold quantile on |r_ref| (default: 0.85).")
    parser.add_argument("--micro-q", type=float, default=0.70, help="Calm/chop micro threshold quantile on |r_ref| (default: 0.70).")
    parser.add_argument("--calm-count", type=int, default=20, help="Number of calm/chop control windows to sample (default: 20).")
    parser.add_argument("--cross-frac", type=float, default=0.4, help="First-crossing fraction of composite move (default: 0.4).")
    parser.add_argument(
        "--jitter-guard-ms",
        type=int,
        default=250,
        help="Default jitter guard in ms (final guard=max(default, p95_jitter(A), p95_jitter(B)) (default: 250).",
    )
    parser.add_argument("--confirm-primary-s", type=float, default=2.0, help="Primary confirm horizon seconds (default: 2.0).")
    parser.add_argument("--confirm-secondary-s", type=float, default=4.0, help="Secondary confirm horizon seconds (default: 4.0).")
    parser.add_argument(
        "--max-stale-s",
        type=float,
        default=1.0,
        help="Max acceptable staleness (fill-forward age) for a sample in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--weights",
        default="0.60,0.30,0.10",
        help="Regime weights impulse,transition,calm (default: 0.60,0.30,0.10).",
    )
    parser.add_argument(
        "--exclude-ref",
        default="upbit_spot",
        help="Comma-separated candidate_ids to exclude from composite reference price (default: upbit_spot).",
    )
    parser.add_argument(
        "--timebase",
        choices=["exchange", "recv", "exchange_local"],
        default="exchange",
        help=(
            "Timebase for bucketing/alignment: exchange (default), recv, or exchange_local "
            "(exchange timestamp adjusted by median(recv-exchange) per venue)."
        ),
    )
    parser.add_argument("--out", default="", help="Output path (txt). Default writes to docs/diagnostics with timestamp.")
    args = parser.parse_args()

    input_path = Path(args.input)
    bucket_ms = max(50, int(args.bucket_ms))
    horizon_buckets = max(1, int(round(args.return_horizon_ms / bucket_ms)))
    dir_horizon_buckets = max(1, int(round(args.dir_horizon_ms / bucket_ms)))
    pre_buckets = max(0, int(round(float(args.pre_s) * 1000 / bucket_ms)))
    post_buckets = max(1, int(round(float(args.post_s) * 1000 / bucket_ms)))
    cooldown_buckets = max(1, int(round(float(args.cooldown_s) * 1000 / bucket_ms)))
    confirm_primary_buckets = max(1, int(round(float(args.confirm_primary_s) * 1000 / bucket_ms)))
    confirm_secondary_buckets = max(1, int(round(float(args.confirm_secondary_s) * 1000 / bucket_ms)))
    max_stale_buckets = max(0, int(round(float(args.max_stale_s) * 1000 / bucket_ms)))
    weights = _weights(str(args.weights))
    exclude_ref = {x.strip() for x in str(args.exclude_ref).split(",") if x.strip()}
    timebase_str = str(args.timebase)
    if timebase_str not in ("exchange", "recv", "exchange_local"):
        raise ValueError(f"Invalid timebase: {timebase_str!r}")
    timebase: Timebase = timebase_str

    meta, by_key = _parse_capture(input_path)

    # Group series by (base_symbol, market_type)
    groups: dict[tuple[str, str], list[VenueSeries]] = defaultdict(list)
    for s in by_key.values():
        groups[(s.base_symbol, s.market_type)].append(s)

    report_lines: list[str] = []
    report_lines.append("Venue Discovery Tournament (L1 proxy)")
    report_lines.append(f"input: {input_path}")
    if meta.created_at_ms > 0 and meta.stop_at_ms > meta.created_at_ms:
        report_lines.append(f"capture_duration_s={(meta.stop_at_ms - meta.created_at_ms) / 1000.0:.1f}")
    report_lines.append(f"bucket_ms={bucket_ms} horizon_ms={args.return_horizon_ms} dir_horizon_ms={args.dir_horizon_ms}")
    report_lines.append(f"pre_s={args.pre_s} post_s={args.post_s} cooldown_s={args.cooldown_s} cross_frac={args.cross_frac}")
    report_lines.append(f"confirm_primary_s={args.confirm_primary_s} confirm_secondary_s={args.confirm_secondary_s} jitter_guard_ms={args.jitter_guard_ms}")
    report_lines.append(f"exclude_ref={sorted(exclude_ref)}")
    report_lines.append(f"timebase={timebase}")
    report_lines.append("")

    for (base_symbol, market_type), series_list in sorted(groups.items()):
        if not series_list:
            continue
        offsets = {s.candidate_id: _series_time_offset_ms(s.recv_minus_exchange_ms) for s in series_list}
        series_points = {s.candidate_id: len(s.points) for s in series_list}

        # Align bucket ranges across all series in this group using local-time-adjusted exchange timestamps.
        def bucket_for(p: SeriesPoint, *, offset_ms: float) -> int:
            return _effective_ts_ms(p, timebase=timebase, offset_ms=offset_ms) // bucket_ms

        offsets_for_timebase = offsets if timebase == "exchange_local" else {k: 0.0 for k in offsets}
        start_bucket = min((bucket_for(p, offset_ms=offsets_for_timebase[s.candidate_id]) for s in series_list for p in s.points), default=0)
        end_bucket = max((bucket_for(p, offset_ms=offsets_for_timebase[s.candidate_id]) for s in series_list for p in s.points), default=0)
        if end_bucket <= start_bucket:
            continue

        # Build aligned arrays
        series_px: dict[str, list[float | None]] = {}
        series_age: dict[str, list[int]] = {}
        series_jitter_p95: dict[str, float] = {}
        for s in series_list:
            px, age = _build_aligned_arrays(
                s,
                start_bucket=start_bucket,
                end_bucket=end_bucket,
                max_stale_buckets=max_stale_buckets,
                bucket_ms=bucket_ms,
                offset_ms=offsets_for_timebase[s.candidate_id],
                timebase=timebase,
            )
            series_px[s.candidate_id] = px
            series_age[s.candidate_id] = age
            series_jitter_p95[s.candidate_id] = _jitter_p95_ms(s.recv_minus_exchange_ms)

        ref_px = _composite_ref(series_px, exclude_candidate_ids=exclude_ref)
        r_ref = _compute_returns(ref_px, horizon_buckets=horizon_buckets)
        events, thresholds = _extract_events(
            r_ref,
            impulse_q=float(args.impulse_q),
            transition_q=float(args.transition_q),
            micro_q=float(args.micro_q),
            cooldown_buckets=cooldown_buckets,
            pre_buckets=pre_buckets,
            post_buckets=post_buckets,
            calm_count=int(args.calm_count),
        )

        candidates = sorted(series_px.keys())
        if len(candidates) < 2:
            continue

        pair_results = _pairwise_tournament(
            bucket_ms=bucket_ms,
            group_key=(base_symbol, market_type),
            candidates=candidates,
            series_px=series_px,
            series_age=series_age,
            series_jitter_p95=series_jitter_p95,
            ref_px=ref_px,
            events=events,
            pre_buckets=pre_buckets,
            post_buckets=post_buckets,
            dir_horizon_buckets=dir_horizon_buckets,
            jitter_guard_ms_default=int(args.jitter_guard_ms),
            confirm_primary_buckets=confirm_primary_buckets,
            confirm_secondary_buckets=confirm_secondary_buckets,
            cross_frac=float(args.cross_frac),
            max_stale_buckets=max_stale_buckets,
        )
        scores = _venue_scores(candidates, pair_results, weights=weights)

        report_lines.append(f"== {base_symbol} / {market_type} ==")
        report_lines.append(f"candidates: {', '.join(candidates)}")
        report_lines.append(_format_time_hygiene(candidates, offsets, series_jitter_p95, series_points))
        report_lines.append(
            "events: "
            + ", ".join(
                f"{reg}={sum(1 for e in events if e.regime == reg)}" for reg in ("impulse", "transition", "calm")
            )
        )
        report_lines.append(
            f"thresholds(|r_ref|): impulse={thresholds['impulse_thr']:.6f} transition={thresholds['transition_thr']:.6f} micro={thresholds['micro_thr']:.6f}"
        )
        report_lines.append("")
        report_lines.append(_format_rankings(candidates, scores, weights=weights))
        report_lines.append(_format_pair_table(candidates, pair_results, regime="impulse"))
        report_lines.append(_format_pair_table(candidates, pair_results, regime="transition"))
        report_lines.append(_format_pair_table(candidates, pair_results, regime="calm"))

    out_path: Path
    if str(args.out).strip():
        out_path = Path(str(args.out))
    else:
        out_path = Path("docs/diagnostics") / f"venue-tournament-l1-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
