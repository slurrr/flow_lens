from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Iterable

from flow_lens.adapters.mock import MockAdapter, MockEffort, MockStep
from flow_lens.engine.buffer import RollingEventBuffer
from flow_lens.engine.constants import Defaults
from flow_lens.engine.loop import EngineLoop
from flow_lens.engine.state_engine import StateEngine, StateSnapshot
from flow_lens.models.event import AggressorSide
from flow_lens.tui.input import InputState
from flow_lens.tui.renderer import Renderer, RendererConfig


@dataclass(frozen=True)
class StepSpec:
    x: float
    y: float
    effort_total: float
    source_weights: tuple[float, ...]


def main() -> None:
    curses.wrapper(_run)


def _run(stdscr: "curses.window") -> None:
    stdscr.nodelay(True)
    stdscr.keypad(True)
    curses.curs_set(0)

    defaults = Defaults()
    update_hz = 2.0
    fps = 30.0
    window_ms = int(defaults.time_domain.update_window_seconds * 1000)
    sim_step_ms = window_ms

    scenarios = _build_storyboard_scenarios()
    input_state = InputState(symbols=list(scenarios.keys()))

    renderer = Renderer(RendererConfig())
    loop, adapter, now_timestamp = _init_scenario(
        input_state.symbol,
        scenarios[input_state.symbol],
        window_ms,
        sim_step_ms,
    )
    last_state: StateSnapshot | None = None
    last_update = time.monotonic()
    last_frame = last_update

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        if key != -1:
            previous_symbol = input_state.symbol
            input_state.handle_key(key)
            if input_state.symbol != previous_symbol:
                loop, adapter, now_timestamp = _init_scenario(
                    input_state.symbol,
                    scenarios[input_state.symbol],
                    window_ms,
                    sim_step_ms,
                )
                last_state = None

        now = time.monotonic()
        if now - last_update >= 1.0 / update_hz:
            last_update = now
            if not adapter.has_next():
                loop, adapter, now_timestamp = _init_scenario(
                    input_state.symbol,
                    scenarios[input_state.symbol],
                    window_ms,
                    sim_step_ms,
                )
                last_state = None
            events = adapter.next_events()
            last_state = loop.step(events, now_timestamp)
            now_timestamp += sim_step_ms

        if now - last_frame >= 1.0 / fps:
            last_frame = now
            renderer.draw(
                stdscr,
                input_state.symbol,
                last_state,
                status_spot=None,
                status_perp=None,
                spot_stats=None,
                perp_stats=None,
                search_mode=input_state.search_mode,
                search_buffer=input_state.search_buffer,
            )

        time.sleep(0.001)


def _init_scenario(
    name: str,
    steps: Iterable[StepSpec],
    window_ms: int,
    sim_step_ms: int,
) -> tuple[EngineLoop, MockAdapter, int]:
    now_ms = int(time.time() * 1000)
    mock_steps = _steps_from_specs(steps)
    adapter = MockAdapter(
        symbol=name,
        steps=mock_steps,
        start_timestamp_ms=now_ms,
        step_ms=sim_step_ms,
    )
    buffer = RollingEventBuffer(window_delta_ms=window_ms)
    engine = StateEngine()
    loop = EngineLoop(symbol=name, buffer=buffer, engine=engine)
    return loop, adapter, now_ms


def _steps_from_specs(specs: Iterable[StepSpec]) -> list[MockStep]:
    price = 100.0
    steps: list[MockStep] = []
    for spec in specs:
        efforts, price = _efforts_for_step(spec, price)
        steps.append(MockStep(price=price, efforts=efforts))
    return steps


def _efforts_for_step(spec: StepSpec, price: float) -> tuple[list[MockEffort], float]:
    total = max(spec.effort_total, 0.0)
    x = _clamp(spec.x, -0.95, 0.95)
    y = _clamp(spec.y, -0.95, 0.95)

    dominance = x * total
    e_spot = (total + dominance) / 2.0
    ratio_spot = e_spot / total if total > 0 else 0.5
    ratio_perp = 1.0 - ratio_spot

    eff_raw = _atanh(y)
    disp = eff_raw * total
    if dominance < 0:
        disp = -disp
    aggressor_side: AggressorSide = _aggressor_side(y, disp)

    weights = spec.source_weights if spec.source_weights else (1.0,)
    weight_sum = sum(weights) or 1.0
    efforts: list[MockEffort] = []
    for idx, weight in enumerate(weights, start=1):
        source_total = total * (weight / weight_sum)
        spot_value = source_total * ratio_spot
        perp_value = source_total * ratio_perp
        source_id = f"src{idx}"
        if spot_value > 0:
            efforts.append(
                MockEffort(
                    source_id=source_id,
                    side_type="spot",
                    aggressor_side=aggressor_side,
                    effort_value=spot_value,
                )
            )
        if perp_value > 0:
            efforts.append(
                MockEffort(
                    source_id=source_id,
                    side_type="perp",
                    aggressor_side=aggressor_side,
                    effort_value=perp_value,
                )
            )
    price = price + disp
    return efforts, price


