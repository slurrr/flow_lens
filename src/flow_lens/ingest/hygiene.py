from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, replace

from flow_lens.adapters.base import AdapterEvent
from flow_lens.models.event import Event


@dataclass(frozen=True)
class HygieneConfig:
    enabled: bool = True
    max_excess_wire_lag_ms: int = 2000
    hard_max_wire_lag_ms: int = 30000
    wire_lag_baseline_window_s: int = 300
    wire_lag_baseline_sample_interval_ms: int = 200
    wire_lag_baseline_min_samples: int = 30
    wire_lag_baseline_max_samples: int = 2000
    dedupe_ttl_s: int = 30
    log_interval_s: int = 10
    future_venue_ts_grace_ms: int = 250
    connect_gate_s: int = 0
    connect_gate_max_excess_wire_lag_ms: int = 500
    connect_gate_hard_max_wire_lag_ms: int = 5000
    connect_gate_rearm_after_s: int = 60


@dataclass(frozen=True)
class HygieneMetricsEvent:
    symbol: str
    source_id: str
    interval_start_ms: int
    interval_end_ms: int
    samples_with_venue_ts: int
    wire_lag_ms_p50: float
    wire_lag_ms_p95: float
    stale_on_arrival_dropped: int
    dedupe_dropped: int
    venue_ts_missing: int
    negative_wire_lag: int
    future_venue_ts: int
    connect_gate_rearm_inactivity: int
    connect_gate_rearm_stale_burst: int


@dataclass
class _HygieneBucket:
    interval_start_ms: int
    interval_end_ms: int
    wire_lags: list[int]
    stale_on_arrival_dropped: int
    dedupe_dropped: int
    venue_ts_missing: int
    negative_wire_lag: int
    future_venue_ts: int
    connect_gate_rearm_inactivity: int
    connect_gate_rearm_stale_burst: int


