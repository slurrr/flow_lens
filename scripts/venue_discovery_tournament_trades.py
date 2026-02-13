#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
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
    hyperliquid_ts_mode: str | None = None


@dataclass
class Reservoir:
    capacity: int
    seed: int
    _values: list[float] = field(default_factory=list)
    _n_seen: int = 0
    _sorted_cache: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self._n_seen += 1
        if len(self._values) < self.capacity:
            self._values.append(value)
            bisect.insort(self._sorted_cache, value)
            return
        # deterministic-ish reservoir: use a simple LCG via seed.
        self.seed = (1103515245 * self.seed + 12345) & 0x7FFFFFFF
        j = self.seed % self._n_seen
        if j < self.capacity:
            old = self._values[j]
            self._values[j] = value
            idx = bisect.bisect_left(self._sorted_cache, old)
            if idx < len(self._sorted_cache):
                self._sorted_cache.pop(idx)
                bisect.insort(self._sorted_cache, value)
            else:
                # Safety fallback for unexpected float edge cases.
                self._sorted_cache = sorted(self._values)

    def median(self) -> float:
        return self.pct(0.5)

    def pct(self, pct: float) -> float:
        if not self._sorted_cache:
            return 0.0
        if pct <= 0:
            return self._sorted_cache[0]
        if pct >= 1:
            return self._sorted_cache[-1]
        idx = int(round(pct * (len(self._sorted_cache) - 1)))
        return self._sorted_cache[idx]


@dataclass
class BucketAgg:
    sum_px_qty: float = 0.0
    sum_qty: float = 0.0
    last_px: float = 0.0
    last_ts_ms: int = 0

    def add(self, ts_ms: int, price: float, qty: float) -> None:
        if qty > 0:
            self.sum_px_qty += price * qty
            self.sum_qty += qty
        if ts_ms >= self.last_ts_ms:
            self.last_ts_ms = ts_ms
            self.last_px = price

    def price(self) -> float | None:
        if self.sum_qty > 0:
            return self.sum_px_qty / self.sum_qty
        if self.last_px > 0:
            return self.last_px
        return None


@dataclass
class SeriesStats:
    points: int = 0
    recv_minus_exchange: Reservoir = field(default_factory=lambda: Reservoir(20_000, seed=123))
    venue_points: int = 0
    recv_minus_venue: Reservoir = field(default_factory=lambda: Reservoir(20_000, seed=456))
    stale_dropped: int = 0


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


