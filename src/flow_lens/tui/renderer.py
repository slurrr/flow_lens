from __future__ import annotations

import curses
from dataclasses import dataclass
from typing import Any, TypeAlias

from flow_lens.adapters.base import AdapterStats, AdapterStatus
from flow_lens.engine.state_engine import StateSnapshot
from flow_lens.tui.metrics import LiveMetricsSnapshot

CursesWindow: TypeAlias = Any

@dataclass(frozen=True)
class RendererConfig:
    width: int = 49
    height: int = 21


class Renderer:
    def __init__(self, config: RendererConfig = RendererConfig()) -> None:
        self._config = config
        self._colors_ready = False
        self._last_y: float | None = None
        self._last_state_id: int | None = None

    def draw(
        self,
        stdscr: CursesWindow,
        symbol: str,
        state: StateSnapshot | None,
        *,
        status_spot: AdapterStatus | None = None,
        status_perp: AdapterStatus | None = None,
        spot_stats: AdapterStats | None = None,
        perp_stats: AdapterStats | None = None,
        metrics: LiveMetricsSnapshot | None = None,
        search_mode: bool = False,
        search_buffer: str = "",
    ) -> None:
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()

        header = symbol if not search_mode else f"{symbol}   /{search_buffer}"
        self._draw_header(stdscr, header, maxx, row=0)

        map_top = 2
        map_left = max(0, (maxx - self._config.width) // 2)
        if map_top + self._config.height + 1 >= maxy:
            map_left = 0

        map_right = map_left + self._config.width - 1
        map_bottom = map_top + self._config.height - 1

        self._draw_axes(stdscr, map_top, map_left)

        if state is None:
            self._last_y = None
            self._last_state_id = None
            self._draw_labels(
                stdscr,
                map_top,
                map_left,
                map_right,
                map_bottom,
                maxy,
                maxx,
                status_spot=status_spot,
                status_perp=status_perp,
            )
            stdscr.refresh()
            return

        is_new_update = id(state) != self._last_state_id
        if is_new_update:
            self._last_y = state.y
            self._last_state_id = id(state)

        self._draw_labels(
            stdscr,
            map_top,
            map_left,
            map_right,
            map_bottom,
            maxy,
            maxx,
            status_spot=status_spot,
            status_perp=status_perp,
        )

        self._draw_persistence_line(stdscr, state, map_top, map_left, map_right, maxx, maxy)

        base_x, base_y = _norm_to_grid(state.x, state.y, self._config.width, self._config.height)
        base_x += map_left
        base_y += map_top

        dot_x, dot_y = _apply_lean_offset(base_x, base_y, state.lean)
        self._draw_halo(stdscr, dot_x, dot_y, state.halo_bin, map_left, map_top, maxx, maxy)
        self._draw_dot(stdscr, dot_x, dot_y, state.size_bin, maxx, maxy)

        self._draw_status_bar(
            stdscr,
            spot_stats=spot_stats,
            perp_stats=perp_stats,
            metrics=metrics,
            state=state,
            symbol=symbol,
            map_left=map_left,
            map_right=map_right,
            map_bottom=map_bottom,
            maxy=maxy,
            maxx=maxx,
        )

        stdscr.refresh()

    def _draw_header(
        self,
        stdscr: CursesWindow,
        header: str,
        maxx: int,
        *,
        row: int = 0,
    ) -> None:
        if row >= stdscr.getmaxyx()[0]:
            return
        stdscr.addstr(row, 0, header[: maxx - 1])

    def _draw_status_bar(
        self,
        stdscr: CursesWindow,
        *,
        spot_stats: AdapterStats | None,
        perp_stats: AdapterStats | None,
        metrics: LiveMetricsSnapshot | None,
        state: StateSnapshot | None,
        symbol: str,
        map_left: int,
        map_right: int,
        map_bottom: int,
        maxy: int,
        maxx: int,
    ) -> None:
        lines = _status_lines(
            metrics,
            state,
            symbol,
            perp_stats,
            spot_stats,
        )
        top = map_bottom + 4
        if not lines:
            return
        inner_width = min(max(len(line) for line in lines), maxx - 2)
        if inner_width <= 0:
            return
        box_height = len(lines) + 2
        bottom = top + box_height - 1
        if bottom >= maxy:
            return
        x0 = min(max(0, map_left), maxx - (inner_width + 2))
        if x0 < 0:
            x0 = 0

        stdscr.addstr(top, x0, "┌" + "─" * inner_width + "┐")
        for idx, line in enumerate(lines, start=1):
            stdscr.addstr(top + idx, x0, "│")
            _addstr_limited(
                stdscr, top + idx, x0 + 1, line, x0 + 1 + inner_width
            )
            stdscr.addstr(top + idx, x0 + inner_width + 1, "│")
        stdscr.addstr(bottom, x0, "└" + "─" * inner_width + "┘")

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
        *,
        status_spot: AdapterStatus | None,
        status_perp: AdapterStatus | None,
    ) -> None:
        top = "ACCEPTING"
        bot = "REJECTING"
        left = "PERP"
        right = "SPOT"

        spot_attr = 0
        perp_attr = 0
        if curses.has_colors():
            self._ensure_colors()
            if status_spot is not None:
                spot_attr = curses.color_pair(_status_color(status_spot))
            if status_perp is not None:
                perp_attr = curses.color_pair(_status_color(status_perp))

        tx = map_left + max(0, (self._config.width - len(top)) // 2)
        if map_top - 1 >= 1 and tx + len(top) < maxx:
            stdscr.addstr(map_top - 1, tx, top)

        bx = map_left + max(0, (self._config.width - len(bot)) // 2)
        if map_bottom + 1 < maxy and bx + len(bot) < maxx:
            stdscr.addstr(map_bottom + 1, bx, bot)

        if map_left - len(left) - 1 >= 0 and map_top + self._config.height // 2 < maxy:
            if perp_attr:
                stdscr.addstr(
                    map_top + self._config.height // 2,
                    map_left - len(left) - 1,
                    left,
                    perp_attr,
                )
            else:
                stdscr.addstr(
                    map_top + self._config.height // 2, map_left - len(left) - 1, left
                )

        if map_right + 2 + len(right) < maxx and map_top + self._config.height // 2 < maxy:
            if spot_attr:
                stdscr.addstr(
                    map_top + self._config.height // 2, map_right + 2, right, spot_attr
                )
            else:
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

    def _draw_persistence_line(
        self,
        stdscr: CursesWindow,
        state: StateSnapshot,
        map_top: int,
        map_left: int,
        map_right: int,
        maxx: int,
        maxy: int,
    ) -> None:
        if not state.persist_enabled:
            return
        _, grid_y = _norm_to_grid(0.0, state.persist_raw, self._config.width, self._config.height)
        y = map_top + grid_y
        center_y = map_top + self._config.height // 2
        if y == center_y or not _in_bounds(map_left, y, maxx, maxy):
            return
        for x in range(map_left, map_right + 1):
            if not _in_bounds(x, y, maxx, maxy):
                continue
            if (x - map_left) % 2 == 1:
                stdscr.addstr(y, x, "·")

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


def _feeds_text(stats: AdapterStats | None) -> str:
    if stats is None:
        return "0/0"
    return f"{stats.active_pairs}/{stats.total_pairs}"


def _reconnect_text(stats: AdapterStats | None) -> str:
    if stats is None:
        return "0"
    return str(stats.reconnect_count)


def _tbt_text(stats: AdapterStats | None) -> str:
    if stats is None or stats.tbt_ms is None:
        return "n/a"
    if stats.tbt_ms < 1000:
        return f"{stats.tbt_ms:.0f}ms"
    return f"{stats.tbt_ms / 1000:.1f}s"


def _status_lines(
    metrics: LiveMetricsSnapshot | None,
    state: StateSnapshot | None,
    symbol: str,
    perp_stats: AdapterStats | None,
    spot_stats: AdapterStats | None,
) -> list[str]:
    majors = {"BTC", "ETH", "SOL"}
    series_target = "<1/m" if symbol.upper() in majors else "<3/m"
    col_width = 48

    if metrics is None:
        p95 = p99 = flip_raw = flip_y = deadband = disp_ratio = air_pocket = None
        persist = None
        switch_rate = None
    else:
        p95 = metrics.y_raw_p95
        p99 = metrics.y_raw_p99
        flip_raw = metrics.flip_rate_y_raw
        flip_y = metrics.flip_rate_y
        deadband = metrics.deadband_active_rate
        disp_ratio = metrics.disp_ratio
        persist = metrics.e_dir_persistence
        switch_rate = metrics.price_series_switch_rate
        air_pocket = metrics.air_pocket_active_rate

    if state is None:
        y_raw_now = None
        y_smoothed = None
        y_gated = None
        persist_now = None
        persist_slope = None
        gate_now = None
    else:
        y_raw_now = state.y_raw
        y_smoothed = state.y
        y_gated = state.y_gated
        persist_now = state.persist_raw
        persist_slope = state.persist_slope
        gate_now = state.gate

    line_metrics_1 = (
        "p95|Y_raw| "
        f"{_fmt_float(p95, 2)} [0.6-0.8]  "
        "p99|Y_raw| "
        f"{_fmt_float(p99, 2)} [<0.9]  "
        "Flip Y_raw "
        f"{_fmt_rate(flip_raw)} [3-8]  "
        "Y "
        f"{_fmt_rate(flip_y)} [1-4]  "
        "Deadband "
        f"{_fmt_float(deadband, 2)} [0.25-0.55]"
    )
    y_line_indent = 0
    line_metrics_y = (
        " " * y_line_indent
        + "Y_raw "
        + _fmt_float(y_raw_now, 2)
        + "  Y_g "
        + _fmt_float(y_gated, 2)
        + "  Y_s "
        + _fmt_float(y_smoothed, 2)
        + "  S "
        + _fmt_float(persist_now, 2)
        + "  dS/s "
        + _fmt_float(persist_slope, 3)
        + "  Gate "
        + _fmt_float(gate_now, 2)
    )
    line_metrics_disp = (
        "|disp|/scale "
        f"{_fmt_float(disp_ratio, 2)} [0.8-2.0]  "
        "E_dir persist "
        f"{_fmt_int(persist)} [3-10]  "
        "Series switch "
        f"{_fmt_rate(switch_rate)} [{series_target}]  "
        "Air pocket "
        f"{_fmt_float(air_pocket, 2)} [<0.2]"
    )
    feeds_left = (
        "Feeds "
        f"{_feeds_text(perp_stats)}  "
        "TBT "
        f"{_tbt_text(perp_stats)}  "
        "Reconn "
        f"{_reconnect_text(perp_stats)}"
    )
    feeds_right = (
        "Feeds "
        f"{_feeds_text(spot_stats)}  "
        "TBT "
        f"{_tbt_text(spot_stats)}  "
        "Reconn "
        f"{_reconnect_text(spot_stats)}"
    )
    line_feeds = f"{feeds_left}".ljust(col_width) + feeds_right
    return [line_feeds, line_metrics_1, line_metrics_y, line_metrics_disp]


def _fmt_float(value: float | None, digits: int) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}/m"


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _addstr_limited(
    stdscr: CursesWindow, y: int, x: int, text: str, maxx: int, attr: int = 0
) -> int:
    if x >= maxx - 1:
        return x
    available = maxx - x - 1
    if available <= 0:
        return x
    chunk = text[:available]
    if attr:
        stdscr.addstr(y, x, chunk, attr)
    else:
        stdscr.addstr(y, x, chunk)
    return x + len(chunk)


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
