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


def history_plot(
    values: Sequence[float],
    *,
    width: int = 48,
    height: int = 4,
    label: str = "GPU",
) -> Table:
    """nvtop-like multi-row area plot (0-100%)."""
    box = Table(show_header=False, expand=False, box=None, padding=0)
    box.add_column("y", width=4, justify="right")
    box.add_column("plot", justify="left")

    samples = _resample([float(v) for v in values], width)
    # Pad left with zeros until we have history
    if len(values) < width:
        pad = width - len(values)
        samples = [0.0] * pad + [float(v) for v in values]

    rows: list[Text] = [Text() for _ in range(height)]
    for v in samples:
        level = int(round(max(0.0, min(v, 100.0)) / 100.0 * height * 8))
        for r in range(height):
            # top row is high values
            row_from_top = r
            # capacity in this row: 8 sublevels
            lo = (height - 1 - row_from_top) * 8
            hi = lo + 8
            if level >= hi:
                ch = "█"
                style = "yellow"
            elif level > lo:
                ch = _LEVELS[level - lo]
                style = "yellow"
            else:
                ch = " "
                style = "bright_black"
            rows[r].append(ch, style=style)

    y_labels = ["100", " 75", " 50", " 25"] if height == 4 else [f"{int(100*(height-r)/height):3d}" for r in range(height)]
    for r in range(height):
        label_t = Text(y_labels[r] if r < len(y_labels) else "", style="bright_black")
        frame = Text("│", style="bright_black")
        frame += rows[r]
        frame.append("│", style="bright_black")
        if r == 0:
            frame.append(f" {label}", style="bright_black")
        box.add_row(label_t, frame)

    axis = Text("└" + "─" * width + "┘", style="bright_black")
    box.add_row(Text("  0", style="bright_black"), axis)
    return box


def nvtop_gpu_block(
    gpu: GPUInfo,
    *,
    compact: bool = False,
    util_history: Sequence[float] | None = None,
    mem_history: Sequence[float] | None = None,
    plot_width: int = 48,
) -> Table:
    """Render one GPU in an nvtop-like block with optional history plots."""
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
        # one-line spark even in compact
        hist = list(util_history or [])
        if hist:
            spark = Text("  ")
            for v in _resample(hist, min(plot_width, 32)):
                idx = int(round(max(0.0, min(v, 100.0)) / 100.0 * 8))
                spark.append(_LEVELS[idx] if idx > 0 else " ", style="yellow")
            box.add_row(spark)
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
    meta.append(f"{mem_pct:.0f}%", style="yellow")
    box.add_row(meta)

    util_h = list(util_history or [])
    mem_h = list(mem_history or [])
    if util_h:
        box.add_row(Text(""))
        box.add_row(history_plot(util_h, width=plot_width, height=4, label="GPU-Util"))
    if mem_h:
        box.add_row(Text(""))
        box.add_row(history_plot(mem_h, width=plot_width, height=3, label="MEM-Util"))
    return box
