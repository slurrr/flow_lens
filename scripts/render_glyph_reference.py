#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GlyphConfig:
    width_cells: int
    height_cells: int
    dot_radii: tuple[int, int, int]
    halo_radii: tuple[int, int, int]


class BrailleCanvas:
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

    def render(self) -> str:
        lines: list[str] = []
        for row in self._cells:
            line = "".join(chr(0x2800 + mask) if mask else " " for mask in row)
            lines.append(line)
        return "\n".join(lines)


def draw_disk(canvas: BrailleCanvas, center_x: int, center_y: int, radius: int) -> None:
    if radius <= 0:
        canvas.set_px(center_x, center_y)
        return
    r2 = radius * radius
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= r2:
                canvas.set_px(center_x + dx, center_y + dy)


def draw_ring(canvas: BrailleCanvas, center_x: int, center_y: int, radius: int) -> None:
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


def render_combo(config: GlyphConfig, *, size_bin: int, halo_bin: int) -> str:
    canvas = BrailleCanvas(config.width_cells, config.height_cells)
    cx = canvas.width_px // 2
    cy = canvas.height_px // 2
    halo_radius = config.halo_radii[halo_bin]
    dot_radius = config.dot_radii[size_bin]
    draw_ring(canvas, cx, cy, halo_radius)
    draw_disk(canvas, cx, cy, dot_radius)
    return canvas.render()


def build_reference(config: GlyphConfig) -> str:
    parts: list[str] = []

    parts.append("DOT size bins (no halo)")
    for size_bin in range(3):
        parts.append("")
        parts.append(f"size_bin={size_bin}")
        parts.append(render_combo(config, size_bin=size_bin, halo_bin=0))

    parts.append("")
    parts.append("HALO bins with medium dot (size_bin=1)")
    for halo_bin in range(3):
        parts.append("")
        parts.append(f"halo_bin={halo_bin}")
        parts.append(render_combo(config, size_bin=1, halo_bin=halo_bin))

    parts.append("")
    parts.append("FULL matrix (size_bin x halo_bin)")
    for size_bin in range(3):
        for halo_bin in range(3):
            parts.append("")
            parts.append(f"size_bin={size_bin} halo_bin={halo_bin}")
            parts.append(render_combo(config, size_bin=size_bin, halo_bin=halo_bin))

    return "\n".join(parts)


def parse_radii(raw: str, label: str) -> tuple[int, int, int]:
    chunks = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    if len(chunks) != 3:
        raise ValueError(f"{label} must have exactly 3 comma-separated integers.")
    values = (int(chunks[0]), int(chunks[1]), int(chunks[2]))
    if any(value < 0 for value in values):
        raise ValueError(f"{label} values must be >= 0.")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render Flow Lens dot/halo glyph references with current Braille math."
    )
    parser.add_argument(
        "--width-cells",
        type=int,
        default=21,
        help="Canvas width in terminal cells.",
    )
    parser.add_argument(
        "--height-cells",
        type=int,
        default=11,
        help="Canvas height in terminal cells.",
    )
    parser.add_argument(
        "--dot-radii",
        type=str,
        default="1,2,4",
        help="Dot radii for size bins 0,1,2.",
    )
    parser.add_argument(
        "--halo-radii",
        type=str,
        default="0,6,9",
        help="Halo radii for halo bins 0,1,2.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional output file path. If omitted, prints to stdout only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.width_cells < 5 or args.height_cells < 5:
        raise SystemExit("width-cells and height-cells must be >= 5.")

    config = GlyphConfig(
        width_cells=args.width_cells,
        height_cells=args.height_cells,
        dot_radii=parse_radii(args.dot_radii, "dot-radii"),
        halo_radii=parse_radii(args.halo_radii, "halo-radii"),
    )
    report = build_reference(config)
    print(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"\nWrote glyph reference to {args.out}")


if __name__ == "__main__":
    main()