class HygieneIngestor:
    def __init__(self, config: HygieneConfig) -> None:
        self._config = config
        self._dedupe_seen: OrderedDict[tuple[str, str, str], int] = OrderedDict()
        self._dedupe_ttl_ms = max(1, config.dedupe_ttl_s * 1000)
        self._dedupe_capacity = max(10_000, config.dedupe_ttl_s * 2_000)
        self._connect_seen_ms: dict[str, int] = {}
        self._last_seen_recv_ms_by_source: dict[str, int] = {}
        self._last_seen_recv_ms_by_source_symbol: dict[tuple[str, str], int] = {}
        self._stale_drop_recv_ms_by_source: dict[str, deque[int]] = {}
        self._log_interval_ms = max(1, config.log_interval_s * 1000)
        self._baseline_window_ms = max(1, config.wire_lag_baseline_window_s * 1000)
        self._baseline_sample_interval_ms = max(1, config.wire_lag_baseline_sample_interval_ms)
        self._baseline_min_samples = max(1, config.wire_lag_baseline_min_samples)
        self._baseline_max_samples = max(1, config.wire_lag_baseline_max_samples)
        self._baseline_samples: dict[tuple[str, str], deque[tuple[int, int]]] = {}
        self._last_baseline_sample_ts_ms: dict[tuple[str, str], int] = {}
        self._connect_gate_rearm_after_ms = max(0, config.connect_gate_rearm_after_s * 1000)
        self._stale_burst_window_ms = 5_000
        self._buckets: dict[tuple[str, str], _HygieneBucket] = {}
        self._pending_metrics: list[HygieneMetricsEvent] = []

    def process(self, item: AdapterEvent, *, base_symbol: str) -> Event | None:
        event = item.event
        actual_symbol = item.symbol.upper()
        lens_symbol = base_symbol.upper()
        source_id = event.source_id
        recv_ts_ms = event.timestamp
        event = replace(event, timestamp=recv_ts_ms)
        if not self._config.enabled:
            return event

        key = (lens_symbol, source_id)
        bucket = self._ensure_bucket(key, recv_ts_ms)
        self._ensure_connect_seen(source_id, recv_ts_ms)
        if self._maybe_rearm_on_inactivity(source_id, actual_symbol, recv_ts_ms):
            bucket.connect_gate_rearm_inactivity += 1

        # Locked order: dedupe -> stale-on-arrival -> enqueue.
        if event.trade_id is not None:
            dedupe_key = (source_id, actual_symbol, event.trade_id)
            if self._is_duplicate(dedupe_key, recv_ts_ms):
                bucket.dedupe_dropped += 1
                return None

        venue_ts = event.venue_timestamp_ms
        if venue_ts is None:
            bucket.venue_ts_missing += 1
            return event

        wire_lag_ms = recv_ts_ms - venue_ts
        bucket.wire_lags.append(wire_lag_ms)
        if wire_lag_ms < 0:
            bucket.negative_wire_lag += 1
        if venue_ts > recv_ts_ms + self._config.future_venue_ts_grace_ms:
            bucket.future_venue_ts += 1

        baseline_ms, baseline_initialized = self._wire_lag_baseline(key, recv_ts_ms)
        max_excess_wire_lag_ms = self._effective_max_excess_wire_lag_ms(source_id, recv_ts_ms)
        hard_max_wire_lag_ms = self._effective_hard_max_wire_lag_ms(source_id, recv_ts_ms)
        excess_wire_lag_ms = wire_lag_ms - baseline_ms
        stale_by_hard_cap = wire_lag_ms > hard_max_wire_lag_ms
        stale_by_excess = (
            baseline_initialized and excess_wire_lag_ms > max_excess_wire_lag_ms
        )
        if stale_by_hard_cap or stale_by_excess:
            bucket.stale_on_arrival_dropped += 1
            if self._maybe_rearm_on_stale_drop(
                source_id,
                recv_ts_ms,
                wire_lag_ms=wire_lag_ms,
                hard_max_wire_lag_ms=hard_max_wire_lag_ms,
            ):
                bucket.connect_gate_rearm_stale_burst += 1
            return None
        self._maybe_sample_wire_lag_baseline(key, recv_ts_ms, wire_lag_ms)
        return event

    def flush_due(self, now_ms: int) -> list[HygieneMetricsEvent]:
        if not self._config.enabled:
            self._pending_metrics.clear()
            self._buckets.clear()
            return []
        events = list(self._pending_metrics)
        self._pending_metrics.clear()
        for (symbol, source_id), bucket in list(self._buckets.items()):
            if now_ms < bucket.interval_end_ms:
                continue
            events.append(_finalize_bucket(symbol, source_id, bucket))
            self._buckets[(symbol, source_id)] = _HygieneBucket(
                interval_start_ms=bucket.interval_end_ms,
                interval_end_ms=bucket.interval_end_ms + self._log_interval_ms,
                wire_lags=[],
                stale_on_arrival_dropped=0,
                dedupe_dropped=0,
                venue_ts_missing=0,
                negative_wire_lag=0,
                future_venue_ts=0,
                connect_gate_rearm_inactivity=0,
                connect_gate_rearm_stale_burst=0,
            )
        return events

    def _ensure_connect_seen(self, source_id: str, now_ms: int) -> None:
        self._connect_seen_ms.setdefault(source_id, now_ms)

    def _maybe_rearm_on_inactivity(
        self,
        source_id: str,
        actual_symbol: str,
        now_ms: int,
    ) -> bool:
        key = (source_id, actual_symbol)
        source_last = self._last_seen_recv_ms_by_source.get(source_id)
        symbol_last = self._last_seen_recv_ms_by_source_symbol.get(key)
        rearmed = False
        if (
            self._config.connect_gate_s > 0
            and self._connect_gate_rearm_after_ms > 0
            and source_last is not None
            and symbol_last is not None
            and now_ms - source_last > self._connect_gate_rearm_after_ms
            and now_ms - symbol_last > self._connect_gate_rearm_after_ms
        ):
            self._connect_seen_ms[source_id] = now_ms
            rearmed = True
        self._last_seen_recv_ms_by_source[source_id] = now_ms
        self._last_seen_recv_ms_by_source_symbol[key] = now_ms
        return rearmed

    def _maybe_rearm_on_stale_drop(
        self,
        source_id: str,
        now_ms: int,
        *,
        wire_lag_ms: int,
        hard_max_wire_lag_ms: int,
    ) -> bool:
        if self._config.connect_gate_s <= 0:
            return False
        if wire_lag_ms > hard_max_wire_lag_ms * 2:
            self._connect_seen_ms[source_id] = now_ms
            return True
        series = self._stale_drop_recv_ms_by_source.setdefault(source_id, deque())
        series.append(now_ms)
        cutoff = now_ms - self._stale_burst_window_ms
        while series and series[0] < cutoff:
            series.popleft()
        if len(series) >= 2:
            self._connect_seen_ms[source_id] = now_ms
            series.clear()
            return True
        return False

    def _effective_max_excess_wire_lag_ms(self, source_id: str, now_ms: int) -> int:
        if self._config.connect_gate_s <= 0:
            return self._config.max_excess_wire_lag_ms
        first_seen = self._connect_seen_ms.get(source_id)
        if first_seen is None:
            return self._config.max_excess_wire_lag_ms
        if now_ms - first_seen <= self._config.connect_gate_s * 1000:
            return min(
                self._config.max_excess_wire_lag_ms,
                self._config.connect_gate_max_excess_wire_lag_ms,
            )
        return self._config.max_excess_wire_lag_ms

    def _effective_hard_max_wire_lag_ms(self, source_id: str, now_ms: int) -> int:
        if self._config.connect_gate_s <= 0:
            return self._config.hard_max_wire_lag_ms
        first_seen = self._connect_seen_ms.get(source_id)
        if first_seen is None:
            return self._config.hard_max_wire_lag_ms
        if now_ms - first_seen <= self._config.connect_gate_s * 1000:
            return min(
                self._config.hard_max_wire_lag_ms,
                self._config.connect_gate_hard_max_wire_lag_ms,
            )
        return self._config.hard_max_wire_lag_ms

    def _wire_lag_baseline(self, key: tuple[str, str], now_ms: int) -> tuple[float, bool]:
        samples = self._baseline_samples.setdefault(key, deque())
        self._evict_old_baseline_samples(samples, now_ms)
        if len(samples) < self._baseline_min_samples:
            return 0.0, False
        values = sorted(value for _, value in samples)
        return _percentile(values, 0.50), True

    def _maybe_sample_wire_lag_baseline(
        self,
        key: tuple[str, str],
        now_ms: int,
        wire_lag_ms: int,
    ) -> None:
        last_sample_ts = self._last_baseline_sample_ts_ms.get(key)
        if last_sample_ts is not None and now_ms - last_sample_ts < self._baseline_sample_interval_ms:
            return
        samples = self._baseline_samples.setdefault(key, deque())
        self._evict_old_baseline_samples(samples, now_ms)
        samples.append((now_ms, wire_lag_ms))
        while len(samples) > self._baseline_max_samples:
            samples.popleft()
        self._last_baseline_sample_ts_ms[key] = now_ms

    def _evict_old_baseline_samples(self, samples: deque[tuple[int, int]], now_ms: int) -> None:
        cutoff = now_ms - self._baseline_window_ms
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def _is_duplicate(self, key: tuple[str, str, str], now_ms: int) -> bool:
        self._evict_dedupe(now_ms)
        seen_ts = self._dedupe_seen.get(key)
        if seen_ts is not None and now_ms - seen_ts <= self._dedupe_ttl_ms:
            self._dedupe_seen.move_to_end(key)
            return True
        self._dedupe_seen[key] = now_ms
        self._dedupe_seen.move_to_end(key)
        if len(self._dedupe_seen) > self._dedupe_capacity:
            self._dedupe_seen.popitem(last=False)
        return False

    def _evict_dedupe(self, now_ms: int) -> None:
        cutoff = now_ms - self._dedupe_ttl_ms
        while self._dedupe_seen:
            _, seen_ts = next(iter(self._dedupe_seen.items()))
            if seen_ts >= cutoff:
                break
            self._dedupe_seen.popitem(last=False)

    def _ensure_bucket(self, key: tuple[str, str], now_ms: int) -> _HygieneBucket:
        bucket = self._buckets.get(key)
        if bucket is not None:
            if now_ms < bucket.interval_end_ms:
                return bucket
            symbol, source_id = key
            self._pending_metrics.append(_finalize_bucket(symbol, source_id, bucket))
        bucket_start = (now_ms // self._log_interval_ms) * self._log_interval_ms
        new_bucket = _HygieneBucket(
            interval_start_ms=bucket_start,
            interval_end_ms=bucket_start + self._log_interval_ms,
            wire_lags=[],
            stale_on_arrival_dropped=0,
            dedupe_dropped=0,
            venue_ts_missing=0,
            negative_wire_lag=0,
            future_venue_ts=0,
            connect_gate_rearm_inactivity=0,
            connect_gate_rearm_stale_burst=0,
        )
        self._buckets[key] = new_bucket
        return new_bucket


def _finalize_bucket(symbol: str, source_id: str, bucket: _HygieneBucket) -> HygieneMetricsEvent:
    wire_lags = sorted(bucket.wire_lags)
    samples_with_venue_ts = len(wire_lags)
    return HygieneMetricsEvent(
        symbol=symbol,
        source_id=source_id,
        interval_start_ms=bucket.interval_start_ms,
        interval_end_ms=bucket.interval_end_ms,
        samples_with_venue_ts=samples_with_venue_ts,
        wire_lag_ms_p50=_percentile(wire_lags, 0.50),
        wire_lag_ms_p95=_percentile(wire_lags, 0.95),
        stale_on_arrival_dropped=bucket.stale_on_arrival_dropped,
        dedupe_dropped=bucket.dedupe_dropped,
        venue_ts_missing=bucket.venue_ts_missing,
        negative_wire_lag=bucket.negative_wire_lag,
        future_venue_ts=bucket.future_venue_ts,
        connect_gate_rearm_inactivity=bucket.connect_gate_rearm_inactivity,
        connect_gate_rearm_stale_burst=bucket.connect_gate_rearm_stale_burst,
    )


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return float(values[0])
    if pct >= 1:
        return float(values[-1])
    idx = int(round(pct * (len(values) - 1)))
    return float(values[idx])
