# GPUWatch host summary bar (v1)

Date: 2026-09-17  
Repo: fork of `GPIOX/gpuwatch` under the user's GitHub (`Som5ra`), personal use first; upstream PR optional later.

## Goal

Keep the existing multi-host GPU TUI, and add a **lightweight host summary line** above each selected server's GPU block so you can see whether the machine is busy without opening htop/Netdata.

## Non-goals (v1)

- No process table / interactive htop
- No network metrics
- No new remote agents, packages, or persistent files on remotes
- No faster poll interval than today
- No new third-party dependencies

## Approach

**Same SSH probe round-trip** as today's NVML probe. Extend the remote Python script so one JSON payload includes both `gpus` and `host`. Collector/UI already poll once per refresh; host metrics ride along.

## Data collection (remote, stdlib only)

Read from `/proc` only:

| Metric | Source | Notes |
|--------|--------|--------|
| CPU % | `/proc/stat` | Two samples ~0.1s apart; total non-idle / total delta. One aggregate %, not per-core bars. |
| Memory | `/proc/meminfo` | `MemAvailable` / `MemTotal` → used and %. |
| Load | `/proc/loadavg` | 1-minute load only. |
| Disk R/W MB/s | `/proc/diskstats` | Two samples aligned with the CPU sleep; sum sectors across **physical** disks only (skip `loop*`, `ram*`, partitions if parent disk is counted — prefer whole disks like `nvme0n1`, `sda`). |

Constraints for lightness:

- Sleep between samples ≈ **0.1s** (not 1s)
- No subprocess for host metrics (`iostat` / `vmstat` not required)
- No disk writes; ephemeral JSON on stdout only
- If host collection fails, still return GPUs; `host` omitted or marked error

## Data model

Add optional `HostInfo` on `ServerSnapshot`:

- `cpu_percent: float`
- `mem_used_mb: int`, `mem_total_mb: int`
- `load1: float`
- `disk_read_mb_s: float`, `disk_write_mb_s: float`

Old probes / missing `host` must not crash the TUI.

## UI

For each selected server section, **one summary line above the GPU list**, roughly:

`CPU 42% | MEM 31.2/128G (24%) | LOAD 3.1 | DISK ↑120 ↓45 MB/s`

- Keep the line in compact mode
- Color thresholds reuse existing green / yellow / red style where natural (CPU, mem); disk rates can stay neutral or soft scale
- On host failure: show `—` (or short error) on that line; GPU block unchanged

## Error handling

- GPU path unchanged
- Host partial failure → summary degraded, session still useful
- Auth / timeout / no python: same status chips as today

## Testing / done when

- Fork exists under user GitHub
- With `servers.yml` pointing at at least one Linux SSH host: summary line shows plausible CPU/mem/load/disk rates
- Remote without changes still works (zero install)
- Compact mode still shows the summary line
- No new dependencies in `pyproject.toml` for this feature
- Probe stays clearly lighter than Netdata (single short `/proc` sample pair per poll)

## Out of scope follow-ups

- Top-N CPU/mem processes
- Per-core CPU bars
- Network throughput
- Upstream PR to `GPIOX/gpuwatch`
