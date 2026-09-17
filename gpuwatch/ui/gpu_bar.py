"""
Rich bar rendering for GPU utilization and memory.

nvtop-inspired meters + short utilization history plots.
Yellow fill, blank unused, wrapped in [].
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.style import Style
from rich.table import Table
from rich.text import Text

from ..models import GPUInfo

# 8-level vertical blocks for sparklines / mini plots
_LEVELS = " ▁▂▃▄▅▆▇█"


def _bar_style(percent: float) -> Style:
    if percent < 50:
        return Style(color="green")
    elif percent < 80:
        return Style(color="yellow")
    else:
        return Style(color="red")


def _fill_bar(percent: float, width: int) -> Text:
    pct = max(0.0, min(float(percent), 100.0))
    filled = int(round(pct / 100.0 * width))
    filled = min(filled, width)
    out = Text()
    if filled:
        out.append("█" * filled, style="yellow")
    if width - filled:
        out.append(" " * (width - filled))
    return out


def bracket_bar(percent: float, width: int = 20) -> Text:
    out = Text("[", style="bright_black")
    out += _fill_bar(percent, width)
    out.append("]", style="bright_black")
    return out


def utilization_bar(percent: int, width: int = 10) -> Text:
    pct = max(0, min(int(percent), 100))
    out = bracket_bar(pct, width=width)
    out.append(f" {pct:3d}%", style="yellow")
    return out


def _format_mem(mb: int) -> str:
    if mb >= 20480:
        return f"{mb / 1024:.2f}GiB"
    else:
        return f"{mb}MiB"


def memory_bar(used_mb: int, total_mb: int, width: int = 16) -> Text:
    if total_mb <= 0:
        return Text("[N/A]", style="bright_black")
    pct = (used_mb / total_mb) * 100.0
    out = bracket_bar(pct, width=width)
    out.append(
        f" {_format_mem(used_mb)} / {_format_mem(total_mb)}",
        style="yellow",
    )
    return out


def temp_str(celsius: int) -> Text:
    if celsius <= 0:
        return Text("N/A", style=Style(color="bright_black"))
    if celsius < 50:
        style = Style(color="blue")
    elif celsius < 70:
        style = Style(color="green")
    elif celsius < 85:
        style = Style(color="yellow")
    else:
        style = Style(color="red")
    return Text(f"{celsius}°C", style=style)


def power_str(watts: float, limit_watts: float) -> Text:
    if limit_watts <= 0:
        return Text(f"{watts:.0f}W", style=Style(color="bright_black"))
    ratio = (watts / limit_watts) * 100
    if ratio < 50:
        style = Style(color="green")
    elif ratio < 80:
        style = Style(color="yellow")
    else:
        style = Style(color="red")
    return Text(f"{watts:.0f}W/{limit_watts:.0f}W", style=style)


def _resample(values: Sequence[float], width: int) -> list[float]:
    if width <= 0:
        return []
    if not values:
        return [0.0] * width
    if len(values) == 1:
        return [float(values[0])] * width
    out: list[float] = []
    last = len(values) - 1
    for i in range(width):
        pos = i * last / (width - 1)
        lo = int(pos)
        hi = min(lo + 1, last)
        frac = pos - lo
        out.append(values[lo] * (1 - frac) + values[hi] * frac)
    return out




def _pad_history(values: Sequence[float], n: int) -> list[float | None]:
    """Right-align real samples. Missing left slots are None (not drawn as 0%)."""
    vals = [max(0.0, min(100.0, float(v))) for v in values]
    if n <= 0:
        return []
    if len(vals) >= n:
        return vals[-n:]  # type: ignore[return-value]
    return [None] * (n - len(vals)) + vals  # type: ignore[return-value]


# Braille: 2x4 dots per cell. Bit order:
#  (0,0)=1 (1,0)=8
#  (0,1)=2 (1,1)=16
#  (0,2)=4 (1,2)=32
#  (0,3)=64 (1,3)=128
_BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def _braille_set(grid: list[list[int]], x_dot: int, y_dot: int, cols_dots: int, rows_dots: int) -> None:
    if not (0 <= x_dot < cols_dots and 0 <= y_dot < rows_dots):
        return
    cx, cy = x_dot // 2, y_dot // 4
    bx, by = x_dot % 2, y_dot % 4
    if 0 <= cy < len(grid) and 0 <= cx < len(grid[0]):
        grid[cy][cx] |= _BRAILLE_BITS[by][bx]


def _draw_braille_polyline(
    grid: list[list[int]],
    points: Sequence[tuple[int, int | None]],
    cols_dots: int,
    rows_dots: int,
) -> None:
    """Draw polyline through (x_dot, y_dot) samples; None y skips (gap)."""
    prev: tuple[int, int] | None = None
    for x, y in points:
        if y is None:
            prev = None
            continue
        if prev is None:
            _braille_set(grid, x, y, cols_dots, rows_dots)
            prev = (x, y)
            continue
        x0, y0 = prev
        x1, y1 = x, y
        dx = x1 - x0
        dy = y1 - y0
        steps = max(abs(dx), abs(dy), 1)
        for s in range(steps + 1):
            xi = int(round(x0 + dx * s / steps))
            yi = int(round(y0 + dy * s / steps))
            _braille_set(grid, xi, yi, cols_dots, rows_dots)
        prev = (x1, y1)


def nvtop_line_chart(
    util_history: Sequence[float],
    mem_history: Sequence[float],
    *,
    width: int = 72,
    height: int = 12,
) -> Table:
    """nvtop-like dual series chart with braille polylines.

    Stair-step ACS corners look like a big rectangle on sudden 0→100 jumps.
    Braille gives thin diagonal lines instead of a vertical wall + zero baseline box.
    Left side is blank until enough real samples exist (no fake 0% padding).
    Y: 100 at top, 0 at bottom.
    """
    rows = max(height, 4)
    cols = max(width, 16)
    # Braille: each char = 2x4 dots
    rows_dots = rows * 4
    cols_dots = cols * 2

    util = _pad_history(util_history, cols)
    mem = _pad_history(mem_history, cols)
    n_real = sum(1 for v in util if v is not None)

    def to_y_dot(v: float) -> int:
        # 100% → y_dot 0 (top), 0% → rows_dots-1 (bottom)
        return int(round((100.0 - v) / 100.0 * (rows_dots - 1)))

    util_grid = [[0 for _ in range(cols)] for _ in range(rows)]
    mem_grid = [[0 for _ in range(cols)] for _ in range(rows)]

    util_pts: list[tuple[int, int | None]] = []
    mem_pts: list[tuple[int, int | None]] = []
    for i in range(cols):
        # center of braille cell column pair
        x_dot = i * 2 + 1
        u, m = util[i], mem[i]
        util_pts.append((x_dot, to_y_dot(u) if u is not None else None))
        mem_pts.append((x_dot, to_y_dot(m) if m is not None else None))

    _draw_braille_polyline(util_grid, util_pts, cols_dots, rows_dots)
    _draw_braille_polyline(mem_grid, mem_pts, cols_dots, rows_dots)

    line_colors = ("cyan", "dark_orange")
    legends = ("GPU0 %", "GPU0 mem%")

    box = Table(show_header=False, expand=True, box=None, padding=0)
    box.add_column("y", width=4, justify="right", no_wrap=True)
    box.add_column("plot", justify="left", no_wrap=True)

    # Tick labels at 100/75/50/25/0
    tick_at: dict[int, int] = {}
    for tick in (100, 75, 50, 25, 0):
        tr = int(round((100.0 - tick) / 100.0 * (rows - 1)))
        tick_at.setdefault(tr, tick)

    for r in range(rows):
        y_txt = Text(f"{tick_at[r]:3d}" if r in tick_at else "   ", style="bright_black")
        line = Text()
        line.append("│", style="bright_black")
        for c in range(cols):
            ub, mb = util_grid[r][c], mem_grid[r][c]
            if ub and mb:
                # Prefer util color when both occupy the cell; still show combined dots
                ch = chr(0x2800 + (ub | mb))
                line.append(ch, style=line_colors[0])
            elif ub:
                line.append(chr(0x2800 + ub), style=line_colors[0])
            elif mb:
                line.append(chr(0x2800 + mb), style=line_colors[1])
            else:
                line.append(" ", style="bright_black")
        line.append("│", style="bright_black")
        if r < 2:
            line.append(" ", style="bright_black")
            line.append(legends[r], style=line_colors[r])
        box.add_row(y_txt, line)

    axis = Text("└" + "─" * cols + "┘", style="bright_black")
    span = max(n_real, 1)
    left, mid, right = f"-{span}", f"-{span // 2}", "-0"
    tline = Text("    ", style="bright_black")
    pad_mid = max(cols // 2 - len(left) - len(mid) // 2, 1)
    pad_right = max(cols - len(left) - pad_mid - len(mid) - len(right), 1)
    tline.append(left + " " * pad_mid + mid + " " * pad_right + right, style="bright_black")
    box.add_row(Text("   ", style="bright_black"), axis)
    box.add_row(Text("   ", style="bright_black"), tline)
    return box


def nvtop_gpu_block(
    gpu: GPUInfo,
    *,
    compact: bool = False,
    util_history: Sequence[float] | None = None,
    mem_history: Sequence[float] | None = None,
    plot_width: int = 72,
) -> Table:
    """Render one GPU in an nvtop-like block with a dual-line history chart."""
    box = Table(show_header=False, expand=True, box=None, padding=0)
    box.add_column("body", justify="left")

    head = Text()
    head.append(f"Device {gpu.index} ", style="bold cyan")
    head.append("[", style="bright_black")
    head.append(gpu.name, style="white")
    head.append("]", style="bright_black")
    box.add_row(head)

    if compact:
        line = Text()
        line.append("  GPU ", style="bright_black")
        line += utilization_bar(gpu.utilization_gpu, width=12)
        line.append("  MEM ", style="bright_black")
        line += memory_bar(gpu.memory_used_mb, gpu.memory_total_mb, width=12)
        line.append("  ", style="bright_black")
        line += temp_str(gpu.temperature_c)
        line.append("  ", style="bright_black")
        line += power_str(gpu.power_watts, gpu.power_limit_watts)
        box.add_row(line)
        util_h = list(util_history or [])
        mem_h = list(mem_history or [])
        if util_h or mem_h:
            box.add_row(nvtop_line_chart(util_h, mem_h, width=min(plot_width, 56), height=8))
        return box

    gpu_line = Text()
    gpu_line.append("  GPU ", style="bright_black")
    gpu_line += utilization_bar(gpu.utilization_gpu, width=24)
    box.add_row(gpu_line)

    mem_line = Text()
    mem_line.append("  MEM ", style="bright_black")
    mem_line += memory_bar(gpu.memory_used_mb, gpu.memory_total_mb, width=24)
    box.add_row(mem_line)

    meta = Text()
    meta.append("  TEMP ", style="bright_black")
    meta += temp_str(gpu.temperature_c)
    meta.append("   PWR ", style="bright_black")
    meta += power_str(gpu.power_watts, gpu.power_limit_watts)
    mem_pct = gpu.memory_percent
    meta.append("   MEM-UTIL ", style="bright_black")
    meta.append(f"{mem_pct:.0f}%", style="dark_orange")
    box.add_row(meta)

    util_h = list(util_history or [])
    mem_h = list(mem_history or [])
    if util_h or mem_h:
        box.add_row(Text(""))
        box.add_row(nvtop_line_chart(util_h, mem_h, width=max(plot_width, 72), height=12))
    return box
