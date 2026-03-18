from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from flow_lens.engine.aggregation import aggregate_events
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.constants import ControlBaseline
from flow_lens.engine.control_baseline import DynamicControlBaseline
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import Event
from flow_lens.models.flow_frame import EffortContribution, FlowFrame


@dataclass
class EngineLoop:
    symbol: str
    buffer: RollingEventBuffer
    engine: StateEngine
    control_baseline: DynamicControlBaseline = field(
        default_factory=lambda: DynamicControlBaseline(ControlBaseline())
    )
    source_allowlist: set[str] | None = None

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

        price_start, price_end, price_series = self.buffer.window_price_range(now_timestamp)
        if price_end is None:
            self.control_baseline.update(0.0, now_timestamp, state_valid=False)
            return None
        if price_start is None:
            price_start = price_end

        spot_count, perp_count = self.buffer.window_counts(now_timestamp)
        last_spot_ts, last_perp_ts = self.buffer.last_event_timestamps()
        spot_fresh = spot_count > 0
        perp_fresh = perp_count > 0

        buffer_snapshot = self.buffer.snapshot(source_allowlist=self.source_allowlist)
        buffer_agg = aggregate_events(buffer_snapshot)
        frame = FlowFrame(
            symbol=self.symbol,
            timestamp=now_timestamp,
            price=price_end,
            price_start=price_start,
            window_seconds=self.buffer.window_delta_ms / 1000.0,
            active_price_source_id=self.buffer.active_price_source_id,
            selector_policy=self.buffer.selector_policy,
            price_series_side=self.buffer.price_series_side,
            price_series_used=price_series,
            spot_fresh=spot_fresh,
            perp_fresh=perp_fresh,
            spot_event_count_window=spot_count,
            perp_event_count_window=perp_count,
            last_spot_event_ts=last_spot_ts,
            last_perp_event_ts=last_perp_ts,
            e_dir=buffer_agg.e_dir,
            efforts=tuple(
                EffortContribution(
                    source_id=source_id,
                    side_type=side_type,
                    aggressor_side=aggressor_side,
                    effort_value=effort_value,
                )
                for (source_id, side_type, aggressor_side), effort_value in buffer_agg.per_key.items()
            ),
        )
        state = self.engine.compute(
            frame,
            dispersion_sources=buffer_agg.per_source,
        )
        baseline = self.control_baseline.update(state.x, now_timestamp, state_valid=True)
        return replace(
            state,
            control_baseline_enabled=baseline.enabled,
            control_baseline_initialized=baseline.initialized,
            control_baseline_x=baseline.baseline_x,
            control_baseline_target_x=baseline.target_x,
            control_baseline_mode=baseline.mode,
            control_baseline_breakout_age_s=baseline.breakout_age_s,
            control_baseline_delta=baseline.delta,
            control_baseline_visible=baseline.baseline_visible,
            control_baseline_midnight_tick_visible=baseline.midnight_tick_visible,
            control_baseline_midnight_tick_locked=baseline.midnight_tick_locked,
            control_baseline_midnight_tick_x=baseline.midnight_tick_x,
            control_baseline_midnight_tick_samples=baseline.midnight_tick_samples,
        )
