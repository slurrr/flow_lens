from __future__ import annotations

import curses
from dataclasses import dataclass
from typing import Any, TypeAlias

from flow_lens.adapters.base import AdapterStatus
from flow_lens.engine.state_engine import StateSnapshot

CursesWindow: TypeAlias = Any

@dataclass(frozen=True)
class RendererConfig:
    width: int = 49
    height: int = 21


class Renderer:
    def __init__(self, config: RendererConfig = RendererConfig()) -> None:
        self._config = config
        self._colors_ready = False

    def draw(
        self,
        stdscr: CursesWindow,
        symbol: str,
        state: StateSnapshot | None,
        *,
        status_spot: AdapterStatus | None = None,
        status_perp: AdapterStatus | None = None,
        search_mode: bool = False,
        search_buffer: str = "",
    ) -> None:
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()

        header = symbol if not search_mode else f"{symbol}   /{search_buffer}"
        self._draw_header(stdscr, header, status_spot, status_perp, maxx)

        map_top = 2
        map_left = max(0, (maxx - self._config.width) // 2)
        if map_top + self._config.height + 1 >= maxy:
            map_left = 0

        map_right = map_left + self._config.width - 1
        map_bottom = map_top + self._config.height - 1

        self._draw_labels(stdscr, map_top, map_left, map_right, map_bottom, maxy, maxx)
        self._draw_axes(stdscr, map_top, map_left)

        if state is None:
            stdscr.refresh()
            return

        base_x, base_y = _norm_to_grid(state.x, state.y, self._config.width, self._config.height)
        base_x += map_left
        base_y += map_top

        dot_x, dot_y = _apply_lean_offset(base_x, base_y, state.lean)
        self._draw_halo(stdscr, dot_x, dot_y, state.halo_bin, map_left, map_top, maxx, maxy)
        self._draw_dot(stdscr, dot_x, dot_y, state.size_bin, maxx, maxy)

        stdscr.refresh()

    def _draw_header(
        self,
        stdscr: CursesWindow,
        header: str,
        status_spot: AdapterStatus | None,
        status_perp: AdapterStatus | None,
        maxx: int,
    ) -> None:
        if status_spot is None and status_perp is None:
            stdscr.addstr(0, 0, header[: maxx - 1])
            return

        if curses.has_colors():
            self._ensure_colors()
            offset = 0
            for status in (status_spot, status_perp):
                if status is None:
                    continue
                color = _status_color(status)
                stdscr.addstr(0, offset, "●", curses.color_pair(color))
                offset += 1
            stdscr.addstr(0, offset, f" {header}"[: maxx - 1])
        else:
            prefix = "".join(
                _status_text(status)
                for status in (status_spot, status_perp)
                if status is not None
            )
            stdscr.addstr(0, 0, f"{prefix} {header}"[: maxx - 1])

    def _ensure_colors(self) -> None:
        if self._colors_ready:
            return
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        self._colors_ready = True

    def _draw_labels(
        self,
        stdscr: CursesWindow,
        map_top: int,
        map_left: int,
        map_right: int,
        map_bottom: int,
        maxy: int,
        maxx: int,
    ) -> None:
        top = "ACCEPTED"
        bot = "REJECTED"
        left = "PERP"
        right = "SPOT"

        tx = map_left + max(0, (self._config.width - len(top)) // 2)
        if map_top - 1 >= 1 and tx + len(top) < maxx:
            stdscr.addstr(map_top - 1, tx, top)

        bx = map_left + max(0, (self._config.width - len(bot)) // 2)
        if map_bottom + 1 < maxy and bx + len(bot) < maxx:
            stdscr.addstr(map_bottom + 1, bx, bot)

        if map_left - len(left) - 1 >= 0 and map_top + self._config.height // 2 < maxy:
            stdscr.addstr(map_top + self._config.height // 2, map_left - len(left) - 1, left)

        if map_right + 2 + len(right) < maxx and map_top + self._config.height // 2 < maxy:
            stdscr.addstr(map_top + self._config.height // 2, map_right + 2, right)

    def _draw_axes(self, stdscr: CursesWindow, map_top: int, map_left: int) -> None:
        cx = self._config.width // 2
        cy = self._config.height // 2

        for x in range(self._config.width):
            ch = "─" if x != cx else "┼"
            stdscr.addstr(map_top + cy, map_left + x, ch)
        for y in range(self._config.height):
            ch = "│" if y != cy else "┼"
            stdscr.addstr(map_top + y, map_left + cx, ch)

    def _draw_dot(
        self,
        stdscr: CursesWindow,
        x: int,
        y: int,
        size_bin: int,
        maxx: int,
        maxy: int,
    ) -> None:
        if not _in_bounds(x, y, maxx, maxy):
            return
        if size_bin >= 2:
            stdscr.addstr(y, x, _dot_char(size_bin), curses.A_BOLD)
        else:
            stdscr.addstr(y, x, _dot_char(size_bin))

    def _draw_halo(
        self,
        stdscr: CursesWindow,
        x: int,
        y: int,
        halo_bin: int,
        map_left: int,
        map_top: int,
        maxx: int,
        maxy: int,
    ) -> None:
        radius = _halo_radius(halo_bin)
        if radius == 0:
            return

        offsets = _ring_offsets(radius)
        for dx, dy in offsets:
            cx = x + dx
            cy = y + dy
            if not _in_bounds(cx, cy, maxx, maxy):
                continue
            if cx < map_left or cy < map_top:
                continue
            stdscr.addstr(cy, cx, ".")

def _norm_to_grid(xn: float, yn: float, width: int, height: int) -> tuple[int, int]:
    cx = width // 2
    cy = height // 2
    gx = int(round(cx + xn * (width // 2 - 1)))
    gy = int(round(cy - yn * (height // 2 - 1)))
    return _clamp(gx, 0, width - 1), _clamp(gy, 0, height - 1)


def _dot_char(size_bin: int) -> str:
    if size_bin <= 0:
        return "·"
    if size_bin == 1:
        return "◉"
    return "⬤"


def _status_color(status: AdapterStatus) -> int:
    if status == AdapterStatus.CONNECTED:
        return 1
    if status == AdapterStatus.STALE:
        return 2
    return 3


def _status_text(status: AdapterStatus) -> str:
    if status == AdapterStatus.CONNECTED:
        return "G"
    if status == AdapterStatus.STALE:
        return "Y"
    return "R"


def _halo_radius(halo_bin: int) -> int:
    if halo_bin <= 0:
        return 0
    if halo_bin == 1:
        return 1
    return 2


def _ring_offsets(radius: int) -> list[tuple[int, int]]:
    if radius == 1:
        return [
            (-1, -1),
            (0, -1),
            (1, -1),
            (-1, 0),
            (1, 0),
            (-1, 1),
            (0, 1),
            (1, 1),
        ]
    if radius == 2:
        return [
            (-2, -2),
            (-1, -2),
            (0, -2),
            (1, -2),
            (2, -2),
            (-2, -1),
            (2, -1),
            (-2, 0),
            (2, 0),
            (-2, 1),
            (2, 1),
            (-2, 2),
            (-1, 2),
            (0, 2),
            (1, 2),
            (2, 2),
        ]
    return []


def _apply_lean_offset(
    x: int, y: int, lean: tuple[int, int] | None
) -> tuple[int, int]:
    if lean is None:
        return x, y
    dx, dy = lean
    if dx == 0 and dy == 0:
        return x, y
    return x + dx, y - dy


def _in_bounds(x: int, y: int, maxx: int, maxy: int) -> bool:
    return 0 <= x < maxx and 0 <= y < maxy


def _clamp(value: int, lower: int, upper: int) -> int:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
