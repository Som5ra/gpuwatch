"""
Single-server GPU panel widget.

Renders GPU utilization bars, memory usage, temperatures, power draw,
and running processes for one server. Updates on each polling cycle.
"""

from __future__ import annotations

import time
from collections import deque

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from ..models import HostInfo, ServerSnapshot

from .gpu_bar import _bar_style, _format_mem, nvtop_gpu_block



def _mini_bar(percent: float, width: int = 10) -> Text:
    """Compact bar: yellow fill, empty unused."""
    pct = max(0.0, min(float(percent), 100.0))
    filled = int(round(pct / 100.0 * width))
    filled = min(filled, width)
    out = Text()
    if filled:
        out.append("█" * filled, style="yellow")
    if width - filled:
        out.append(" " * (width - filled), style="bright_black")
    out.append(f"{pct:3.0f}%", style="yellow")
    return out


def _mem_stack_bar(host: HostInfo, width: int = 24) -> Text:
    """Mem bar: yellow used portion, unused left blank."""
    total = max(host.mem_total_mb, 1)
    # used + buffers + cache as "occupied" like a simple meter
    occupied = max(host.mem_used_mb, 0) + max(host.mem_buffers_mb, 0) + max(host.mem_cached_mb, 0)
    occupied = min(occupied, total)
    filled = int(round(occupied / total * width))
    filled = min(filled, width)
    out = Text()
    if filled:
        out.append("█" * filled, style="yellow")
    if width - filled:
        out.append(" " * (width - filled), style="bright_black")
    used_g = host.mem_used_mb / 1024
    total_g = host.mem_total_mb / 1024
    out.append(f" {used_g:.1f}/{total_g:.1f}G", style="white")
    return out


def _format_host_summary(host_info: HostInfo | None) -> Table:
    """htop-style CPU per-core meters + Mem/Swap + load/disk."""
    box = Table(show_header=False, expand=True, box=None, padding=0)
    box.add_column("meters", justify="left")

    if host_info is None:
        box.add_row(Text("CPU/MEM —", style="bright_black"))
        return box

    cores = host_info.cpu_per_core or [host_info.cpu_percent]
    n = len(cores)
    # Choose columns by core count (htop-like multi-column)
    if n <= 8:
        cols, bar_w = 2, 12
    elif n <= 16:
        cols, bar_w = 4, 8
    elif n <= 32:
        cols, bar_w = 4, 6
    else:
        cols, bar_w = 8, 4

    cpu_grid = Table(show_header=False, expand=False, box=None, padding=(0, 1))
    for _ in range(cols):
        cpu_grid.add_column(justify="left")

    # Header
    head = Text()
    head.append(f"CPU ({n} cores)  avg ", style="bright_black")
    head.append(f"{host_info.cpu_percent:.0f}%", style=_bar_style(host_info.cpu_percent))
    box.add_row(head)

    row: list[Text] = []
    for i, pct in enumerate(cores):
        cell = Text()
        cell.append(f"{i:>2}[", style="bright_black")
        cell += _mini_bar(pct, width=bar_w)
        cell.append("]", style="bright_black")
        row.append(cell)
        if len(row) == cols:
            cpu_grid.add_row(*row)
            row = []
    if row:
        while len(row) < cols:
            row.append(Text(""))
        cpu_grid.add_row(*row)
    box.add_row(cpu_grid)

    mem_line = Text()
    mem_line.append("Mem [", style="bright_black")
    mem_line += _mem_stack_bar(host_info, width=28)
    mem_line.append("]", style="bright_black")
    box.add_row(mem_line)

    if host_info.swap_total_mb > 0:
        sw = host_info.swap_used_mb / max(host_info.swap_total_mb, 1) * 100.0
        swap_line = Text()
        swap_line.append("Swp [", style="bright_black")
        swap_line += _mini_bar(sw, width=28)
        swap_line.append(
            f" {host_info.swap_used_mb/1024:.1f}/{host_info.swap_total_mb/1024:.1f}G",
            style="white",
        )
        swap_line.append("]", style="bright_black")
        box.add_row(swap_line)

    foot = Text()
    foot.append("Load ", style="bright_black")
    foot.append(f"{host_info.load1:.2f}", style="white")
    foot.append("  Disk ", style="bright_black")
    foot.append(f"↑{host_info.disk_read_mb_s:.0f}", style="cyan")
    foot.append(" ", style="bright_black")
    foot.append(f"↓{host_info.disk_write_mb_s:.0f}", style="magenta")
    foot.append(" MB/s", style="bright_black")
    box.add_row(foot)
    return box


