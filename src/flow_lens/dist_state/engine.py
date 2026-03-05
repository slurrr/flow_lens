from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field

from flow_lens.dist_state.models import (
    DistKlineCloseEvent,
    DistOiSnapshotEvent,
    DistPanelSnapshot,
    DistRowBins,
    DistRowMetrics,
    DistRowSnapshot,
    DistTimeframe,
)

LOGGER = logging.getLogger(__name__)
EPSILON = 1e-12
BIN_LEVELS = 7


@dataclass(frozen=True)
class DistStateConfig:
    enabled: bool
    symbol: str
    source_id: str
    timeframes: tuple[DistTimeframe, ...]
    warmup_kline_bars: int
    warmup_oi_hist_points: int
    ready_core_min_bars: int
    ready_p_min_deltas: int
    oi_join_tolerance_ms: int
    oi_seed_points: int
    oi_seed_min_points: int
    v_scale_window_bars: int
    v_scale_percentile: float
    v_scale_min_samples: int
    hl_vol_bars: float
    hl_stretch_bars: float
    hl_oi_bars: float
    hl_atr_short_bars: float
    hl_atr_long_bars: float
    hl_a_bars: float
    k_s: float
    k_p: float
    k_t: float


@dataclass
class _DistRowState:
    tf: DistTimeframe
    bars_seen: int = 0
    oi_deltas_seen: int = 0
    oi_var_initialized: bool = False
    last_close_ms: int | None = None
    last_processed_close_ms: int | None = None
    processed_close_keys: deque[int] = field(default_factory=deque)
    processed_close_set: set[int] = field(default_factory=set)
    prev_close: float | None = None
    prev_return: float | None = None
    var_r: float = 0.0
    mu_x: float | None = None
    var_dx: float = 0.0
    p_same: float = 0.5
    atr_s: float | None = None
    atr_l: float | None = None
    var_oi: float = 0.0
    prev_oi: float | None = None
    v_scale_samples: deque[float] = field(default_factory=deque)
    metrics: DistRowMetrics = DistRowMetrics(None, None, None, None, None)
    bins: DistRowBins = DistRowBins(None, None, None, None, None)