def _open_maybe_gz(path: Path) -> Iterable[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from f
        return
    with path.open("rt", encoding="utf-8") as f:
        yield from f


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


def _series_time_offset_ms(recv_minus_exchange_ms: Reservoir) -> float:
    # Center estimate; reservoir is already a sample.
    return float(recv_minus_exchange_ms.median())


def _effective_ts_ms(ts_exchange_ms: int, ts_recv_ms: int, *, timebase: Timebase, offset_ms: float) -> int:
    if timebase == "exchange":
        return ts_exchange_ms
    if timebase == "recv":
        return ts_recv_ms
    return int(ts_exchange_ms + offset_ms)


def _compute_returns(px: list[float | None], *, horizon_buckets: int) -> list[float | None]:
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

    def pct(p: float) -> float:
        if p <= 0:
            return abs_r[0]
        if p >= 1:
            return abs_r[-1]
        idx = int(round(p * (len(abs_r) - 1)))
        return abs_r[idx]

    impulse_thr = pct(impulse_q)
    transition_thr = pct(transition_q)
    micro_thr = pct(micro_q)

    events: list[EventWindow] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        for a, b in occupied:
            if not (end < a or start > b):
                return True
        return False

    def place(regime: Regime, t: int) -> None:
        start = max(0, t - pre_buckets)
        end = t + post_buckets
        events.append(EventWindow(regime=regime, t0_bucket=t, start_bucket=start, end_bucket=end))
        occupied.append((start, end))

    last_t: int | None = None

    def can_place(t: int) -> bool:
        nonlocal last_t
        if last_t is None:
            return True
        return (t - last_t) >= cooldown_buckets

    # Impulse
    for t, r in enumerate(r_ref):
        if not isinstance(r, float):
            continue
        if abs(r) < impulse_thr:
            continue
        if not can_place(t):
            continue
        place("impulse", t)
        last_t = t

    # Transition: sign flip + threshold
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
        if overlaps(max(0, t - pre_buckets), t + post_buckets):
            continue
        place("transition", t)
        last_t = t

    # Calm control windows: pick low-activity segments that don't overlap impulse/transition windows.
    if calm_count > 0:
        candidates: list[int] = []
        for t, r in enumerate(r_ref):
            if not isinstance(r, float):
                continue
            if abs(r) > micro_thr:
                continue
            start = max(0, t - pre_buckets)
            end = t + post_buckets
            if overlaps(start, end):
                continue
            candidates.append(t)
        step = max(1, len(candidates) // max(1, calm_count))
        for idx in range(0, len(candidates), step):
            if len([e for e in events if e.regime == "calm"]) >= calm_count:
                break
            t = candidates[idx]
            start = max(0, t - pre_buckets)
            end = t + post_buckets
            if overlaps(start, end):
                continue
            place("calm", t)

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
    candidates: list[str],
    series_px: dict[str, list[float | None]],
    series_age: dict[str, list[int]],
    jitter_p95: dict[str, float],
    ref_px: list[float | None],
    events: list[EventWindow],
    dir_horizon_buckets: int,
    jitter_guard_ms_default: int,
    confirm_primary_buckets: int,
    confirm_secondary_buckets: int,
    cross_frac: float,
    max_stale_buckets: int,
) -> dict[tuple[str, str], dict[Regime, PairCounts]]:
    results: dict[tuple[str, str], dict[Regime, PairCounts]] = {}
    pair_defs: list[tuple[str, str, int]] = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            results[(a, b)] = {"impulse": PairCounts(), "transition": PairCounts(), "calm": PairCounts()}
            jitter_guard_ms = max(jitter_guard_ms_default, int(jitter_p95.get(a, 0.0)), int(jitter_p95.get(b, 0.0)))
            pair_defs.append((a, b, max(0, jitter_guard_ms // bucket_ms)))

    r_ref = _compute_returns(ref_px, horizon_buckets=dir_horizon_buckets)

    for e in events:
        t0 = e.t0_bucket
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
        r_now = r_ref[t0 + dir_horizon_buckets]
        if not isinstance(r_now, float):
            continue

        confirm_buckets = confirm_primary_buckets if e.regime != "calm" else confirm_secondary_buckets

        crossings: dict[str, int | None] = {}
        for candidate in candidates:
            px = series_px.get(candidate)
            age = series_age.get(candidate)
            if px is None or age is None:
                crossings[candidate] = None
                continue
            crossings[candidate] = _first_crossing(
                px,
                age,
                t0=t0,
                start=e.start_bucket,
                end=e.end_bucket,
                dir_sign=dir_sign,
                required_move=required_move,
                cross_frac=cross_frac,
                max_stale_buckets=max_stale_buckets,
            )

        for a, b, jitter_guard_buckets in pair_defs:
            rc = results[(a, b)][e.regime]
            t_a = crossings.get(a)
            t_b = crossings.get(b)
            if t_a is None or t_b is None:
                rc.no_contest += 1
                continue

            rc.contests += 1

            dt = t_b - t_a
            if dt == 0:
                rc.ties += 1
                continue
            if dt > 0:
                if (t_a + jitter_guard_buckets) < t_b and dt <= confirm_buckets:
                    rc.a_wins += 1
                    rc.lead_times_ms_a.append(dt * bucket_ms)
                else:
                    rc.ties += 1
            else:
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
    scores: dict[str, dict[ScoreKey, float]] = {
        c: {"impulse": 0.0, "transition": 0.0, "calm": 0.0, "combined": 0.0} for c in candidates
    }
    denom: dict[str, dict[Regime, int]] = {c: {"impulse": 0, "transition": 0, "calm": 0} for c in candidates}

    for (a, b), by_regime in pair_results.items():
        for regime, c in by_regime.items():
            if c.contests <= 0:
                continue
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


def _format_rankings(candidates: list[str], scores: dict[str, dict[ScoreKey, float]], *, weights: dict[Regime, float]) -> str:
    lines: list[str] = []
    lines.append("Venue rankings (avg pairwise win rate)")
    lines.append("candidate                combined   impulse   transition   calm")
    ranked = sorted(candidates, key=lambda v: float(scores[v]["combined"]), reverse=True)
    for v in ranked:
        s = scores[v]
        lines.append(f"{v[:22].ljust(22)}  {s['combined']:8.3f}  {s['impulse']:7.3f}  {s['transition']:10.3f}  {s['calm']:6.3f}")
    lines.append("")
    lines.append(f"weights: impulse={weights['impulse']:.2f}, transition={weights['transition']:.2f}, calm={weights['calm']:.2f}")
    lines.append("")
    return "\n".join(lines)


def _format_time_hygiene(
    candidates: list[str],
    stats: dict[str, SeriesStats],
    offsets_ms: dict[str, float],
) -> str:
    lines: list[str] = []
    lines.append("Time hygiene (from ts_recv_ms - ts_exchange_ms)")
    lines.append("candidate                points   med_offset_ms   jitter_p95_ms")
    for cid in sorted(candidates):
        s = stats.get(cid)
        points = s.points if s is not None else 0
        if s is not None:
            center = offsets_ms.get(cid, 0.0)
            jitter = abs(s.recv_minus_exchange.pct(0.95) - center)
        else:
            jitter = 0.0
        lines.append(f"{cid[:22].ljust(22)}  {str(points).rjust(6)}  {offsets_ms.get(cid, 0.0):13.0f}  {jitter:12.0f}")
    lines.append("")
    return "\n".join(lines)


def _format_venue_time_hygiene(candidates: list[str], stats: dict[str, SeriesStats]) -> str:
    lines: list[str] = []
    lines.append("Venue timestamp lag (from ts_recv_ms - ts_venue_ms, when provided)")
    lines.append("candidate                points   med_lag_ms   lag_p95_ms")
    for cid in sorted(candidates):
        s = stats.get(cid)
        if s is None or s.venue_points <= 0 or not s.recv_minus_venue._values:
            lines.append(f"{cid[:22].ljust(22)}  {str(0).rjust(6)}  {str(0).rjust(10)}  {str(0).rjust(9)}")
            continue
        try:
            med = float(statistics.median(s.recv_minus_venue._values))
        except statistics.StatisticsError:
            med = 0.0
        p95 = float(s.recv_minus_venue.pct(0.95))
        lines.append(f"{cid[:22].ljust(22)}  {str(s.venue_points).rjust(6)}  {med:10.0f}  {p95:9.0f}")
    lines.append("")
    return "\n".join(lines)


def _format_stale_drops(
    candidates: list[str],
    stats: dict[str, SeriesStats],
    *,
    drop_stale: bool,
    max_wire_lag_ms: int,
) -> str:
    lines: list[str] = []
    lines.append(f"Stale-on-arrival drops (wire_lag_ms > {max_wire_lag_ms}ms) drop_stale={drop_stale}")
    lines.append("candidate                dropped")
    for cid in sorted(candidates):
        s = stats.get(cid)
        dropped = s.stale_dropped if s is not None else 0
        lines.append(f"{cid[:22].ljust(22)}  {str(dropped).rjust(7)}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pairwise venue discovery tournament on captured trade prints.")
    parser.add_argument("--input", required=True, help="Path to a venue_trade_capture log (jsonl or jsonl.gz).")
    parser.add_argument(
        "--start-ms",
        type=int,
        default=0,
        help="Optional inclusive epoch-ms lower bound (applied on chosen timebase). Default: 0 (disabled).",
    )
    parser.add_argument(
        "--end-ms",
        type=int,
        default=0,
        help="Optional exclusive epoch-ms upper bound (applied on chosen timebase). Default: 0 (disabled).",
    )
    parser.add_argument("--bucket-ms", type=int, default=200, help="Time bucket size in ms (default: 200).")
    parser.add_argument("--return-horizon-ms", type=int, default=1000, help="Return horizon for event extraction (default: 1000).")
    parser.add_argument("--dir-horizon-ms", type=int, default=1000, help="Direction horizon for composite move (default: 1000).")
    parser.add_argument("--pre-s", type=float, default=2.0, help="Event pre-window seconds (default: 2.0).")
    parser.add_argument("--post-s", type=float, default=8.0, help="Event post-window seconds (default: 8.0).")
    parser.add_argument("--cooldown-s", type=float, default=4.0, help="Cooldown between extracted events (default: 4.0).")
    parser.add_argument("--impulse-q", type=float, default=0.95, help="Impulse threshold quantile on |r_ref| (default: 0.95).")
    parser.add_argument("--transition-q", type=float, default=0.85, help="Transition threshold quantile on |r_ref| (default: 0.85).")
    parser.add_argument("--micro-q", type=float, default=0.70, help="Micro threshold quantile on |r_ref| (default: 0.70).")
    parser.add_argument("--calm-count", type=int, default=20, help="Number of calm windows to sample (default: 20).")
    parser.add_argument("--cross-frac", type=float, default=0.4, help="First-crossing fraction of composite move (default: 0.4).")
    parser.add_argument("--confirm-primary-s", type=float, default=2.0, help="Primary confirm horizon seconds (default: 2.0).")
    parser.add_argument("--confirm-secondary-s", type=float, default=4.0, help="Secondary confirm horizon seconds (default: 4.0).")
    parser.add_argument("--jitter-guard-ms", type=int, default=250, help="Default jitter guard (ms) (default: 250).")
    parser.add_argument("--max-stale-s", type=float, default=1.0, help="Max fill-forward staleness seconds (default: 1.0).")
    parser.add_argument(
        "--drop-stale",
        action="store_true",
        help="Drop stale-on-arrival trades (wire_lag_ms > --max-wire-lag-ms). Default: disabled.",
    )
    parser.add_argument(
        "--max-wire-lag-ms",
        type=int,
        default=5000,
        help="Max allowed wire lag in ms for --drop-stale (default: 5000).",
    )
    parser.add_argument(
        "--wire-lag-thresholds-ms",
        default="",
        help=(
            "Optional comma-separated thresholds to run in one report (e.g. '2000,4000'). "
            "When set, the analyzer runs multiple passes with drop_stale enabled for each threshold."
        ),
    )
    parser.add_argument("--weights", default="0.60,0.30,0.10", help="Regime weights impulse,transition,calm (default: 0.60,0.30,0.10).")
    parser.add_argument("--exclude-ref", default="upbit_spot", help="Comma-separated candidate_ids to exclude from composite reference price.")
    parser.add_argument(
        "--timebase",
        choices=["exchange", "recv", "exchange_local"],
        default="exchange",
        help="Timebase for bucketing/alignment (default: exchange).",
    )
    parser.add_argument("--out", default="", help="Output path (txt). Default writes to docs/diagnostics with timestamp.")
    args = parser.parse_args()

    input_path = Path(args.input)
    start_ms = int(args.start_ms or 0)
    end_ms = int(args.end_ms or 0)
    if start_ms and end_ms and end_ms <= start_ms:
        raise ValueError("--end-ms must be > --start-ms when both are provided")
    bucket_ms = max(50, int(args.bucket_ms))
    horizon_buckets = max(1, int(round(float(args.return_horizon_ms) / bucket_ms)))
    dir_horizon_buckets = max(1, int(round(float(args.dir_horizon_ms) / bucket_ms)))
    pre_buckets = max(0, int(round(float(args.pre_s) * 1000 / bucket_ms)))
    post_buckets = max(1, int(round(float(args.post_s) * 1000 / bucket_ms)))
    cooldown_buckets = max(1, int(round(float(args.cooldown_s) * 1000 / bucket_ms)))
    confirm_primary_buckets = max(1, int(round(float(args.confirm_primary_s) * 1000 / bucket_ms)))
    confirm_secondary_buckets = max(1, int(round(float(args.confirm_secondary_s) * 1000 / bucket_ms)))
    max_stale_buckets = max(0, int(round(float(args.max_stale_s) * 1000 / bucket_ms)))
    drop_stale = bool(args.drop_stale)
    max_wire_lag_ms = max(0, int(args.max_wire_lag_ms))
    weights = _weights(str(args.weights))
    exclude_ref = {x.strip() for x in str(args.exclude_ref).split(",") if x.strip()}
    timebase_str = str(args.timebase)
    if timebase_str not in ("exchange", "recv", "exchange_local"):
        raise ValueError(f"Invalid timebase: {timebase_str!r}")
    timebase: Timebase = timebase_str

    thresholds: list[int] = []
    if str(args.wire_lag_thresholds_ms).strip():
        for part in str(args.wire_lag_thresholds_ms).split(","):
            part = part.strip()
            if not part:
                continue
            thresholds.append(max(0, int(part)))
        thresholds = sorted(set(thresholds))
    else:
        thresholds = [max_wire_lag_ms]

    multi = str(args.wire_lag_thresholds_ms).strip() != ""
    drop_stale_by_thr = {thr: True for thr in thresholds} if multi else {thresholds[0]: drop_stale}

    meta: CaptureMeta | None = None
    # Per-threshold state (trade parsing happens once)
    groups_by_thr: dict[int, dict[tuple[str, str], dict[str, dict[int, BucketAgg]]]] = {
        thr: defaultdict(lambda: defaultdict(dict)) for thr in thresholds
    }
    stats_by_thr: dict[int, dict[tuple[str, str], dict[str, SeriesStats]]] = {
        thr: defaultdict(lambda: defaultdict(SeriesStats)) for thr in thresholds
    }

    for line in _open_maybe_gz(input_path):
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
            if isinstance(m, dict) and m.get("type") == "venue_trade_capture":
                meta = CaptureMeta(
                    created_at_ms=int(m.get("created_at_ms") or 0),
                    stop_at_ms=int(m.get("stop_at_ms") or 0),
                    symbols=[str(x) for x in (m.get("symbols") or []) if isinstance(x, str)],
                    candidates=[str(x) for x in (m.get("candidates") or []) if isinstance(x, str)],
                    hyperliquid_ts_mode=str(m.get("hyperliquid_ts_mode")) if isinstance(m.get("hyperliquid_ts_mode"), str) else None,
                )
            continue

        candidate_id = payload.get("candidate_id")
        venue = payload.get("venue")
        market_type = payload.get("market_type")
        base_symbol = payload.get("base_symbol")
        if not isinstance(candidate_id, str) or not isinstance(venue, str) or not isinstance(market_type, str) or not isinstance(base_symbol, str):
            continue
        ts_exchange_ms = payload.get("ts_exchange_ms")
        ts_venue_ms = payload.get("ts_venue_ms")
        ts_recv_ms = payload.get("ts_recv_ms")
        price = payload.get("price")
        size = payload.get("size")
        if not isinstance(ts_exchange_ms, int) or not isinstance(ts_recv_ms, int):
            continue
        if not isinstance(price, (int, float)):
            continue
        if not isinstance(size, (int, float)):
            size = 0.0
        px = float(price)
        qty = float(size)
        if px <= 0:
            continue

        filter_ts_ms = ts_exchange_ms if timebase in {"exchange", "exchange_local"} else ts_recv_ms
        if start_ms and filter_ts_ms < start_ms:
            continue
        if end_ms and filter_ts_ms >= end_ms:
            continue

        key = (base_symbol, market_type)

        wire_basis_ms = ts_venue_ms if isinstance(ts_venue_ms, int) and ts_venue_ms > 0 else ts_exchange_ms
        wire_lag_ms = ts_recv_ms - int(wire_basis_ms)

        for thr in thresholds:
            stats = stats_by_thr[thr]
            s = stats[key][candidate_id]
            s.points += 1
            s.recv_minus_exchange.add(float(ts_recv_ms - ts_exchange_ms))
            if isinstance(ts_venue_ms, int) and ts_venue_ms > 0:
                s.venue_points += 1
                s.recv_minus_venue.add(float(ts_recv_ms - ts_venue_ms))

            if drop_stale_by_thr[thr] and wire_lag_ms > thr:
                s.stale_dropped += 1
                continue

            offset_ms = 0.0
            if timebase == "exchange_local":
                offset_ms = _series_time_offset_ms(s.recv_minus_exchange)
            effective_ms = _effective_ts_ms(ts_exchange_ms, ts_recv_ms, timebase=timebase, offset_ms=offset_ms)
            b = effective_ms // bucket_ms
            groups = groups_by_thr[thr]
            agg = groups[key][candidate_id].get(b)
            if agg is None:
                agg = BucketAgg()
                groups[key][candidate_id][b] = agg
            agg.add(effective_ms, px, qty)

    if meta is None:
        raise ValueError(f"Capture file missing meta line: {input_path}")

    report_lines: list[str] = []
    report_lines.append("Venue Discovery Tournament (Trade-based)")
    report_lines.append(f"input: {input_path}")
    if start_ms or end_ms:
        basis = "exchange" if timebase in {"exchange", "exchange_local"} else "recv"
        report_lines.append(f"time_filter_ms=[{start_ms or '-inf'},{end_ms or '+inf'}) basis={basis}")
    if meta.created_at_ms > 0 and meta.stop_at_ms > meta.created_at_ms:
        report_lines.append(f"capture_duration_s={(meta.stop_at_ms - meta.created_at_ms) / 1000.0:.1f}")
    report_lines.append(f"bucket_ms={bucket_ms} horizon_ms={args.return_horizon_ms} dir_horizon_ms={args.dir_horizon_ms}")
    report_lines.append(f"pre_s={args.pre_s} post_s={args.post_s} cooldown_s={args.cooldown_s} cross_frac={args.cross_frac}")
    report_lines.append(f"confirm_primary_s={args.confirm_primary_s} confirm_secondary_s={args.confirm_secondary_s} jitter_guard_ms={args.jitter_guard_ms}")
    report_lines.append(f"exclude_ref={sorted(exclude_ref)}")
    report_lines.append(f"timebase={timebase}")
    if meta.hyperliquid_ts_mode:
        report_lines.append(f"hyperliquid_ts_mode={meta.hyperliquid_ts_mode}")
    if multi:
        report_lines.append(f"wire_lag_thresholds_ms={thresholds}")
    report_lines.append("")

    for thr in thresholds:
        if multi:
            report_lines.append(f"=== wire_lag_ms<={thr} ===")
            report_lines.append("")

        groups = groups_by_thr[thr]
        stats = stats_by_thr[thr]

        for (base_symbol, market_type), by_candidate in sorted(groups.items()):
            if not by_candidate:
                continue
            candidates = sorted(by_candidate.keys())
            if len(candidates) < 2:
                continue

            start_bucket = min((b for d in by_candidate.values() for b in d.keys()), default=0)
            end_bucket = max((b for d in by_candidate.values() for b in d.keys()), default=0)
            n = end_bucket - start_bucket + 1

            series_px: dict[str, list[float | None]] = {}
            series_age: dict[str, list[int]] = {}
            offsets_ms: dict[str, float] = {}
            jitter_p95: dict[str, float] = {}

            for cid in candidates:
                s = stats[(base_symbol, market_type)][cid]
                offsets_ms[cid] = _series_time_offset_ms(s.recv_minus_exchange)
                jitter_p95[cid] = s.recv_minus_exchange.pct(0.95)
                arr: list[float | None] = [None] * n
                age: list[int] = [max_stale_buckets + 1] * n

                last_px: float | None = None
                last_idx: int | None = None
                buckets = by_candidate[cid]
                for i in range(n):
                    b = start_bucket + i
                    agg = buckets.get(b)
                    if agg is not None:
                        p = agg.price()
                        if isinstance(p, float) and p > 0:
                            arr[i] = p
                            age[i] = 0
                            last_px = p
                            last_idx = i
                            continue
                    if last_px is None or last_idx is None:
                        continue
                    gap = i - last_idx
                    age[i] = gap
                    if gap <= max_stale_buckets:
                        arr[i] = last_px

                series_px[cid] = arr
                series_age[cid] = age

            # Composite reference is median across candidates (excluding some)
            ref_px: list[float | None] = [None] * n
            for i in range(n):
                values: list[float] = []
                for cid in candidates:
                    if cid in exclude_ref:
                        continue
                    v = series_px[cid][i]
                    if isinstance(v, float) and v > 0:
                        values.append(v)
                ref_px[i] = _median(values)

            r_ref = _compute_returns(ref_px, horizon_buckets=horizon_buckets)
            events, thr_abs = _extract_events(
                r_ref,
                impulse_q=float(args.impulse_q),
                transition_q=float(args.transition_q),
                micro_q=float(args.micro_q),
                cooldown_buckets=cooldown_buckets,
                pre_buckets=pre_buckets,
                post_buckets=post_buckets,
                calm_count=int(args.calm_count),
            )

            pair_results = _pairwise_tournament(
                bucket_ms=bucket_ms,
                candidates=candidates,
                series_px=series_px,
                series_age=series_age,
                jitter_p95=jitter_p95,
                ref_px=ref_px,
                events=events,
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
            report_lines.append(_format_time_hygiene(candidates, stats[(base_symbol, market_type)], offsets_ms))
            report_lines.append(_format_venue_time_hygiene(candidates, stats[(base_symbol, market_type)]))
            report_lines.append(
                _format_stale_drops(
                    candidates,
                    stats[(base_symbol, market_type)],
                    drop_stale=drop_stale_by_thr[thr],
                    max_wire_lag_ms=thr,
                )
            )
            report_lines.append(
                "events: " + ", ".join(f"{reg}={sum(1 for e in events if e.regime == reg)}" for reg in ("impulse", "transition", "calm"))
            )
            report_lines.append(
                f"thresholds(|r_ref|): impulse={thr_abs['impulse_thr']:.6f} transition={thr_abs['transition_thr']:.6f} micro={thr_abs['micro_thr']:.6f}"
            )
            report_lines.append("")
            report_lines.append(_format_rankings(candidates, scores, weights=weights))

    out_path: Path
    if str(args.out).strip():
        out_path = Path(str(args.out))
    else:
        out_path = Path("docs/diagnostics") / f"venue-tournament-trades-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
