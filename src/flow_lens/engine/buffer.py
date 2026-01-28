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

    def window_price_range(self, now_timestamp: int) -> tuple[float | None, float | None]:
        spot_fresh = self._spot_fresh(now_timestamp)
        perp_fresh = self._perp_fresh(now_timestamp)
        if spot_fresh:
            side = "spot"
        elif perp_fresh:
            side = "perp"
        elif self._last_spot_price is not None:
            side = "spot"
        elif self._last_perp_price is not None:
            side = "perp"
        else:
            return None, None

        if side == "spot":
            start = self._window_start_price("spot")
            end = self._last_spot_price
        else:
            start = self._window_start_price("perp")
            end = self._last_perp_price
        if end is None:
            return None, None
        if start is None:
            start = end
        return start, end

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
