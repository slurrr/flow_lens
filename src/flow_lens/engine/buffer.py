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

    def append(self, event: Event) -> None:
        self._events.append(event)
        self._last_price = event.price
        self._last_price_timestamp = event.timestamp

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def expire(self, now_timestamp: int) -> None:
        cutoff = now_timestamp - self.window_delta_ms
        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()

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
        return self._last_price
