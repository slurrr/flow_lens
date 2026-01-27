from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from flow_lens.engine.aggregation import aggregate_events
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import Event, SideType
from flow_lens.models.flow_frame import EffortContribution, FlowFrame


@dataclass
class EngineLoop:
    symbol: str
    buffer: RollingEventBuffer
    engine: StateEngine

    def step(self, events: Iterable[Event], now_timestamp: int) -> StateSnapshot | None:
        step_events = tuple(events)
        if step_events:
            self.buffer.extend(step_events)
        self.buffer.expire(now_timestamp)

        price = self.buffer.reference_price(now_timestamp)
        if price is None:
            return None

        frame = flow_frame_from_events(
            symbol=self.symbol,
            timestamp=now_timestamp,
            price=price,
            events=step_events,
        )
        buffer_snapshot = self.buffer.snapshot()
        buffer_agg = aggregate_events(buffer_snapshot)
        buffer_total = buffer_agg.e_spot + buffer_agg.e_perp
        return self.engine.compute(
            frame,
            dispersion_sources=buffer_agg.per_source,
            effort_floor_total=buffer_total,
        )


def flow_frame_from_events(
    *,
    symbol: str,
    timestamp: int,
    price: float,
    events: Iterable[Event],
) -> FlowFrame:
    per_key: dict[tuple[str, SideType], float] = {}
    for event in events:
        key = (event.source_id, event.side_type)
        per_key[key] = per_key.get(key, 0.0) + event.effort_value

    efforts = [
        EffortContribution(source_id=source_id, side_type=side_type, effort_value=effort_value)
        for (source_id, side_type), effort_value in per_key.items()
    ]

    return FlowFrame(symbol=symbol, timestamp=timestamp, price=price, efforts=efforts)
