from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable, Mapping

from flow_lens.models.event import Event, SideType


@dataclass(frozen=True)
class PriceSourceMeta:
    source_id: str
    market_type_for_x: SideType
    price_eligible: bool
    price_priority: int


@dataclass(frozen=True)
class PriceSwitchEvent:
    from_source_id: str
    to_source_id: str
    reason: str
    staleness_from_ms: int | None
    staleness_to_ms: int | None
    priority_from: int
    priority_to: int
    selector_policy: str


@dataclass(frozen=True)
class PriceSelection:
    active_source_id: str | None
    selector_policy: str
    price_series_side: str
    price_series_used: str


@dataclass(frozen=True)
class _SourceSnapshot:
    source_id: str
    market_type_for_x: SideType
    price_eligible: bool
    price_priority: int
    last_price: float | None
    last_timestamp: int | None
    staleness_ms: int | None


class PriceSelectorPolicy:
    @property
    def name(self) -> str:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def select(
        self,
        *,
        now_timestamp: int,
        sources: Mapping[str, _SourceSnapshot],
    ) -> tuple[str | None, PriceSwitchEvent | None]:
        raise NotImplementedError


@dataclass
class PriorityStickySelector(PriceSelectorPolicy):
    stale_failover_ms: int = 6_000
    recovery_confirm_cycles: int = 2
    switch_cooldown_cycles: int = 1
    _active_source_id: str | None = field(default=None, init=False)
    _cooldown_remaining_cycles: int = field(default=0, init=False)
    _recovery_counts: dict[str, int] = field(default_factory=dict, init=False)

    @property
    def name(self) -> str:
        return "priority_sticky"

    def reset(self) -> None:
        self._active_source_id = None
        self._cooldown_remaining_cycles = 0
        self._recovery_counts.clear()

    def select(
        self,
        *,
        now_timestamp: int,
        sources: Mapping[str, _SourceSnapshot],
    ) -> tuple[str | None, PriceSwitchEvent | None]:
        if self._cooldown_remaining_cycles > 0:
            self._cooldown_remaining_cycles -= 1

        eligible = [
            source
            for source in sources.values()
            if source.price_eligible
            and source.last_price is not None
            and source.last_timestamp is not None
        ]
        if not eligible:
            self._active_source_id = None
            self._recovery_counts.clear()
            return None, None

        fresh = [
            source
            for source in eligible
            if source.staleness_ms is not None and source.staleness_ms <= self.stale_failover_ms
        ]
        best_fresh = self._best_source(fresh)
        active = sources.get(self._active_source_id) if self._active_source_id else None
        if active is None or active.last_price is None or active.last_timestamp is None:
            target = best_fresh or self._best_source(eligible)
            if target is None:
                self._active_source_id = None
                self._recovery_counts.clear()
                return None, None
            self._active_source_id = target.source_id
            self._recovery_counts.clear()
            return target.source_id, None

        active_stale = active.staleness_ms is None or active.staleness_ms > self.stale_failover_ms
        if active_stale:
            if best_fresh is not None and best_fresh.source_id != active.source_id:
                if self._cooldown_remaining_cycles > 0:
                    return active.source_id, None
                return self._switch(active=active, target=best_fresh, reason="stale")
            return active.source_id, None

        if best_fresh is None or best_fresh.source_id == active.source_id:
            self._recovery_counts.clear()
            return active.source_id, None

        if not self._is_better(candidate=best_fresh, active=active):
            self._recovery_counts.clear()
            return active.source_id, None

        count = self._recovery_counts.get(best_fresh.source_id, 0) + 1
        self._recovery_counts = {best_fresh.source_id: count}
        if count < self.recovery_confirm_cycles or self._cooldown_remaining_cycles > 0:
            return active.source_id, None

        reason = "recovered" if best_fresh.price_priority > active.price_priority else "priority"
        return self._switch(active=active, target=best_fresh, reason=reason)

    def _switch(
        self,
        *,
        active: _SourceSnapshot,
        target: _SourceSnapshot,
        reason: str,
    ) -> tuple[str, PriceSwitchEvent]:
        self._active_source_id = target.source_id
        self._cooldown_remaining_cycles = self.switch_cooldown_cycles
        self._recovery_counts.clear()
        event = PriceSwitchEvent(
            from_source_id=active.source_id,
            to_source_id=target.source_id,
            reason=reason,
            staleness_from_ms=active.staleness_ms,
            staleness_to_ms=target.staleness_ms,
            priority_from=active.price_priority,
            priority_to=target.price_priority,
            selector_policy=self.name,
        )
        return target.source_id, event

    def _best_source(self, sources: list[_SourceSnapshot]) -> _SourceSnapshot | None:
        if not sources:
            return None
        return sorted(
            sources,
            key=lambda item: (-item.price_priority, item.source_id),
        )[0]

    def _is_better(self, *, candidate: _SourceSnapshot, active: _SourceSnapshot) -> bool:
        if candidate.price_priority != active.price_priority:
            return candidate.price_priority > active.price_priority
        return candidate.source_id < active.source_id


