#!/usr/bin/env python3
"""
Flow Lens — faux TUI prototype (single graphic, glanceable)

Keys:
  ←/→ or h/l : cycle predefined symbols
  /          : search/jump (type, Enter to select, Esc to cancel)
  q          : quit
Behavior:
  - Position encodes state (control × effectiveness)
  - Dot size encodes dominance magnitude
  - Halo encodes participation
  - Lean appears only on update (1–2 frames), then settles (no idle animation)
"""
import curses
import random
import time
from dataclasses import dataclass

# ----------------------------
# Config
# ----------------------------
SYMBOLS = [
    "BTC-PERP", "ETH-PERP", "SOL-PERP", "XRP-PERP", "BNB-PERP",
    "DOGE-PERP", "AVAX-PERP", "LINK-PERP", "ARB-PERP", "OP-PERP",
]

W = 49   # map width (odd preferred)
H = 21   # map height (odd preferred)

UPDATE_HZ = 2.0          # state updates per second
FPS = 30.0               # render refresh rate
LEAN_FRAMES = 2          # frames to show the lean after an update
SMOOTH_SIZE = 0.15       # dot size smoothing (lower = smoother)
SMOOTH_HALO = 0.12       # halo smoothing

# ----------------------------
# Model
# ----------------------------
@dataclass
class State:
    x: float            # -1..+1  (perp..spot)
    y: float            # -1..+1  (rejected..accepted)
    dom_mag: float      # 0..1    (magnitude of dominance)
    participation: float# 0..1    (participation strength)

@dataclass
class Visual:
    dot_level: float    # 0..1 (smoothed)
    halo_level: float   # 0..1 (smoothed)
    lean_dx: float      # -1..+1 direction only
    lean_dy: float      # -1..+1 direction only
    lean_frames_left: int

# ----------------------------
# Helpers
# ----------------------------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def lerp(a, b, t):
    return a + (b - a) * t