def _aggressor_side(y: float, disp: float) -> AggressorSide:
    if disp == 0.0:
        return "buy"
    if y >= 0:
        return "buy" if disp > 0 else "sell"
    return "sell" if disp > 0 else "buy"


def _build_storyboard_scenarios() -> dict[str, list[StepSpec]]:
    return {
        "Trap": [
            StepSpec(x=-0.2, y=0.0, effort_total=40.0, source_weights=(1.0,)),
            StepSpec(x=-0.4, y=-0.2, effort_total=60.0, source_weights=(1.0,)),
            StepSpec(x=-0.6, y=-0.4, effort_total=80.0, source_weights=(0.6, 0.25, 0.15)),
            StepSpec(x=-0.8, y=-0.6, effort_total=100.0, source_weights=(0.4, 0.3, 0.2, 0.1)),
            StepSpec(x=-0.8, y=-0.7, effort_total=120.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.8, y=-0.6, effort_total=120.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.7, y=-0.5, effort_total=110.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.6, y=-0.3, effort_total=100.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.5, y=-0.2, effort_total=90.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.4, y=-0.1, effort_total=80.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.2, y=0.2, effort_total=70.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.2, y=0.1, effort_total=70.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.1, y=0.1, effort_total=65.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.0, y=0.2, effort_total=60.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.0, y=0.1, effort_total=60.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.1, y=0.2, effort_total=55.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.1, y=0.1, effort_total=55.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.2, y=0.4, effort_total=60.0, source_weights=(0.7, 0.2, 0.1)),
            StepSpec(x=0.4, y=0.5, effort_total=50.0, source_weights=(1.0,)),
        ],
        "Continuation": [
            StepSpec(x=0.2, y=0.1, effort_total=40.0, source_weights=(1.0,)),
            StepSpec(x=0.4, y=0.4, effort_total=60.0, source_weights=(1.0,)),
            StepSpec(x=0.6, y=0.6, effort_total=80.0, source_weights=(1.0,)),
            StepSpec(x=0.8, y=0.7, effort_total=90.0, source_weights=(0.7, 0.2, 0.1)),
            StepSpec(x=0.8, y=0.6, effort_total=100.0, source_weights=(0.4, 0.3, 0.2, 0.1)),
            StepSpec(x=0.8, y=0.6, effort_total=100.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.7, y=0.5, effort_total=95.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.6, y=0.4, effort_total=90.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.5, y=0.3, effort_total=85.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.4, y=0.2, effort_total=80.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.3, y=0.2, effort_total=70.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.2, y=0.1, effort_total=60.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.2, y=0.1, effort_total=55.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.1, y=0.0, effort_total=50.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.1, y=0.0, effort_total=50.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.1, y=0.0, effort_total=45.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=0.0, y=0.0, effort_total=50.0, source_weights=(1.0,)),
        ],
        "Squeeze": [
            StepSpec(x=-0.2, y=0.2, effort_total=50.0, source_weights=(1.0,)),
            StepSpec(x=-0.5, y=0.5, effort_total=70.0, source_weights=(1.0,)),
            StepSpec(x=-0.8, y=0.7, effort_total=90.0, source_weights=(0.7, 0.2, 0.1)),
            StepSpec(x=-0.9, y=0.8, effort_total=110.0, source_weights=(0.4, 0.3, 0.2, 0.1)),
            StepSpec(x=-0.9, y=0.8, effort_total=120.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.9, y=0.7, effort_total=120.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.8, y=0.6, effort_total=110.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.7, y=0.5, effort_total=100.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.6, y=0.4, effort_total=90.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.5, y=0.3, effort_total=80.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.4, y=0.2, effort_total=70.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.3, y=0.2, effort_total=65.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.2, y=0.1, effort_total=60.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.2, y=0.1, effort_total=55.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.1, y=0.1, effort_total=50.0, source_weights=(0.25, 0.25, 0.25, 0.25)),
            StepSpec(x=-0.2, y=0.1, effort_total=60.0, source_weights=(1.0,)),
            StepSpec(x=0.0, y=0.0, effort_total=50.0, source_weights=(1.0,)),
        ],
        "Air Pocket": [
            StepSpec(x=0.05, y=0.1, effort_total=30.0, source_weights=(1.0,)),
            StepSpec(x=0.08, y=0.2, effort_total=25.0, source_weights=(1.0,)),
            StepSpec(x=0.1, y=0.5, effort_total=12.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.12, y=0.6, effort_total=10.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.12, y=0.6, effort_total=9.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.12, y=0.6, effort_total=9.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.1, y=0.6, effort_total=8.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.1, y=0.5, effort_total=8.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.08, y=0.4, effort_total=7.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.06, y=0.3, effort_total=6.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.05, y=0.2, effort_total=6.0, source_weights=(0.5, 0.5)),
            StepSpec(x=0.0, y=0.0, effort_total=5.0, source_weights=(1.0,)),
        ],
    }


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _atanh(value: float) -> float:
    from math import log

    return 0.5 * log((1.0 + value) / (1.0 - value))


if __name__ == "__main__":
    main()