@dataclass
class RollingEventBuffer:
    """Rolling event buffer keyed by time.

    window_delta_ms and Event.timestamp are milliseconds since epoch.
    """

    window_delta_ms: int
    source_meta: Mapping[str, PriceSourceMeta]
    price_selector: PriceSelectorPolicy = field(default_factory=PriorityStickySelector)
    _events: Deque[Event] = field(default_factory=deque, init=False)
    _last_price: float | None = field(default=None, init=False)
    _last_price_timestamp: int | None = field(default=None, init=False)
    _last_price_by_source: dict[str, float] = field(default_factory=dict, init=False)
    _last_timestamp_by_source: dict[str, int] = field(default_factory=dict, init=False)
    _last_before_window_by_source: dict[str, float] = field(default_factory=dict, init=False)
    _last_side_timestamps: dict[SideType, int | None] = field(
        default_factory=lambda: {"spot": None, "perp": None},
        init=False,
    )
    _source_side_by_id: dict[str, SideType] = field(default_factory=dict, init=False)
    _last_selection: PriceSelection | None = field(default=None, init=False)
    _price_switch_events: list[PriceSwitchEvent] = field(default_factory=list, init=False)

    def append(self, event: Event) -> None:
        self._events.append(event)
        self._last_price = event.price
        self._last_price_timestamp = event.timestamp
        self._last_price_by_source[event.source_id] = event.price
        self._last_timestamp_by_source[event.source_id] = event.timestamp
        self._source_side_by_id[event.source_id] = event.side_type
        self._last_side_timestamps[event.side_type] = event.timestamp

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def expire(self, now_timestamp: int) -> None:
        cutoff = now_timestamp - self.window_delta_ms
        while self._events and self._events[0].timestamp < cutoff:
            expired = self._events.popleft()
            self._last_before_window_by_source[expired.source_id] = expired.price

    def snapshot(self, source_allowlist: set[str] | None = None) -> tuple[Event, ...]:
        if source_allowlist is None:
            return tuple(self._events)
        return tuple(event for event in self._events if event.source_id in source_allowlist)

    @property
    def size(self) -> int:
        return len(self._events)

    @property
    def last_price(self) -> float | None:
        return self._last_price

    @property
    def last_price_timestamp(self) -> int | None:
        return self._last_price_timestamp

    @property
    def active_price_source_id(self) -> str | None:
        if self._last_selection is None:
            return None
        return self._last_selection.active_source_id

    @property
    def selector_policy(self) -> str:
        if self._last_selection is None:
            return self.price_selector.name
        return self._last_selection.selector_policy

    @property
    def price_series_side(self) -> str:
        if self._last_selection is None:
            return "none"
        return self._last_selection.price_series_side

    def reference_price(self, now_timestamp: int) -> float | None:
        """
        Returns the last known trade price. Price is carried forward
        even if no events occurred within the current window.
        """
        active_source_id, _ = self._select_active_source(now_timestamp)
        if active_source_id is None:
            return None
        return self._last_price_by_source.get(active_source_id)

    def window_price_range(self, now_timestamp: int) -> tuple[float | None, float | None, str]:
        active_source_id, selection = self._select_active_source(now_timestamp)
        self._last_selection = selection
        if active_source_id is None:
            return None, None, selection.price_series_used

        end = self._last_price_by_source.get(active_source_id)
        if end is None:
            return None, None, selection.price_series_used

        start = self._window_start_price(active_source_id)
        if start is None:
            start = end
        return start, end, selection.price_series_used

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
        return self._last_side_timestamps["spot"], self._last_side_timestamps["perp"]

    def pop_price_switch_events(self) -> tuple[PriceSwitchEvent, ...]:
        events = tuple(self._price_switch_events)
        self._price_switch_events.clear()
        return events

    def reset_context(self) -> None:
        self._events.clear()
        self._last_price = None
        self._last_price_timestamp = None
        self._last_price_by_source.clear()
        self._last_timestamp_by_source.clear()
        self._last_before_window_by_source.clear()
        self._last_side_timestamps = {"spot": None, "perp": None}
        self._source_side_by_id.clear()
        self._last_selection = None
        self._price_switch_events.clear()
        self.price_selector.reset()

    def _window_start_price(self, source_id: str) -> float | None:
        for event in self._events:
            if event.source_id == source_id:
                return event.price
        return self._last_before_window_by_source.get(source_id)

    def _select_active_source(self, now_timestamp: int) -> tuple[str | None, PriceSelection]:
        snapshots = self._build_source_snapshots(now_timestamp)
        active_source_id, switch_event = self.price_selector.select(
            now_timestamp=now_timestamp,
            sources=snapshots,
        )
        if switch_event is not None:
            self._price_switch_events.append(switch_event)

        side = "none"
        if active_source_id is not None:
            active = snapshots.get(active_source_id)
            if active is not None:
                side = active.market_type_for_x

        selection = PriceSelection(
            active_source_id=active_source_id,
            selector_policy=self.price_selector.name,
            price_series_side=side,
            price_series_used=side,
        )
        return active_source_id, selection

    def _build_source_snapshots(self, now_timestamp: int) -> dict[str, _SourceSnapshot]:
        snapshots: dict[str, _SourceSnapshot] = {}
        source_ids = set(self.source_meta.keys())
        source_ids.update(self._last_timestamp_by_source.keys())
        for source_id in source_ids:
            meta = self.source_meta.get(source_id)
            market_type_for_x: SideType
            if meta is None:
                market_type_for_x = self._source_side_by_id.get(source_id, "spot")
            else:
                market_type_for_x = meta.market_type_for_x
            last_timestamp = self._last_timestamp_by_source.get(source_id)
            staleness_ms: int | None
            if last_timestamp is None:
                staleness_ms = None
            else:
                staleness_ms = max(0, now_timestamp - last_timestamp)
            snapshots[source_id] = _SourceSnapshot(
                source_id=source_id,
                market_type_for_x=market_type_for_x,
                price_eligible=meta.price_eligible if meta is not None else False,
                price_priority=meta.price_priority if meta is not None else 0,
                last_price=self._last_price_by_source.get(source_id),
                last_timestamp=last_timestamp,
                staleness_ms=staleness_ms,
            )
        return snapshots