def norm_to_grid(xn, yn):
    """Map normalized [-1,1] coords to grid indices."""
    cx = W // 2
    cy = H // 2
    # x: -1 left -> 0, +1 right -> W-1
    gx = int(round(cx + xn * (W // 2 - 1)))
    # y: +1 up -> 0, -1 down -> H-1
    gy = int(round(cy - yn * (H // 2 - 1)))
    return clamp(gx, 0, W - 1), clamp(gy, 0, H - 1)

def dot_char(level):
    """Choose a dot glyph by strength."""
    # Prefer simple, widely-supported glyphs
    if level < 0.33:
        return "•"  # light
    elif level < 0.66:
        return "●"  # medium
    else:
        return "⬤"  # heavy (may degrade on some terminals; fallback handled below)

def safe_glyph(g):
    # In case terminal can't render '⬤', swap to '●'
    return "●" if g == "⬤" else g

def halo_radius(level):
    """Halo radius in cells (0..2)."""
    if level < 0.25:
        return 0
    elif level < 0.60:
        return 1
    else:
        return 2

def step_symbol(idx, direction, n):
    return (idx + direction) % n

# ----------------------------
# Faux data generator (per symbol)
# ----------------------------
class SymbolSim:
    """
    Produces plausible-ish state updates:
      x = control (perp..spot)
      y = effectiveness (rejected..accepted)
      dom_mag = strength of dominance
      participation = overall activity
    """
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.state = State(
            x=self.rng.uniform(-0.2, 0.2),
            y=self.rng.uniform(-0.2, 0.2),
            dom_mag=self.rng.uniform(0.2, 0.6),
            participation=self.rng.uniform(0.2, 0.6),
        )

    def tick(self):
        s = self.state

        # control drift (bounded random walk with slight mean reversion)
        dx = self.rng.gauss(0, 0.10) - 0.08 * s.x
        dy = self.rng.gauss(0, 0.10) - 0.08 * s.y

        # participation pulses
        dp = self.rng.gauss(0, 0.08) - 0.06 * (s.participation - 0.45)

        # dominance magnitude correlates with participation + occasional bursts
        burst = 0.0
        if self.rng.random() < 0.08:
            burst = self.rng.uniform(0.15, 0.35) * (1 if self.rng.random() < 0.5 else -1)

        # Apply
        new_x = clamp(s.x + dx, -1, 1)
        new_y = clamp(s.y + dy, -1, 1)
        new_part = clamp(s.participation + dp, 0, 1)

        # Dominance magnitude: activity-driven (0..1)
        base_mag = clamp(0.15 + 0.85 * new_part + self.rng.gauss(0, 0.06), 0, 1)
        new_mag = clamp(base_mag + abs(burst), 0, 1)

        self.state = State(new_x, new_y, new_mag, new_part)
        return self.state

# ----------------------------
# Renderer
# ----------------------------
def draw(stdscr, symbol, st: State, vis: Visual, search_mode, search_buf):
    stdscr.erase()
    maxy, maxx = stdscr.getmaxyx()

    # Header: always show symbol (required)
    header = symbol if not search_mode else f"{symbol}   /{search_buf}"
    stdscr.addstr(0, 0, header[: maxx - 1])

    # Labels (minimal descriptors)
    # Keep these tight and consistent.
    # Vertical: ACCEPTED / REJECTED ; Horizontal: PERP / SPOT
    top = "ACCEPTED"
    bot = "REJECTED"
    left = "PERP"
    right = "SPOT"

    # Compute map origin (centered)
    map_top = 2
    map_left = max(0, (maxx - W) // 2)
    if map_top + H + 1 >= maxy:
        # If terminal is small, anchor top-left
        map_left = 0
    map_right = map_left + W - 1
    map_bottom = map_top + H - 1

    # Draw labels (outside map, minimal)
    # Top label centered
    tx = map_left + max(0, (W - len(top)) // 2)
    if map_top - 1 >= 1 and tx + len(top) < maxx:
        stdscr.addstr(map_top - 1, tx, top)
    # Bottom label centered
    bx = map_left + max(0, (W - len(bot)) // 2)
    if map_bottom + 1 < maxy and bx + len(bot) < maxx:
        stdscr.addstr(map_bottom + 1, bx, bot)
    # Left label
    if map_left - len(left) - 1 >= 0 and map_top + H // 2 < maxy:
        stdscr.addstr(map_top + H // 2, map_left - len(left) - 1, left)
    # Right label
    if map_right + 2 + len(right) < maxx and map_top + H // 2 < maxy:
        stdscr.addstr(map_top + H // 2, map_right + 2, right)

    # Axes
    cx = W // 2
    cy = H // 2
    for x in range(W):
        ch = "─" if x != cx else "┼"
        stdscr.addstr(map_top + cy, map_left + x, ch)
    for y in range(H):
        ch = "│" if y != cy else "┼"
        stdscr.addstr(map_top + y, map_left + cx, ch)

    # Determine lean position (direction only, not magnitude), used for 1–2 frames only
    use_lean = vis.lean_frames_left > 0
    lean_x = st.x + (0.10 * vis.lean_dx if use_lean else 0.0)
    lean_y = st.y + (0.10 * vis.lean_dy if use_lean else 0.0)
    gx, gy = norm_to_grid(lean_x, lean_y)

    # Choose dot + halo by smoothed levels
    dch = safe_glyph(dot_char(vis.dot_level))
    hr = halo_radius(vis.halo_level)

    # Halo: very faint ring of '·' around dot (no interpretation required)
    # Keep it subtle and sparse.
    if hr > 0:
        for oy in range(-hr, hr + 1):
            for ox in range(-hr, hr + 1):
                if ox == 0 and oy == 0:
                    continue
                # ring-like: prefer perimeter
                if max(abs(ox), abs(oy)) != hr:
                    continue
                px = gx + ox
                py = gy + oy
                if 0 <= px < W and 0 <= py < H:
                    stdscr.addstr(map_top + py, map_left + px, "·")

    # Dot
    stdscr.addstr(map_top + gy, map_left + gx, dch)

    # Minimal help only in search mode (still minimal)
    if search_mode and map_bottom + 3 < maxy:
        hint = "Enter=select  Esc=cancel"
        hx = map_left + max(0, (W - len(hint)) // 2)
        if hx + len(hint) < maxx:
            stdscr.addstr(map_bottom + 3, hx, hint)

    stdscr.refresh()

# ----------------------------
# Main loop
# ----------------------------
def run(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    # Pre-create sims per symbol
    sims = {s: SymbolSim(seed=hash(s) & 0xFFFFFFFF) for s in SYMBOLS}

    idx = 0
    symbol = SYMBOLS[idx]
    st = sims[symbol].state

    vis = Visual(dot_level=st.dom_mag, halo_level=st.participation, lean_dx=0.0, lean_dy=0.0, lean_frames_left=0)

    search_mode = False
    search_buf = ""

    next_update = time.time()
    frame_dt = 1.0 / FPS
    update_dt = 1.0 / UPDATE_HZ

    last = time.time()
    prev_state = st

    while True:
        now = time.time()

        # Input
        try:
            key = stdscr.getch()
        except Exception:
            key = -1

        if key != -1:
            if search_mode:
                if key in (27,):  # Esc
                    search_mode = False
                    search_buf = ""
                elif key in (curses.KEY_ENTER, 10, 13):
                    # select
                    q = search_buf.strip().upper()
                    if q:
                        # if exact match in list, jump; else try partial match; else add
                        candidates = [s for s in SYMBOLS if s.upper() == q]
                        if not candidates:
                            candidates = [s for s in SYMBOLS if q in s.upper()]
                        if candidates:
                            symbol = candidates[0]
                            idx = SYMBOLS.index(symbol)
                        else:
                            # add new symbol to list (keeps tool usable)
                            SYMBOLS.append(q)
                            sims[q] = SymbolSim(seed=hash(q) & 0xFFFFFFFF)
                            idx = SYMBOLS.index(q)
                            symbol = q
                        st = sims[symbol].state
                        prev_state = st
                    search_mode = False
                    search_buf = ""
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    search_buf = search_buf[:-1]
                else:
                    # accept printable
                    if 32 <= key <= 126:
                        ch = chr(key)
                        # keep it simple: allow [A-Z0-9-_/]
                        if ch.isalnum() or ch in "-_/":
                            search_buf += ch
            else:
                if key in (ord('q'), ord('Q')):
                    return
                elif key in (curses.KEY_LEFT, ord('h')):
                    idx = step_symbol(idx, -1, len(SYMBOLS))
                    symbol = SYMBOLS[idx]
                    st = sims[symbol].state
                    prev_state = st
                    vis.lean_frames_left = 0
                elif key in (curses.KEY_RIGHT, ord('l')):
                    idx = step_symbol(idx, +1, len(SYMBOLS))
                    symbol = SYMBOLS[idx]
                    st = sims[symbol].state
                    prev_state = st
                    vis.lean_frames_left = 0
                elif key == ord('/'):
                    search_mode = True
                    search_buf = ""

        # Update model state
        if now >= next_update:
            next_update += update_dt

            prev_state = st
            st = sims[symbol].tick()

            # Lean direction only (sign), for 1–2 frames on update
            dx = st.x - prev_state.x
            dy = st.y - prev_state.y
            # Convert to direction (avoid jitter near zero)
            eps = 1e-3
            vis.lean_dx = 0.0 if abs(dx) < eps else (1.0 if dx > 0 else -1.0)
            vis.lean_dy = 0.0 if abs(dy) < eps else (1.0 if dy > 0 else -1.0)
            vis.lean_frames_left = LEAN_FRAMES

        # Smooth size/halo (stable, low flicker)
        vis.dot_level = clamp(lerp(vis.dot_level, st.dom_mag, SMOOTH_SIZE), 0, 1)
        vis.halo_level = clamp(lerp(vis.halo_level, st.participation, SMOOTH_HALO), 0, 1)

        # Render
        draw(stdscr, symbol, st, vis, search_mode, search_buf)

        # Lean frame countdown (only after render tick)
        if vis.lean_frames_left > 0:
            vis.lean_frames_left -= 1

        # Frame pacing
        elapsed = time.time() - last
        sleep_for = frame_dt - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
        last = time.time()

def main():
    curses.wrapper(run)

if __name__ == "__main__":
    main()