class DistStateEngine:
    def __init__(self, config: DistStateConfig) -> None:
        self._config = config
        self._rows: dict[DistTimeframe, _DistRowState] = {
            tf: _DistRowState(tf=tf) for tf in config.timeframes
        }
        self._oi_buckets: dict[tuple[str, int], DistOiSnapshotEvent] = {}
        self._last_oi_ts_recv_ms: int | None = None
        self._last_oi_value: float | None = None
        self._kline_base = "https://fapi.binance.com/fapi/v1/klines"
        self._oi_live_url = "https://fapi.binance.com/fapi/v1/openInterest"
        self._oi_hist_url = "https://fapi.binance.com/futures/data/openInterestHist"
        self._lambda_vol = _ewma_lambda(config.hl_vol_bars)
        self._lambda_stretch = _ewma_lambda(config.hl_stretch_bars)
        self._lambda_oi = _ewma_lambda(config.hl_oi_bars)
        self._lambda_atr_s = _ewma_lambda(config.hl_atr_short_bars)
        self._lambda_atr_l = _ewma_lambda(config.hl_atr_long_bars)
        self._lambda_a = _ewma_lambda(config.hl_a_bars)

    def warmup(self) -> None:
        for tf in self._config.timeframes:
            self._warmup_klines(tf)
        self._warmup_oi()
        for row in self._rows.values():
            row.last_processed_close_ms = row.last_close_ms
            row.processed_close_set.clear()
            row.processed_close_keys.clear()

    def on_kline_close(self, event: DistKlineCloseEvent) -> DistPanelSnapshot:
        row = self._rows[event.tf]
        if not self._accept_close(row, event.kline_close_ms):
            return self.snapshot()
        if event.tf == "3m":
            self._sample_oi_bucket(event)
        self._evict_oi_buckets(event.kline_close_ms)
        self._apply_close(row, event)
        return self.snapshot()

    def snapshot(self) -> DistPanelSnapshot:
        out_rows: dict[DistTimeframe, DistRowSnapshot] = {}
        for tf, row in self._rows.items():
            ready_core = (
                row.bars_seen >= self._config.ready_core_min_bars
                and len(row.v_scale_samples) >= self._config.v_scale_min_samples
                and row.atr_l is not None
            )
            ready_p = (
                row.oi_var_initialized
                and row.oi_deltas_seen >= self._config.ready_p_min_deltas
            )
            out_rows[tf] = DistRowSnapshot(
                tf=tf,
                ready_core=ready_core,
                ready_p=ready_p,
                last_close_ms=row.last_close_ms,
                metrics=row.metrics,
                bins=row.bins,
            )
        return DistPanelSnapshot(
            symbol=self._config.symbol,
            source_id=self._config.source_id,
            rows=out_rows,
            last_oi_ts_recv_ms=self._last_oi_ts_recv_ms,
            last_oi_value=self._last_oi_value,
        )

    def _warmup_klines(self, tf: DistTimeframe) -> None:
        params = {
            "symbol": f"{self._config.symbol.upper()}USDT",
            "interval": tf,
            "limit": str(self._config.warmup_kline_bars),
        }
        payload = self._http_json(self._kline_base, params)
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, list) or len(item) < 7:
                continue
            event = DistKlineCloseEvent(
                ts_recv_ms=int(item[6]),
                symbol=self._config.symbol,
                source_id=self._config.source_id,
                tf=tf,
                kline_open_ms=int(item[0]),
                kline_close_ms=int(item[6]),
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
            )
            row = self._rows[tf]
            self._apply_close(row, event)

    def _warmup_oi(self) -> None:
        for tf in ("15m", "1h", "4h"):
            if tf not in self._rows:
                continue
            series = self._fetch_oi_hist(tf, self._config.warmup_oi_hist_points)
            self._seed_row_oi(self._rows[tf], series, use_seed=False)
        if "3m" in self._rows:
            series_5m = self._fetch_oi_hist("5m", self._config.oi_seed_points)
            self._seed_3m_from_5m(self._rows["3m"], series_5m)

    def _fetch_oi_hist(self, period: str, limit: int) -> list[float]:
        params = {
            "symbol": f"{self._config.symbol.upper()}USDT",
            "period": period,
            "limit": str(limit),
        }
        payload = self._http_json(self._oi_hist_url, params)
        out: list[float] = []
        if not isinstance(payload, list):
            return out
        for item in payload:
            if not isinstance(item, dict):
                continue
            value = item.get("sumOpenInterest")
            if not isinstance(value, (int, float, str)):
                continue
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out

    def _seed_row_oi(self, row: _DistRowState, series: list[float], *, use_seed: bool) -> None:
        del use_seed
        if len(series) < 2:
            return
        prev = series[0]
        for cur in series[1:]:
            delta = cur - prev
            row.var_oi = self._lambda_oi * row.var_oi + (1 - self._lambda_oi) * (delta * delta)
            row.oi_deltas_seen += 1
            prev = cur
        row.prev_oi = series[-1]
        row.oi_var_initialized = row.oi_deltas_seen >= self._config.oi_seed_min_points

    def _seed_3m_from_5m(self, row: _DistRowState, series_5m: list[float]) -> None:
        if len(series_5m) < 2:
            return
        hl_oi_bars_5m = self._config.hl_oi_bars * (3.0 / 5.0)
        lambda_5m = _ewma_lambda(hl_oi_bars_5m)
        var_oi_5m = 0.0
        deltas = 0
        prev = series_5m[0]
        for cur in series_5m[1:]:
            delta = cur - prev
            var_oi_5m = lambda_5m * var_oi_5m + (1 - lambda_5m) * (delta * delta)
            prev = cur
            deltas += 1
        if deltas < self._config.oi_seed_min_points:
            return
        row.var_oi = var_oi_5m * (3.0 / 5.0)
        row.prev_oi = series_5m[-1]
        row.oi_var_initialized = True

    def _accept_close(self, row: _DistRowState, close_ms: int) -> bool:
        if close_ms in row.processed_close_set:
            return False
        if row.last_processed_close_ms is not None and close_ms < row.last_processed_close_ms:
            return False
        row.last_processed_close_ms = close_ms
        row.processed_close_set.add(close_ms)
        row.processed_close_keys.append(close_ms)
        while len(row.processed_close_keys) > 4096:
            old = row.processed_close_keys.popleft()
            row.processed_close_set.discard(old)
        return True

    def _sample_oi_bucket(self, event: DistKlineCloseEvent) -> None:
        key = (event.source_id, event.kline_close_ms)
        if key in self._oi_buckets:
            return
        sample = self._fetch_live_oi()
        if sample is None:
            return
        if sample.venue_time_ms is None:
            return
        if abs(sample.venue_time_ms - event.kline_close_ms) > self._config.oi_join_tolerance_ms:
            return
        self._oi_buckets[key] = sample
        self._last_oi_ts_recv_ms = sample.ts_recv_ms
        self._last_oi_value = sample.oi

    def _fetch_live_oi(self) -> DistOiSnapshotEvent | None:
        params = {"symbol": f"{self._config.symbol.upper()}USDT"}
        payload = self._http_json(self._oi_live_url, params)
        if not isinstance(payload, dict):
            return None
        oi_raw = payload.get("openInterest")
        if not isinstance(oi_raw, (int, float, str)):
            return None
        try:
            oi = float(oi_raw)
        except (TypeError, ValueError):
            return None
        venue_time_raw = payload.get("time")
        venue_time_ms: int | None = None
        if isinstance(venue_time_raw, (int, float)):
            venue_time_ms = int(venue_time_raw)
        ts_recv_ms = int(time.time_ns() // 1_000_000)
        return DistOiSnapshotEvent(
            ts_recv_ms=ts_recv_ms,
            symbol=self._config.symbol,
            source_id=self._config.source_id,
            oi=oi,
            venue_time_ms=venue_time_ms,
        )

    def _evict_oi_buckets(self, current_close_ms: int) -> None:
        cutoff = current_close_ms - self._config.oi_join_tolerance_ms
        stale = [k for k in self._oi_buckets if k[1] < cutoff]
        for key in stale:
            self._oi_buckets.pop(key, None)

    def _apply_close(self, row: _DistRowState, event: DistKlineCloseEvent) -> None:
        prev_close = row.prev_close
        r_t = math.log(event.close / prev_close) if prev_close and prev_close > 0 else 0.0
        row.var_r = self._lambda_vol * row.var_r + (1 - self._lambda_vol) * (r_t * r_t)
        sigma_r = math.sqrt(max(row.var_r, 0.0))
        if sigma_r > 0:
            row.v_scale_samples.append(sigma_r)
            while len(row.v_scale_samples) > self._config.v_scale_window_bars:
                row.v_scale_samples.popleft()
        sigma_scale = _scale_value(
            row.v_scale_samples,
            percentile=self._config.v_scale_percentile,
            min_samples=self._config.v_scale_min_samples,
        )
        v_norm = sigma_r / (sigma_scale + EPSILON)
        v = v_norm / (1.0 + v_norm)

        x_t = math.log(event.close)
        if row.mu_x is None:
            row.mu_x = x_t
        dx_t = x_t - row.mu_x
        row.var_dx = self._lambda_stretch * row.var_dx + (1 - self._lambda_stretch) * (dx_t * dx_t)
        row.mu_x = self._lambda_stretch * row.mu_x + (1 - self._lambda_stretch) * x_t
        sigma_x = math.sqrt(max(row.var_dx, 0.0))
        s_raw = (x_t - row.mu_x) / (sigma_x + EPSILON)
        s = math.tanh(self._config.k_s * s_raw)

        same = 1.0 if _sign(r_t) != 0 and _sign(r_t) == _sign(row.prev_return or 0.0) else 0.0
        row.p_same = self._lambda_a * row.p_same + (1 - self._lambda_a) * same
        a = max(-1.0, min(1.0, 2.0 * (row.p_same - 0.5)))

        tr = event.high - event.low
        if prev_close is not None:
            tr = max(tr, abs(event.high - prev_close), abs(event.low - prev_close))
        row.atr_s = tr if row.atr_s is None else self._lambda_atr_s * row.atr_s + (1 - self._lambda_atr_s) * tr
        row.atr_l = tr if row.atr_l is None else self._lambda_atr_l * row.atr_l + (1 - self._lambda_atr_l) * tr
        t_raw = (row.atr_s or 0.0) / ((row.atr_l or 0.0) + EPSILON)
        t = math.tanh(self._config.k_t * math.log(max(t_raw, EPSILON)))

        p: float | None = None
        bucket = self._oi_buckets.get((event.source_id, event.kline_close_ms))
        if bucket is not None and row.prev_oi is not None:
            delta_oi = bucket.oi - row.prev_oi
            row.var_oi = self._lambda_oi * row.var_oi + (1 - self._lambda_oi) * (delta_oi * delta_oi)
            row.oi_deltas_seen += 1
            if row.oi_deltas_seen >= self._config.oi_seed_min_points:
                row.oi_var_initialized = True
            if row.oi_var_initialized:
                sigma_oi = math.sqrt(max(row.var_oi, 0.0))
                p_raw = (delta_oi / (sigma_oi + EPSILON)) * _sign(r_t)
                p = math.tanh(self._config.k_p * p_raw)
            row.prev_oi = bucket.oi
        elif bucket is not None and row.prev_oi is None:
            row.prev_oi = bucket.oi

        row.prev_close = event.close
        row.prev_return = r_t
        row.last_close_ms = event.kline_close_ms
        row.bars_seen += 1
        row.metrics = DistRowMetrics(v=v, s=s, a=a, p=p, t=t)
        row.bins = DistRowBins(
            v=_bin_unit(v),
            s=_bin_symmetric(s),
            a=_bin_symmetric(a),
            p=_bin_symmetric(p),
            t=_bin_symmetric(t),
        )

    def _http_json(self, url: str, params: dict[str, str]):
        query = urllib.parse.urlencode(params)
        full = f"{url}?{query}"
        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            LOGGER.debug("Dist-state HTTP call failed: %s", full, exc_info=True)
            return None


def _ewma_lambda(half_life_bars: float) -> float:
    bars = max(half_life_bars, 1e-6)
    return math.exp(-math.log(2.0) / bars)


def _scale_value(samples: deque[float], *, percentile: float, min_samples: int) -> float:
    if not samples:
        return 1.0
    values = sorted(samples)
    if len(values) < min_samples:
        return values[len(values) // 2]
    idx = int(round(max(0.0, min(1.0, percentile)) * (len(values) - 1)))
    return max(values[idx], EPSILON)


def _sign(value: float) -> float:
    if value > EPSILON:
        return 1.0
    if value < -EPSILON:
        return -1.0
    return 0.0


def _bin_symmetric(value: float | None) -> int | None:
    if value is None:
        return None
    v = max(-1.0, min(1.0, value))
    return int(round(((v + 1.0) * 0.5) * (BIN_LEVELS - 1)))


def _bin_unit(value: float | None) -> int | None:
    if value is None:
        return None
    v = max(0.0, min(1.0, value))
    return int(round(v * (BIN_LEVELS - 1)))
