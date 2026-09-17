"""
Rich bar rendering for GPU utilization and memory.

nvtop-inspired meters: yellow fill, blank unused, wrapped in [].
"""

from __future__ import annotations

from rich.style import Style
from rich.table import Table
from rich.text import Text

from ..models import GPUInfo


def _bar_style(percent: float) -> Style:
    """Kept for host meters / legacy callers."""
    if percent < 50:
        return Style(color="green")
    elif percent < 80:
        return Style(color="yellow")
    else:
        return Style(color="red")


def _fill_bar(percent: float, width: int) -> Text:
    """Yellow filled bar; unused is blank spaces."""
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
    """`[████    ]` with yellow fill."""
    out = Text("[", style="bright_black")
    out += _fill_bar(percent, width)
    out.append("]", style="bright_black")
    return out


def utilization_bar(percent: int, width: int = 10) -> Text:
    """GPU util with brackets: `[████  ]  40%`."""
    pct = max(0, min(int(percent), 100))
    out = bracket_bar(pct, width=width)
    out.append(f" {pct:3d}%", style="yellow")
    return out


def _format_mem(mb: int) -> str:
    """Format memory like nvitop/nvtop."""
    if mb >= 20480:
        return f"{mb / 1024:.2f}GiB"
    else:
        return f"{mb}MiB"


def memory_bar(used_mb: int, total_mb: int, width: int = 16) -> Text:
    """GPU mem with brackets: `[██  ] 14MiB / 31.8GiB`."""
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


def nvtop_gpu_block(gpu: GPUInfo, *, compact: bool = False) -> Table:
    """Render one GPU in an nvtop-like block."""
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
    if gpu.utilization_mem:
        meta.append("   MEM-UTIL ", style="bright_black")
        meta.append(f"{gpu.utilization_mem}%", style="yellow")
    box.add_row(meta)
    return box