def _truncate(text: str, max_len: int = 70) -> str:
    """Truncate a string if too long, appending '…'."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"




class ServerPanel(Static):
    """A panel displaying one server's GPU status."""

    def __init__(self, host: str, label: str) -> None:
        super().__init__("")
        self._host = host
        self._label = label
        self._snapshot: ServerSnapshot | None = None
        self.compact: bool = False
        self.name_width: int = 10  # set by Dashboard, updated dynamically
        # Per-GPU utilization / memory history for nvtop-like plots
        self._hist_len = 60
        self._util_hist: dict[int, deque[float]] = {}
        self._mem_hist: dict[int, deque[float]] = {}

    @property
    def host(self) -> str:
        return self._host

    def update_snapshot(self, snapshot: ServerSnapshot) -> None:
        """Update with a new snapshot and re-render."""
        self._snapshot = snapshot
        if snapshot.status == "ok":
            for gpu in snapshot.gpus:
                uh = self._util_hist.setdefault(
                    gpu.index, deque(maxlen=self._hist_len)
                )
                mh = self._mem_hist.setdefault(
                    gpu.index, deque(maxlen=self._hist_len)
                )
                uh.append(float(gpu.utilization_gpu))
                mh.append(float(gpu.memory_percent))
        self.refresh(layout=True)

    def render(self) -> Panel:
        if self._snapshot is None:
            return Panel(
                Text("Waiting for first poll...", style="dim"),
                title=self._label,
                border_style="bright_black",
            )

        snap = self._snapshot
        return Panel(
            self._build_content(snap),
            title=self._build_title(snap),
            border_style="bright_black",
        )

    def _build_title(self, snap: ServerSnapshot) -> Text:
        """Build the panel title line: 'two4090     OK  42ms  12:31:04'."""
        title = Text()
        title.append(snap.label, style="bold cyan")

        # Status indicator
        status_colors = {
            "ok": "green",
            "connecting": "yellow",
            "timeout": "red",
            "stale": "yellow",
            "error": "red",
            "auth_error": "red",
            "no_python": "red",
            "down": "red",
        }
        color = status_colors.get(snap.status, "red")
        status_labels = {
            "ok": "OK",
            "connecting": "CONNECTING",
            "timeout": "TIMEOUT",
            "stale": "STALE",
            "error": "ERROR",
            "auth_error": "AUTH ERR",
            "no_python": "NO PYTHON",
            "down": "DOWN",
        }
        label = status_labels.get(snap.status, snap.status.upper())
        title.append(f"  {label}", style=f"bold {color}")

        # Latency
        if snap.latency_ms is not None:
            title.append(
                f"  {snap.latency_ms:.0f}ms", style="bright_black"
            )

        # Last update time
        if snap.updated_at:
            ts = time.strftime("%H:%M:%S", time.localtime(snap.updated_at))
            title.append(f"  {ts}", style="bright_black")

        return title

    def _build_content(self, snap: ServerSnapshot) -> Table:
        """Build a Rich Table of GPU rows + process subtables."""
        if snap.status == "connecting":
            t = Table(show_header=False, expand=True, box=None)
            t.add_row(Text("Connecting...", style="yellow"))
            return t

        # Choose rendering style
        if self.compact:
            gpu_table = self._build_compact(snap)
        else:
            gpu_table = self._build_full(snap)

        # Prepend error banner if there's an error (preserves GPU data below it)
        if snap.error:
            wrapper = Table(show_header=False, expand=True, box=None)
            err_text = Text("Error: ", style="bold red")
            err_text.append(snap.error, style="red")
            wrapper.add_row(err_text)
            if snap.gpus:
                wrapper.add_row(Text(""))
                wrapper.add_row(Text("Showing last known data:", style="dim"))
            wrapper.add_row(Text(""))
            wrapper.add_row(gpu_table)
            return wrapper

        return gpu_table

    def _build_full(self, snap: ServerSnapshot) -> Table:
        """Host meters + nvtop-like GPU blocks + processes."""
        wrapper = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        wrapper.add_column("body", justify="left")

        wrapper.add_row(_format_host_summary(snap.host_info))
        wrapper.add_row(Text(""))

        for gpu in snap.gpus:
            wrapper.add_row(nvtop_gpu_block(
                gpu,
                compact=False,
                util_history=self._util_hist.get(gpu.index),
                mem_history=self._mem_hist.get(gpu.index),
            ))
            wrapper.add_row(Text(""))

        # ── Process details (free-form indented text below GPU grid) ──
        for gpu in snap.gpus:
            if gpu.processes:
                wrapper.add_row(Text(""))  # spacer
                wrapper.add_row(Text(
                    f"  GPU {gpu.index}  PID      Mem     Command",
                    style="bold underline",
                ))
                for proc in gpu.processes:
                    mem_str = _format_mem(proc.gpu_memory_mb)
                    cmd = _truncate(proc.cmdline or proc.name)
                    wrapper.add_row(Text(
                        f"        {proc.pid:<7} {mem_str:>9}  {cmd}",
                        style="green",
                    ))

            if gpu.other_users:
                wrapper.add_row(Text(""))  # spacer
                for ou in gpu.other_users:
                    mem_str = _format_mem(ou.total_memory_mb)
                    wrapper.add_row(Text(
                        f"  GPU {gpu.index}  {ou.user}: {ou.process_count} proc, {mem_str}",
                        style="dim",
                    ))

        return wrapper

    def _build_compact(self, snap: ServerSnapshot) -> Table:
        """Compact: host meters + one-line nvtop GPU rows."""
        wrapper = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        wrapper.add_column("body", justify="left")
        wrapper.add_row(_format_host_summary(snap.host_info))
        wrapper.add_row(Text(""))
        for gpu in snap.gpus:
            wrapper.add_row(nvtop_gpu_block(
                gpu,
                compact=True,
                util_history=self._util_hist.get(gpu.index),
                mem_history=self._mem_hist.get(gpu.index),
                plot_width=32,
            ))
        return wrapper
