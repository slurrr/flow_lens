from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable

from flow_lens.models.event import Event


@dataclass
class RollingEventBuffer:
    """Rolling event buffer keyed by time.

    window_delta_ms and Event.timestamp are milliseconds since epoch.
    """

    window_delta_ms: int
    spot_stale_switch_ticks: int = 3
    _events: Deque[Event] = field(default_factory=deque, init=False)
    _last_price: float | None = field(default=None, init=False)
    _last_price_timestamp: int | None = field(default=None, init=False)
    _last_spot_price: float | None = field(default=None, init=False)
    _last_spot_timestamp: int | None = field(default=None, init=False)
    _last_perp_price: float | None = field(default=None, init=False)
    _last_perp_timestamp: int | None = field(default=None, init=False)
    _last_spot_before_window: float | None = field(default=None, init=False)
    _last_spot_before_timestamp: int | None = field(default=None, init=False)
    _last_perp_before_window: float | None = field(default=None, init=False)
    _last_perp_before_timestamp: int | None = field(default=None, init=False)
    _price_series_side: str | None = field(default=None, init=False)
    _spot_stale_tick_count: int = field(default=0, init=False)

    def append(self, event: Event) -> None:
        self._events.append(event)
        self._last_price = event.price
        self._last_price_timestamp = event.timestamp
        if event.side_type == "spot":
            self._last_spot_price = event.price
            self._last_spot_timestamp = event.timestamp
        elif event.side_type == "perp":
            self._last_perp_price = event.price
            self._last_perp_timestamp = event.timestamp

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def expire(self, now_timestamp: int) -> None:
        cutoff = now_timestamp - self.window_delta_ms
        while self._events and self._events[0].timestamp < cutoff:
            expired = self._events.popleft()
            if expired.side_type == "spot":
                self._last_spot_before_window = expired.price
                self._last_spot_before_timestamp = expired.timestamp
            elif expired.side_type == "perp":
                self._last_perp_before_window = expired.price
                self._last_perp_before_timestamp = expired.timestamp

    def snapshot(self) -> tuple[Event, ...]:
        return tuple(self._events)

    @property
    def size(self) -> int:
        return len(self._events)

    @property
    def last_price(self) -> float | None:
        return self._last_price

    @property
    def last_price_timestamp(self) -> int | None:
        return self._last_price_timestamp

    def reference_price(self, now_timestamp: int) -> float | None:
        """
        Returns the last known trade price. Price is carried forward
        even if no events occurred within the current window.
        """
        if self._spot_fresh(now_timestamp):
            return self._last_spot_price
        if self._last_perp_price is not None:
            return self._last_perp_price
        if self._last_spot_price is not None:
            return self._last_spot_price
        return self._last_perp_price

    def window_price_range(self, now_timestamp: int) -> tuple[float | None, float | None, str]:
        spot_fresh = self._spot_fresh(now_timestamp)
        perp_fresh = self._perp_fresh(now_timestamp)
        side = self._select_price_series(spot_fresh=spot_fresh, perp_fresh=perp_fresh)
        if side is None:
            return None, None, "none"

        if side == "spot":
            start = self._window_start_price("spot")
            end = self._last_spot_price
            series = "spot" if spot_fresh else "spot_fallback"
        else:
            start = self._window_start_price("perp")
            end = self._last_perp_price
            series = "perp" if perp_fresh else "perp_fallback"
        if end is None:
            return None, None, series
        if start is None:
            start = end
        return start, end, series

    def window_counts(self, now_timestamp: int) -> tuple[int, int]:
        cutoff = now_timestamp - self.window_delta_ms
        spot_count = 0
        perp_count = 0
        for event in self._events:
            if event.timestamp < cutoff:
                continue
            if event.side_type == "spot":
                spot_count += 1
            elif event.side_type == "perp":
                perp_count += 1
        return spot_count, perp_count

    def last_event_timestamps(self) -> tuple[int | None, int | None]:
        return self._last_spot_timestamp, self._last_perp_timestamp

    def _spot_fresh(self, now_timestamp: int) -> bool:
        if self._last_spot_timestamp is None:
            return False
        cutoff = now_timestamp - self.window_delta_ms
        return self._last_spot_timestamp >= cutoff

    def _perp_fresh(self, now_timestamp: int) -> bool:
        if self._last_perp_timestamp is None:
            return False
        cutoff = now_timestamp - self.window_delta_ms
        return self._last_perp_timestamp >= cutoff

    def _window_start_price(self, side_type: str) -> float | None:
        for event in self._events:
            if event.side_type == side_type:
                return event.price
        if side_type == "spot":
            return self._last_spot_before_window
        return self._last_perp_before_window

    def _select_price_series(self, *, spot_fresh: bool, perp_fresh: bool) -> str | None:
        has_spot = self._last_spot_price is not None
        has_perp = self._last_perp_price is not None
        if not has_spot and not has_perp:
            self._price_series_side = None
            self._spot_stale_tick_count = 0
            return None

        if self._price_series_side is None:
            self._price_series_side = "spot" if has_spot else "perp"
            self._spot_stale_tick_count = 0

        if self._price_series_side == "spot":
            if not has_spot:
                self._price_series_side = "perp" if has_perp else None
                self._spot_stale_tick_count = 0
                return self._price_series_side
            if spot_fresh:
                self._spot_stale_tick_count = 0
                return "spot"
            self._spot_stale_tick_count += 1
            if has_perp and self._spot_stale_tick_count >= self.spot_stale_switch_ticks:
                self._price_series_side = "perp"
                self._spot_stale_tick_count = 0
                return "perp"
            return "spot"

        if has_spot and spot_fresh:
            self._price_series_side = "spot"
            self._spot_stale_tick_count = 0
            return "spot"
        if not has_perp and has_spot:
            self._price_series_side = "spot"
            self._spot_stale_tick_count = 0
            return "spot"
        return "perp"
