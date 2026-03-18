from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from flow_lens.models.event import AggressorSide, Event, SideType


@dataclass(frozen=True)
class MockEffort:
    source_id: str
    side_type: SideType
    aggressor_side: AggressorSide
    effort_value: float


@dataclass(frozen=True)
class MockStep:
    price: float
    efforts: Sequence[MockEffort]


class MockAdapter:
    def __init__(
        self,
        *,
        symbol: str,
        steps: Sequence[MockStep],
        start_timestamp_ms: int,
        step_ms: int,
    ) -> None:
        self._symbol = symbol
        self._steps = steps
        self._start_timestamp_ms = start_timestamp_ms
        self._step_ms = step_ms
        self._index = 0

    @property
    def symbol(self) -> str:
        return self._symbol

    def has_next(self) -> bool:
        return self._index < len(self._steps)

    def next_events(self) -> list[Event]:
        if not self.has_next():
            return []

        step = self._steps[self._index]
        timestamp = self._start_timestamp_ms + self._index * self._step_ms
        self._index += 1
        return [
            Event(
                timestamp=timestamp,
                source_id=effort.source_id,
                side_type=effort.side_type,
                aggressor_side=effort.aggressor_side,
                effort_value=effort.effort_value,
                price=step.price,
                base_qty=(
                    effort.effort_value / step.price
                    if step.price > 0
                    else None
                ),
                quote_qty=effort.effort_value,
            )
            for effort in step.efforts
        ]

    def __iter__(self) -> Iterator[list[Event]]:
        while self.has_next():
            yield self.next_events()


def steps_from_rows(
    rows: Iterable[tuple[float, Sequence[MockEffort]]],
) -> list[MockStep]:
    return [MockStep(price=price, efforts=efforts) for price, efforts in rows]
