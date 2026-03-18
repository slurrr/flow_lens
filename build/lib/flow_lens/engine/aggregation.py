from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from flow_lens.models.event import AggressorSide, Event, SideType


@dataclass(frozen=True)
class EffortAggregation:
    e_spot: float
    e_perp: float
    e_dir: float
    per_source: Mapping[str, float]
    per_key: Mapping[tuple[str, SideType, AggressorSide], float]


def aggregate_events(events: Iterable[Event]) -> EffortAggregation:
    e_spot = 0.0
    e_perp = 0.0
    e_dir = 0.0
    per_source: dict[str, float] = {}
    per_key: dict[tuple[str, SideType, AggressorSide], float] = {}

    for event in events:
        if event.side_type == "spot":
            e_spot += event.effort_value
        elif event.side_type == "perp":
            e_perp += event.effort_value
        else:
            raise ValueError(f"Unknown side_type: {event.side_type}")

        if event.aggressor_side == "buy":
            e_dir += event.effort_value
        elif event.aggressor_side == "sell":
            e_dir -= event.effort_value
        else:
            raise ValueError(f"Unknown aggressor_side: {event.aggressor_side}")

        per_source[event.source_id] = per_source.get(event.source_id, 0.0) + event.effort_value
        key = (event.source_id, event.side_type, event.aggressor_side)
        per_key[key] = per_key.get(key, 0.0) + event.effort_value

    return EffortAggregation(
        e_spot=e_spot,
        e_perp=e_perp,
        e_dir=e_dir,
        per_source=MappingProxyType(per_source),
        per_key=MappingProxyType(per_key),
    )
