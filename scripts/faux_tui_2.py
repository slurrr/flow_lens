#!/usr/bin/env python3
"""
flow_lens_faux_tui.py — corrected

Behavior:
- Axes are static
- Dot position moves slowly
- Effective force  -> dot jitters (micro movement)
- Absorbed force   -> dot pulses (size only)
"""

from dataclasses import dataclass
import math
import random
import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

# ---------------- config ----------------

W, H = 61, 21
FPS = 12

DOTS = ["·", "●", "⬤"]     # size levels
JITTER = 1                # chars
PULSE_HZ = 1.1
DRIFT_HZ = 0.04

# ---------------- model ----------------

@dataclass
class State:
    x: float          # [-1,1]
    y: float          # [-1,1]
    participation: float
    absorbed: bool

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def plane_coords(x, y):
    cx = int(round((x + 1) * 0.5 * (W - 1)))
    cy = int(round((1 - (y + 1) * 0.5) * (H - 1)))
    return clamp(cx, 0, W - 1), clamp(cy, 0, H - 1)

# ---------------- render ----------------

def render(state: State, t: float) -> Text:
    grid = [[" " for _ in range(W)] for _ in range(H)]

    mx, my = W // 2, H // 2

    # axes
    for x in range(W):
        grid[my][x] = "─"
    for y in range(H):
        grid[y][mx] = "│"
    grid[my][mx] = "┼"

    # axis hints
    grid[0][mx] = "↑"
    grid[H - 1][mx] = "↓"
    grid[my][0] = "←"
    grid[my][W - 1] = "→"

    # base size
    p = clamp(state.participation, 0, 1)
    base_size = 0 if p < 0.34 else 1 if p < 0.67 else 2

    # behavior
    if state.absorbed:
        phase = (math.sin(2 * math.pi * PULSE_HZ * t) + 1) * 0.5
        delta = -1 if phase < 0.33 else (1 if phase > 0.66 else 0)
        size = clamp(base_size + delta, 0, 2)
        jx = jy = 0
    else:
        size = base_size
        direction = 1 if state.x >= 0 else -1
        jx = random.choice([0, direction]) * JITTER
        jy = 0

    cx, cy = plane_coords(state.x, state.y)
    cx = clamp(cx + jx, 0, W - 1)
    cy = clamp(cy + jy, 0, H - 1)

    grid[cy][cx] = DOTS[size]

    body = "\n".join("".join(r) for r in grid)
    mode = "ABSORB → pulse" if state.absorbed else "TRANSMIT → jitter"

    footer = (
        f"\n   participation: {p:.2f}   mode: {mode}"
        f"\n   ← perp-led | spot-led →     ↓ ineffective | effective ↑"
    )

    return Text(body + footer)

# ---------------- fake dynamics ----------------

def evolve(prev: State, t: float) -> State:
    # slow visible movement
    x = math.sin(2 * math.pi * DRIFT_HZ * t) * 0.85
    y = math.cos(2 * math.pi * DRIFT_HZ * t * 0.8) * 0.65

    # participation swells near “events”
    swell = (math.sin(2 * math.pi * 0.05 * t - 1.2) + 1) * 0.5
    participation = 0.25 + 0.75 * swell**2

    # absorption in blocks
    block = int(t // 8)
    random.seed(block)
    absorbed = random.random() < 0.45

    if absorbed:
        y *= 0.3  # pressure fails to translate

    return State(x=x, y=y, participation=participation, absorbed=absorbed)

# ---------------- main ----------------

def main():
    console = Console()
    state = State(0, 0, 0.4, False)
    start = time.time()

    with Live(render(state, 0), console=console, refresh_per_second=FPS) as live:
        while True:
            t = time.time() - start
            state = evolve(state, t)
            live.update(render(state, t))
            time.sleep(1 / FPS)

if __name__ == "__main__":
    main()
