from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Any, TypeAlias

from flow_lens.adapters.base import AdapterStats, AdapterStatus
from flow_lens.engine.state_engine import StateSnapshot
from flow_lens.tui.metrics import LiveMetricsSnapshot

CursesWindow: TypeAlias = Any
@dataclass(frozen=True)
class RendererConfig:
    # Lens box dimensions in terminal cells (odd numbers center axes cleanly).
    min_width: int = 41
    min_height: int = 17
    max_width: int = 81
    max_height: int = 33

    # Dot size bins (small, medium, large) as Braille-pixel radii.
    dot_radii: tuple[int, int, int] = (1, 2, 4)

    # Halo size bins (none, medium, large) as Braille-pixel radii.
    halo_radii: tuple[int, int, int] = (0, 6, 9)

    # Circular frame controls.
    frame_enabled: bool = True
    frame_inset_px: int = 1
    frame_band_inner: float = 0.995
    frame_band_outer: float = 1.005
    axis_flash_duration_s: float = 0.25
    axis_flash_cooldown_s: float = 0.75


class Renderer:
    def __init__(self, config: RendererConfig = RendererConfig()) -> None:
        self._config = config
        self._colors_ready = False
        self._last_y: float | None = None
        self._last_state_id: int | None = None
        self._axis_flash_sign: int = 0
        self._axis_flash_target: int = 0
        self._axis_flash_until: float = 0.0
        self._axis_flash_cooldown_until: float = 0.0

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
        if maxx <= 1 or maxy <= 1:
            return
        if curses.has_colors():
            self._ensure_colors()

        header = symbol if not search_mode else f"{symbol}   /{search_buffer}"
        self._draw_header(stdscr, header, maxx, row=0)

        map_top = 2
        map_width, map_height = _dynamic_map_size(
            maxx,
            maxy,
            min_width=self._config.min_width,
            min_height=self._config.min_height,
            max_width=self._config.max_width,
            max_height=self._config.max_height,
        )
        map_left = max(0, (maxx - map_width) // 2)
        if map_top + map_height + 1 >= maxy:
            map_left = 0

        map_right = map_left + map_width - 1
        map_bottom = map_top + map_height - 1

        self._draw_axes_overlay(stdscr, map_top, map_left, map_width, map_height)
        self._draw_lens(
            stdscr,
            state=state,
            map_top=map_top,
            map_left=map_left,
            map_width=map_width,
            map_height=map_height,
            maxx=maxx,
            maxy=maxy,
        )
        # Re-apply solid axes so persistence/halo never erase axis continuity.
        self._draw_axes_overlay(stdscr, map_top, map_left, map_width, map_height)
        if state is not None:
            # Dot is re-drawn last so it remains visible even when on the axes.
            self._draw_dot_overlay(
                stdscr,
                state,
                map_top,
                map_left,
                map_width,
                map_height,
                maxx,
                maxy,
            )

        if state is None:
            self._last_y = None
            self._last_state_id = None
            self._axis_flash_sign = 0
            self._axis_flash_target = 0
            self._axis_flash_until = 0.0
            self._axis_flash_cooldown_until = 0.0
            self._draw_labels(
                stdscr,
                map_top,
                map_left,
                map_right,
                map_bottom,
                map_width,
                map_height,
                maxy,
                maxx,
                status_spot=status_spot,
                status_perp=status_perp,
                axis_flash_sign=0,
            )
            stdscr.refresh()
            return

        is_new_update = id(state) != self._last_state_id
        if is_new_update:
            prev_y = self._last_y
            y_shift_sign = 0
            if prev_y is not None:
                y_shift_sign = _sign_value(state.y - prev_y)
            self._last_state_id = id(state)
            self._maybe_trigger_axis_flash(
                _bull_bear_sign(
                    state.e_dir,
                    state.total_effort,
                    state.persist_neutral_dir_abs_flash,
                ),
                y_shift_sign,
            )
            self._last_y = state.y

        self._draw_labels(
            stdscr,
            map_top,
            map_left,
            map_right,
            map_bottom,
            map_width,
            map_height,
            maxy,
            maxx,
            status_spot=status_spot,
            status_perp=status_perp,
            axis_flash_sign=self._axis_flash_sign if self._axis_flash_active() else 0,
        )

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
        _safe_addstr(stdscr, row, 0, header[: maxx - 1])

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

        _safe_addstr(stdscr, top, x0, "┌" + "─" * inner_width + "┐")
        for idx, line in enumerate(lines, start=1):
            _safe_addstr(stdscr, top + idx, x0, "│")
            _addstr_limited(
                stdscr, top + idx, x0 + 1, line, x0 + 1 + inner_width
            )
            _safe_addstr(stdscr, top + idx, x0 + inner_width + 1, "│")
        _safe_addstr(stdscr, bottom, x0, "└" + "─" * inner_width + "┘")

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
        map_width: int,
        map_height: int,
        maxy: int,
        maxx: int,
        *,
        status_spot: AdapterStatus | None,
        status_perp: AdapterStatus | None,
        axis_flash_sign: int,
    ) -> None:
        top = "ACCEPTING"
        bot = "REJECTING"
        left = "PERP"
        right = "SPOT"

        spot_attr = 0
        perp_attr = 0
        top_attr = 0
        bot_attr = 0
        if curses.has_colors():
            self._ensure_colors()
            if status_spot is not None:
                spot_attr = curses.color_pair(_status_color(status_spot))
            if status_perp is not None:
                perp_attr = curses.color_pair(_status_color(status_perp))
            flash_attr = 0
            if axis_flash_sign > 0:
                flash_attr = curses.color_pair(1)
            elif axis_flash_sign < 0:
                flash_attr = curses.color_pair(3)
            if self._axis_flash_target > 0:
                top_attr = flash_attr
            elif self._axis_flash_target < 0:
                bot_attr = flash_attr

        tx = map_left + max(0, (map_width - len(top)) // 2)
        if map_top - 1 >= 1 and tx + len(top) < maxx:
            _safe_addstr(stdscr, map_top - 1, tx, top, attr=top_attr)

        bx = map_left + max(0, (map_width - len(bot)) // 2)
        if map_bottom + 1 < maxy and bx + len(bot) < maxx:
            _safe_addstr(stdscr, map_bottom + 1, bx, bot, attr=bot_attr)

        if map_left - len(left) - 1 >= 0 and map_top + map_height // 2 < maxy:
            if perp_attr:
                _safe_addstr(
                    stdscr,
                    map_top + map_height // 2,
                    map_left - len(left) - 1,
                    left,
                    attr=perp_attr,
                )
            else:
                _safe_addstr(stdscr, map_top + map_height // 2, map_left - len(left) - 1, left)

        if map_right + 2 + len(right) < maxx and map_top + map_height // 2 < maxy:
            if spot_attr:
                _safe_addstr(
                    stdscr,
                    map_top + map_height // 2,
                    map_right + 2,
                    right,
                    attr=spot_attr,
                )
            else:
                _safe_addstr(stdscr, map_top + map_height // 2, map_right + 2, right)

    def _maybe_trigger_axis_flash(self, disp_sign: int, y_shift_sign: int) -> None:
        if disp_sign == 0 or y_shift_sign == 0:
            return
        now = time.monotonic()
        if (
            now < self._axis_flash_cooldown_until
            and disp_sign == self._axis_flash_sign
            and y_shift_sign == self._axis_flash_target
        ):
            return
        self._axis_flash_sign = disp_sign
        self._axis_flash_target = y_shift_sign
        self._axis_flash_until = now + max(0.0, self._config.axis_flash_duration_s)
        self._axis_flash_cooldown_until = now + max(0.0, self._config.axis_flash_cooldown_s)

    def _axis_flash_active(self) -> bool:
        if self._axis_flash_sign == 0:
            return False
        if self._axis_flash_target == 0:
            return False
        return time.monotonic() <= self._axis_flash_until

    def _draw_lens(
        self,
        stdscr: CursesWindow,
        *,
        state: StateSnapshot | None,
        map_top: int,
        map_left: int,
        map_width: int,
        map_height: int,
        maxx: int,
        maxy: int,
    ) -> None:
        canvas = _BrailleCanvas(map_width, map_height)
        persist_canvas: _BrailleCanvas | None = None
        persist_attr = 0
        center_x = canvas.width_px // 2
        center_y = canvas.height_px // 2
        if self._config.frame_enabled:
            _draw_ellipse_ring(
                canvas,
                center_x=center_x,
                center_y=center_y,
                radius_x=max(1, center_x - self._config.frame_inset_px),
                radius_y=max(1, center_y - self._config.frame_inset_px),
                band_inner=self._config.frame_band_inner,
                band_outer=self._config.frame_band_outer,
            )

        if state is not None:
            if state.persist_enabled:
                _, persist_y = _norm_to_braille(0.0, state.persist_raw, map_width, map_height)
                if persist_y != center_y:
                    # Preserve the solid vertical axis cell so line and axis remain distinct.
                    persist_canvas = _BrailleCanvas(map_width, map_height)
                    persist_canvas.draw_hline(
                        persist_y,
                        step=2,
                        skip_x_min=center_x - 1,
                        skip_x_max=center_x,
                    )
                    persist_attr = _persist_provenance_attr(
                        state.persist_dir_raw,
                        state.persist_neutral_dir_abs_persist,
                    )

            dot_cell_x, dot_cell_y = _norm_to_grid(state.x, state.y, map_width, map_height)
            dot_cell_x, dot_cell_y = _apply_lean_offset(dot_cell_x, dot_cell_y, state.lean)
            dot_x = _clamp(dot_cell_x * 2 + 1, 0, canvas.width_px - 1)
            dot_y = _clamp(dot_cell_y * 4 + 1, 0, canvas.height_px - 1)
            _draw_halo(canvas, dot_x, dot_y, state.halo_bin, self._config.halo_radii)
            _draw_dot(canvas, dot_x, dot_y, state.size_bin, self._config.dot_radii)

        canvas.blit(stdscr, map_top, map_left, maxx, maxy)
        if persist_canvas is not None:
            persist_canvas.blit(stdscr, map_top, map_left, maxx, maxy, attr=persist_attr)

    def _draw_axes_overlay(
        self,
        stdscr: CursesWindow,
        map_top: int,
        map_left: int,
        map_width: int,
        map_height: int,
    ) -> None:
        center_x = map_left + map_width // 2
        center_y = map_top + map_height // 2

        for x in range(map_left, map_left + map_width):
            ch = "┼" if x == center_x else "─"
            _safe_addstr(stdscr, center_y, x, ch)
        for y in range(map_top, map_top + map_height):
            ch = "┼" if y == center_y else "│"
            _safe_addstr(stdscr, y, center_x, ch)

    def _draw_dot_overlay(
        self,
        stdscr: CursesWindow,
        state: StateSnapshot,
        map_top: int,
        map_left: int,
        map_width: int,
        map_height: int,
        maxx: int,
        maxy: int,
    ) -> None:
        canvas = _BrailleCanvas(map_width, map_height)
        dot_cell_x, dot_cell_y = _norm_to_grid(state.x, state.y, map_width, map_height)
        dot_cell_x, dot_cell_y = _apply_lean_offset(dot_cell_x, dot_cell_y, state.lean)
        dot_x = _clamp(dot_cell_x * 2 + 1, 0, canvas.width_px - 1)
        dot_y = _clamp(dot_cell_y * 4 + 1, 0, canvas.height_px - 1)
        _draw_dot(canvas, dot_x, dot_y, state.size_bin, self._config.dot_radii)
        canvas.blit(stdscr, map_top, map_left, maxx, maxy)


def _dynamic_map_size(
    maxx: int,
    maxy: int,
    *,
    min_width: int,
    min_height: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    # Keep side labels and a margin visible.
    available_width = maxx - 12
    # Keep vertical room for labels and the status box below the lens.
    available_height = maxy - 11
    width = _choose_odd_size(min_width, min(max_width, available_width), floor=25)
    height = _choose_odd_size(min_height, min(max_height, available_height), floor=15)
    return width, height


def _choose_odd_size(minimum: int, available: int, *, floor: int) -> int:
    del floor
    if available <= 0:
        return 1
    max_odd = available if available % 2 == 1 else available - 1
    if max_odd <= 0:
        return 1
    if max_odd < minimum:
        return max_odd
    return max(minimum, max_odd)


def _norm_to_grid(xn: float, yn: float, width: int, height: int) -> tuple[int, int]:
    cx = width // 2
    cy = height // 2
    gx = int(round(cx + xn * (width // 2 - 1)))
    gy = int(round(cy - yn * (height // 2 - 1)))
    return _clamp(gx, 0, width - 1), _clamp(gy, 0, height - 1)


def _norm_to_braille(xn: float, yn: float, width: int, height: int) -> tuple[int, int]:
    width_px = width * 2
    height_px = height * 4
    x = int(round(((xn + 1.0) / 2.0) * (width_px - 1)))
    y = int(round(((1.0 - yn) / 2.0) * (height_px - 1)))
    return _clamp(x, 0, width_px - 1), _clamp(y, 0, height_px - 1)


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


def _sign_value(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _bull_bear_sign(e_dir: float, total_effort: float, deadband: float) -> int:
    if total_effort <= 0:
        return 0
    share = e_dir / total_effort
    threshold = max(deadband, 0.0)
    if share > threshold:
        return 1
    if share < -threshold:
        return -1
    return 0


def _persist_provenance_attr(s_dir: float, deadband: float) -> int:
    if not curses.has_colors():
        return 0
    sign = _sign_with_deadband(s_dir, deadband)
    if sign > 0:
        return curses.color_pair(1)
    if sign < 0:
        return curses.color_pair(3)
    return 0


def _sign_with_deadband(value: float, deadband: float) -> int:
    threshold = max(deadband, 0.0)
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


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
        _safe_addstr(stdscr, y, x, chunk, attr=attr)
    else:
        _safe_addstr(stdscr, y, x, chunk)
    return x + len(chunk)


class _BrailleCanvas:
    _LEFT_BITS = (0x01, 0x02, 0x04, 0x40)
    _RIGHT_BITS = (0x08, 0x10, 0x20, 0x80)

    def __init__(self, width_cells: int, height_cells: int) -> None:
        self.width_cells = width_cells
        self.height_cells = height_cells
        self.width_px = width_cells * 2
        self.height_px = height_cells * 4
        self._cells: list[list[int]] = [
            [0 for _ in range(width_cells)] for _ in range(height_cells)
        ]

    def set_px(self, x: int, y: int) -> None:
        if x < 0 or x >= self.width_px or y < 0 or y >= self.height_px:
            return
        cell_x = x // 2
        cell_y = y // 4
        sub_x = x % 2
        sub_y = y % 4
        bit = self._LEFT_BITS[sub_y] if sub_x == 0 else self._RIGHT_BITS[sub_y]
        self._cells[cell_y][cell_x] |= bit

    def draw_hline(
        self,
        y: int,
        *,
        step: int = 1,
        skip_x_min: int | None = None,
        skip_x_max: int | None = None,
    ) -> None:
        if y < 0 or y >= self.height_px:
            return
        for x in range(0, self.width_px, max(1, step)):
            if (
                skip_x_min is not None
                and skip_x_max is not None
                and skip_x_min <= x <= skip_x_max
            ):
                continue
            self.set_px(x, y)

    def draw_vline(self, x: int, *, step: int = 1) -> None:
        if x < 0 or x >= self.width_px:
            return
        for y in range(0, self.height_px, max(1, step)):
            self.set_px(x, y)

    def blit(
        self,
        stdscr: CursesWindow,
        top: int,
        left: int,
        maxx: int,
        maxy: int,
        *,
        attr: int = 0,
    ) -> None:
        for row, masks in enumerate(self._cells):
            y = top + row
            if y < 0 or y >= maxy:
                continue
            for col, mask in enumerate(masks):
                if mask == 0:
                    continue
                x = left + col
                if x < 0 or x >= maxx - 1:
                    continue
                _safe_addstr(stdscr, y, x, chr(0x2800 + mask), attr=attr)


def _draw_dot(
    canvas: _BrailleCanvas,
    center_x: int,
    center_y: int,
    size_bin: int,
    dot_radii: tuple[int, int, int],
) -> None:
    radius = dot_radii[_clamp(size_bin, 0, 2)]
    _draw_disk(canvas, center_x, center_y, radius)


def _draw_halo(
    canvas: _BrailleCanvas,
    center_x: int,
    center_y: int,
    halo_bin: int,
    halo_radii: tuple[int, int, int],
) -> None:
    if halo_bin <= 0:
        return
    radius = halo_radii[_clamp(halo_bin, 0, 2)]
    _draw_ring(canvas, center_x, center_y, radius)


def _draw_disk(canvas: _BrailleCanvas, center_x: int, center_y: int, radius: int) -> None:
    if radius <= 0:
        canvas.set_px(center_x, center_y)
        return
    r2 = radius * radius
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= r2:
                canvas.set_px(center_x + dx, center_y + dy)


def _draw_ring(canvas: _BrailleCanvas, center_x: int, center_y: int, radius: int) -> None:
    if radius <= 0:
        return
    inner = (radius - 0.75) * (radius - 0.75)
    outer = (radius + 0.75) * (radius + 0.75)
    span = radius + 1
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            dist2 = float(dx * dx + dy * dy)
            if inner <= dist2 <= outer:
                canvas.set_px(center_x + dx, center_y + dy)


def _draw_ellipse_ring(
    canvas: _BrailleCanvas,
    *,
    center_x: int,
    center_y: int,
    radius_x: int,
    radius_y: int,
    band_inner: float,
    band_outer: float,
) -> None:
    if radius_x <= 0 or radius_y <= 0:
        return
    inner = band_inner
    outer = band_outer
    for dy in range(-radius_y - 1, radius_y + 2):
        for dx in range(-radius_x - 1, radius_x + 2):
            nx = dx / radius_x
            ny = dy / radius_y
            dist = nx * nx + ny * ny
            if inner <= dist <= outer:
                canvas.set_px(center_x + dx, center_y + dy)


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


def _safe_addstr(
    stdscr: CursesWindow,
    y: int,
    x: int,
    text: str,
    *,
    attr: int = 0,
) -> None:
    try:
        if attr:
            stdscr.addstr(y, x, text, attr)
        else:
            stdscr.addstr(y, x, text)
    except curses.error:
        pass
