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







def _right_align_zeros(values: Sequence[float], n: int) -> list[float]:
    """nvtop ring fill: left zeros, newest samples on the right."""
    vals = [max(0.0, min(100.0, float(v))) for v in values]
    if n <= 0:
        return []
    if len(vals) >= n:
        return vals[-n:]
    return [0.0] * (n - len(vals)) + vals


def _data_level(rows: int, data: float, increment: float) -> int:
    """Exact port of nvtop data_level(). ``rows`` is already window_height-1."""
    return int(rows - round(float(data) / increment))


def nvtop_line_chart(
    util_history: Sequence[float],
    mem_history: Sequence[float],
    *,
    width: int = 72,
    height: int = 12,
) -> Table:
    """Exact Python port of Syllo/nvtop src/plot.c :: nvtop_line_plot.

    Critical detail from plot.c:
      getmaxyx(win, rows, cols);
      rows -= 1;
      increment = 100. / (double)(rows);
    so 0% maps to y==rows (== last visible row), never one past it.
    Our earlier bug used ``rows = height`` without the -=1, so 0% was drawn
    on an invisible extra row; fold-back left a gap at big 100↔0 corners
    (the red-box break).
    """
    num_lines = 2
    # Visible plot rows in the Rich table (like ncurses window height).
    win_rows = max(int(height), 2)
    # nvtop: rows -= 1 before data_level / increment
    rows = win_rows - 1
    increment = 100.0 / float(rows)

    cols = max(int(width), num_lines)
    if cols % num_lines:
        cols -= cols % num_lines
    cols = max(cols, num_lines)
    num_data = cols
    n_samples = num_data // num_lines

    util = _right_align_zeros(util_history, n_samples)
    mem = _right_align_zeros(mem_history, n_samples)

    data: list[float] = []
    for s in range(n_samples):
        data.append(util[s])
        data.append(mem[s])

    HLINE, VLINE = "─", "│"
    ULCORNER, URCORNER = "┌", "┐"
    LLCORNER, LRCORNER = "└", "┘"
    TTEE, BTEE, PLUS = "┬", "┴", "┼"

    # y in 0..rows inclusive == 0..win_rows-1
    grid = [[" " for _ in range(cols)] for _ in range(win_rows)]
    colors: list[list[str | None]] = [[None for _ in range(cols)] for _ in range(win_rows)]
    line_colors = ["cyan", "dark_orange"]
    legends = ["GPU0 %", "GPU0 mem%"]

    def set_cell(r: int, c: int, ch: str, color: str) -> None:
        # Clamp to visible rows only (0..rows).
        if r < 0:
            r = 0
        elif r > rows:
            r = rows
        if 0 <= c < cols:
            grid[r][c] = ch
            colors[r][c] = color

    lvl_before = [_data_level(rows, data[k], increment) for k in range(num_lines)]

    i = 0
    while i < num_data and i < cols:
        for k in range(num_lines):
            if i + k >= len(data):
                break
            lvl_now = _data_level(rows, data[i + k], increment)
            # Keep levels inside the window (numerical safety).
            lvl_now = max(0, min(rows, lvl_now))
            lvl_before[k] = max(0, min(rows, lvl_before[k]))
            color = line_colors[k]
            col = i + k

            if lvl_before[k] < lvl_now or lvl_before[k] > lvl_now:
                drawing_down = lvl_before[k] < lvl_now
                bottom = lvl_before[k] if drawing_down else lvl_now
                top = lvl_now if drawing_down else lvl_before[k]
                set_cell(bottom, col, URCORNER if drawing_down else ULCORNER, color)
                set_cell(top, col, LLCORNER if drawing_down else LRCORNER, color)
                if top - bottom > 1:
                    for r in range(bottom + 1, top):
                        set_cell(r, col, VLINE, color)
                for j in range(num_lines):
                    if j == k:
                        continue
                    jc = line_colors[j]
                    lj = max(0, min(rows, lvl_before[j]))
                    if lj == top:
                        set_cell(top, col, BTEE, jc)
                    elif lj == bottom:
                        set_cell(bottom, col, TTEE, jc)
                    elif bottom < lj < top:
                        set_cell(lj, col, PLUS, jc)
                    else:
                        set_cell(lj, col, HLINE, jc)
            else:
                set_cell(lvl_now, col, HLINE, color)
                for j in range(num_lines):
                    if j != k and lvl_before[j] != lvl_now:
                        set_cell(
                            max(0, min(rows, lvl_before[j])),
                            col,
                            HLINE,
                            line_colors[j],
                        )

            lvl_before[k] = lvl_now
        i += num_lines

    # Y ticks like initialize_gpu_mem_plot (positions relative to plot rows).
    # nvtop prints at: 1, 1+rows/4, 1+rows/2, 1+rows*3/4, rows  within the
    # outer window; inside the plot window those are 0, rows/4, ...
    tick_at: dict[int, int] = {}
    for tick, row_expr in (
        (100, 0),
        (75, rows // 4),
        (50, rows // 2),
        (25, (rows * 3) // 4),
        (0, rows),
    ):
        tick_at.setdefault(max(0, min(rows, row_expr)), tick)

    box = Table(show_header=False, expand=True, box=None, padding=0)
    box.add_column("y", width=4, justify="right", no_wrap=True)
    box.add_column("plot", justify="left", no_wrap=True)

    for r in range(win_rows):
        y_txt = Text(f"{tick_at[r]:3d}" if r in tick_at else "   ", style="bright_black")
        line = Text()
        line.append("│", style="bright_black")
        for c in range(cols):
            ch = grid[r][c]
            st = colors[r][c] or "bright_black"
            line.append(ch if ch != " " else " ", style=st)
        line.append("│", style="bright_black")
        if r < num_lines:
            line.append(" ", style="bright_black")
            line.append(legends[r], style=line_colors[r])
        box.add_row(y_txt, line)

    axis = Text("└" + "─" * cols + "┘", style="bright_black")
    left, mid, right = f"-{n_samples}", f"-{n_samples // 2}", "-0"
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
