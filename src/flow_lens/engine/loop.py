from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from flow_lens.engine.aggregation import aggregate_events
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import Event
from flow_lens.models.flow_frame import EffortContribution, FlowFrame


@dataclass
class EngineLoop:
    symbol: str
    buffer: RollingEventBuffer
    engine: StateEngine

    def step(
        self,
        events: Iterable[Event],
        now_timestamp: int,
        *,
        window_override_ms: int | None = None,
    ) -> StateSnapshot | None:
        if window_override_ms is not None:
            self.buffer.window_delta_ms = window_override_ms
        step_events = tuple(events)
        if step_events:
            self.buffer.extend(step_events)
        self.buffer.expire(now_timestamp)

        price_start, price_end = self.buffer.window_price_range(now_timestamp)
        if price_end is None:
            return None
        if price_start is None:
            price_start = price_end

        buffer_snapshot = self.buffer.snapshot()
        buffer_agg = aggregate_events(buffer_snapshot)
        frame = FlowFrame(
            symbol=self.symbol,
            timestamp=now_timestamp,
            price=price_end,
            price_start=price_start,
            efforts=tuple(
                EffortContribution(
                    source_id=source_id, side_type=side_type, effort_value=effort_value
                )
                for (source_id, side_type), effort_value in buffer_agg.per_key.items()
            ),
        )
        return self.engine.compute(
            frame,
            dispersion_sources=buffer_agg.per_source,
        )
