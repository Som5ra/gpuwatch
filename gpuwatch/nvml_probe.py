#!/usr/bin/env python3
"""
NVML GPU probe -- runs on remote server via SSH stdin, outputs JSON to stdout.

Zero dependencies: uses only Python stdlib ctypes + libnvidia-ml.so (part of
NVIDIA driver). No pip install required on the remote side.

Usage (on remote server):
    python3 nvml_probe.py          # runs locally
    ssh host 'python3 -' < nvml_probe.py   # sent over SSH stdin

Output: JSON object to stdout, diagnostics to stderr.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import re
import os
import sys
import time
from typing import Any


# ---------------------------------------------------------------------------
# NVML constants
# ---------------------------------------------------------------------------

NVML_TEMPERATURE_GPU = 0
NVML_SUCCESS = 0
NVML_ERROR_NOT_INITIALIZED = 3
NVML_ERROR_INSUFFICIENT_SIZE = 4
NVML_ERROR_NO_PERMISSION = 7

NVML_DEVICE_NAME_BUFFER_SIZE = 96
NVML_DEVICE_UUID_BUFFER_SIZE = 96

# ---------------------------------------------------------------------------
# C struct definitions
# ---------------------------------------------------------------------------


class NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class NvmlUtilization(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


class NvmlProcessInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("usedGpuMemory", ctypes.c_ulonglong),
    ]


# ---------------------------------------------------------------------------
# NVML function signatures
# ---------------------------------------------------------------------------


def _setup_nvml(lib) -> None:
    """Declare argtypes/restype for all NVML functions used."""
    # Init / shutdown
    lib.nvmlInit.restype = ctypes.c_int
    lib.nvmlShutdown.restype = ctypes.c_int

    # Device count
    lib.nvmlDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_uint)]
    lib.nvmlDeviceGetCount.restype = ctypes.c_int

    # Device handle
    lib.nvmlDeviceGetHandleByIndex.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    lib.nvmlDeviceGetHandleByIndex.restype = ctypes.c_int

    # Device name
    lib.nvmlDeviceGetName.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    lib.nvmlDeviceGetName.restype = ctypes.c_int

    # Device UUID
    lib.nvmlDeviceGetUUID.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    lib.nvmlDeviceGetUUID.restype = ctypes.c_int

    # Memory info
    lib.nvmlDeviceGetMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NvmlMemory),
    ]
    lib.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int

    # Utilization rates
    lib.nvmlDeviceGetUtilizationRates.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NvmlUtilization),
    ]
    lib.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int

    # Temperature
    lib.nvmlDeviceGetTemperature.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.nvmlDeviceGetTemperature.restype = ctypes.c_int

    # Power usage (milliwatts)
    lib.nvmlDeviceGetPowerUsage.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.nvmlDeviceGetPowerUsage.restype = ctypes.c_int

    # Power management limit (milliwatts)
    lib.nvmlDeviceGetPowerManagementLimit.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.nvmlDeviceGetPowerManagementLimit.restype = ctypes.c_int

    # Compute running processes
    lib.nvmlDeviceGetComputeRunningProcesses.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(NvmlProcessInfo),
    ]
    lib.nvmlDeviceGetComputeRunningProcesses.restype = ctypes.c_int


# ---------------------------------------------------------------------------
# Process info helpers (pure Python, /proc filesystem)
# ---------------------------------------------------------------------------


def _read_proc_comm(pid: int) -> str | None:
    """Read process name from /proc/<pid>/comm."""
    try:
        path = f"/proc/{pid}/comm"
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, PermissionError):
        return None


def _read_proc_cmdline(pid: int) -> str | None:
    """Read full command line from /proc/<pid>/cmdline.

    Arguments are separated by null bytes; we replace them with spaces.
    Returns None on failure (process exited, permission denied, etc.).
    """
    try:
        path = f"/proc/{pid}/cmdline"
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            return None
        # Replace null bytes with spaces
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (OSError, PermissionError):
        return None


def _read_proc_uid(pid: int) -> int | None:
    """Read UID (owner) of /proc/<pid>/status. Returns None on failure."""
    try:
        path = f"/proc/{pid}/status"
        with open(path, "r") as f:
            for line in f:
                if line.startswith("Uid:"):
                    # "Uid:\t1000\t1000\t1000\t1000"
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, PermissionError):
        pass
    return None


def _uid_to_name(uid: int) -> str | None:
    """Convert numeric UID to username."""
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _run_nvsmi_processes() -> dict[str, list[dict[str, Any]]]:
    """Fallback: run nvidia-smi to get GPU process info.

    NVML process queries may return NVML_ERROR_NO_PERMISSION when the
    current user cannot read other users' process details. nvidia-smi
    handles this via driver-level access, so we use it as a fallback.

    Returns: dict mapping GPU UUID -> list of {pid, name, used_memory_mb}
    """
    import subprocess

    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    result: dict[str, list[dict[str, Any]]] = {}
    for line in output.decode("utf-8", errors="replace").strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",", 3)]
        if len(parts) < 4:
            continue
        gpu_uuid, pid_str, proc_name, mem_str = parts
        try:
            pid = int(pid_str)
            mem_mb = int(mem_str)
        except ValueError:
            continue
        if gpu_uuid not in result:
            result[gpu_uuid] = []
        result[gpu_uuid].append(
            {"pid": pid, "name": proc_name, "used_memory_mb": mem_mb}
        )
    return result


def _try_nvml_v2_memory(lib, handle) -> tuple[int, int] | None:
    """Try NVML v2 memory info to get reserved field. Returns (used_mb, free_mb) or None."""
    try:
        func = lib.nvmlDeviceGetMemoryInfo_v2
    except AttributeError:
        return None

    class NvmlMemoryV2(ctypes.Structure):
        _fields_ = [
            ("version", ctypes.c_uint),
            ("_pad", ctypes.c_uint),
            ("total", ctypes.c_ulonglong),
            ("reserved", ctypes.c_ulonglong),  # must precede free/used per NVML ABI
            ("free", ctypes.c_ulonglong),
            ("used", ctypes.c_ulonglong),
        ]

    func.argtypes = [ctypes.c_void_p, ctypes.POINTER(NvmlMemoryV2)]
    func.restype = ctypes.c_int
    m = NvmlMemoryV2()
    # NVML versioned structs: version = sizeof(struct) | (major << 24)
    m.version = ctypes.sizeof(NvmlMemoryV2) | (2 << 24)
    rc = func(handle, ctypes.byref(m))
    if rc == NVML_SUCCESS:
        total_mb = int(m.total // (1024 * 1024))
        free_mb = int(m.free // (1024 * 1024))
        used_mb = int((m.total - m.free - m.reserved) // (1024 * 1024))
        return (used_mb, free_mb)
    return None


def _calibrate_reserved(lib, handle, count) -> dict[int, int]:
    """One-time: run nvidia-smi to get per-GPU reserved memory offsets.
    Returns {gpu_index: reserved_mb} so subsequent polls can compute
    user-visible used = NVML_total - NVML_free - reserved.
    """
    import subprocess
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=3,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    offsets: dict[int, int] = {}
    smi_mem: dict[int, int] = {}
    for line in output.decode("utf-8", errors="replace").strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                smi_mem[int(parts[0])] = int(parts[1])
            except ValueError:
                pass

    # For each GPU, compute reserved = NVML(total-free) - nvidia_smi(used)
    for i in range(count.value):
        if i not in smi_mem:
            continue
        test_handle = ctypes.c_void_p()
        rc = lib.nvmlDeviceGetHandleByIndex(i, ctypes.byref(test_handle))
        if rc != NVML_SUCCESS:
            continue
        mem = NvmlMemory()
        rc = lib.nvmlDeviceGetMemoryInfo(test_handle, ctypes.byref(mem))
        if rc != NVML_SUCCESS:
            continue
        nvml_used_mb = int((mem.total - mem.free) // (1024 * 1024))
        reserved = nvml_used_mb - smi_mem[i]
        if reserved > 0:
            offsets[i] = reserved

    return offsets


def _gpu_processes(
    lib,
    handle,
    gpu_uuid: str,
    own_user: str | None,
    nvsmi_cache: list[dict[str, list[dict[str, Any]]] | None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect processes for one GPU.

    Tries NVML first. Falls back to nvidia-smi (lazily fetched once
    and cached via nvsmi_cache) if NVML returns NO_PERMISSION.
    """
    raw_procs: list[dict[str, Any]] = []
    use_nvsmi = False

    # Try NVML
    try:
        proc_count = ctypes.c_uint(0)
        rc = lib.nvmlDeviceGetComputeRunningProcesses(
            handle, ctypes.byref(proc_count), None
        )
        if rc == NVML_ERROR_NO_PERMISSION:
            use_nvsmi = True
        elif rc in (NVML_SUCCESS, NVML_ERROR_INSUFFICIENT_SIZE) and proc_count.value > 0:
            buf = (NvmlProcessInfo * proc_count.value)()
            rc2 = lib.nvmlDeviceGetComputeRunningProcesses(
                handle, ctypes.byref(proc_count), buf
            )
            if rc2 == NVML_SUCCESS:
                for j in range(proc_count.value):
                    pi = buf[j]
                    name = _read_proc_comm(pi.pid)
                    uid = _read_proc_uid(pi.pid)
                    username = _uid_to_name(uid) if uid is not None else None
                    gpu_mem = pi.usedGpuMemory
                    if gpu_mem >= (1 << 63):
                        gpu_mem = 0
                    raw_procs.append(
                        {
                            "pid": pi.pid,
                            "gpu_memory_mb": int(gpu_mem // (1024 * 1024)),
                            "name": name or "?",
                            "user": username,
                        }
                    )
    except Exception:
        pass

    # Fallback to nvidia-smi data, fetched lazily on first NO_PERMISSION
    # and cached for subsequent GPUs in this probe cycle.
    if use_nvsmi:
        if nvsmi_cache[0] is None:
            nvsmi_cache[0] = _run_nvsmi_processes()
        for pi in nvsmi_cache[0].get(gpu_uuid, []):
            # Resolve user from /proc for own/other classification
            uid = _read_proc_uid(pi["pid"])
            username = _uid_to_name(uid) if uid is not None else None
            raw_procs.append(
                {
                    "pid": pi["pid"],
                    "gpu_memory_mb": pi["used_memory_mb"],
                    "name": pi["name"],
                    "user": username,
                }
            )

    # -- Classify: own vs other --
    own_procs: list[dict[str, Any]] = []
    other_map: dict[str, dict[str, int]] = {}

    for rp in raw_procs:
        user = rp.get("user")
        if own_user and user == own_user:
            if not use_nvsmi:
                # NVML path: we already have user info, add cmdline
                cmdline = _read_proc_cmdline(rp["pid"])
            else:
                # nvidia-smi path: cmdline from /proc
                cmdline = _read_proc_cmdline(rp["pid"])
            own_procs.append(
                {
                    "pid": rp["pid"],
                    "gpu_memory_mb": rp["gpu_memory_mb"],
                    "name": rp["name"],
                    "user": user,
                    "cmdline": cmdline,
                }
            )
        else:
            key = user or "?"
            if key not in other_map:
                other_map[key] = {"count": 0, "mem": 0}
            other_map[key]["count"] += 1
            other_map[key]["mem"] += rp["gpu_memory_mb"]

    other_list = [
        {"user": u, "process_count": d["count"], "total_memory_mb": d["mem"]}
        for u, d in sorted(other_map.items())
    ]
    return own_procs, other_list



# ---------------------------------------------------------------------------
# Host summary (/proc) — lightweight, stdlib only
# ---------------------------------------------------------------------------

_MIN_HOST_SAMPLE_S = 0.05


def _parse_cpu_line(parts: list[str]) -> tuple[int, int]:
    """Return (idle_including_iowait, total) jiffies from one /proc/stat cpu line."""
    vals = [int(x) for x in parts[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    total = sum(vals)
    return idle, total


def _read_cpu_times() -> tuple[int, int]:
    """Return aggregate (idle, total) from the first cpu line."""
    with open("/proc/stat", "r") as f:
        parts = f.readline().split()
    return _parse_cpu_line(parts)


def _read_cpu_times_all() -> tuple[tuple[int, int], list[tuple[int, int]]]:
    """Return (aggregate, per-core) idle/total jiffies from /proc/stat."""
    aggregate: tuple[int, int] | None = None
    cores: list[tuple[int, int]] = []
    with open("/proc/stat", "r") as f:
        for line in f:
            if not line.startswith("cpu"):
                break
            parts = line.split()
            name = parts[0]
            idle_total = _parse_cpu_line(parts)
            if name == "cpu":
                aggregate = idle_total
            elif name.startswith("cpu") and name[3:].isdigit():
                cores.append(idle_total)
    if aggregate is None:
        aggregate = (0, 0)
    return aggregate, cores


def _cpu_percent(idle0: int, total0: int, idle1: int, total1: int) -> float:
    d_total = total1 - total0
    d_idle = idle1 - idle0
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))


def _is_physical_disk(name: str) -> bool:
    """Whole disks only — skip loop/ram/dm and partitions."""
    if name.startswith(("loop", "ram", "dm-", "md", "zram")):
        return False
    if re.match(r"^nvme\d+n\d+p\d+$", name):
        return False
    if re.match(r"^mmcblk\d+p\d+$", name):
        return False
    if re.match(r"^(sd|vd|hd|xvd)[a-z]+\d+$", name):
        return False
    if re.match(r"^(sd|vd|hd|xvd)[a-z]+$", name):
        return True
    if re.match(r"^nvme\d+n\d+$", name):
        return True
    if re.match(r"^mmcblk\d+$", name):
        return True
    return False


def _read_disk_sectors() -> tuple[int, int]:
    """Sum read/write sectors across physical disks."""
    read_s = 0
    write_s = 0
    with open("/proc/diskstats", "r") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if not _is_physical_disk(name):
                continue
            read_s += int(parts[5])
            write_s += int(parts[9])
    return read_s, write_s


def _read_mem_mb() -> dict[str, int]:
    """htop-like memory breakdown in MiB."""
    meminfo: dict[str, int] = {}
    with open("/proc/meminfo", "r") as f:
        for line in f:
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            meminfo[key] = int(rest.strip().split()[0])  # kB
    total = meminfo.get("MemTotal", 0)
    free = meminfo.get("MemFree", 0)
    buffers = meminfo.get("Buffers", 0)
    cached = meminfo.get("Cached", 0) + meminfo.get("SReclaimable", 0)
    # Approximate htop "used" (non-cache)
    used = max(total - free - buffers - cached, 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    return {
        "mem_used_mb": used // 1024,
        "mem_buffers_mb": buffers // 1024,
        "mem_cached_mb": cached // 1024,
        "mem_total_mb": total // 1024,
        "swap_used_mb": swap_used // 1024,
        "swap_total_mb": swap_total // 1024,
    }


def _read_load1() -> float:
    with open("/proc/loadavg", "r") as f:
        return float(f.read().split()[0])


def _collect_host(sample0: tuple | None = None, t0: float | None = None) -> dict[str, Any] | None:
    """Build host summary dict.

    If sample0/t0 provided, they are the first CPU/disk sample taken earlier
    in the probe (so we reuse NVML work time instead of always sleeping).
    sample0 shape: (aggregate, cores, disk) where aggregate/cores are idle/total.
    """
    try:
        if sample0 is None or t0 is None:
            agg0, cores0 = _read_cpu_times_all()
            disk0 = _read_disk_sectors()
            t0 = time.monotonic()
            time.sleep(_MIN_HOST_SAMPLE_S)
        else:
            agg0, cores0, disk0 = sample0[0], sample0[1], sample0[2]

        elapsed = time.monotonic() - t0
        if elapsed < _MIN_HOST_SAMPLE_S:
            time.sleep(_MIN_HOST_SAMPLE_S - elapsed)

        agg1, cores1 = _read_cpu_times_all()
        disk1 = _read_disk_sectors()
        dt = max(time.monotonic() - t0, 1e-6)

        cpu = _cpu_percent(agg0[0], agg0[1], agg1[0], agg1[1])
        per_core: list[float] = []
        n = min(len(cores0), len(cores1))
        for i in range(n):
            per_core.append(round(_cpu_percent(cores0[i][0], cores0[i][1], cores1[i][0], cores1[i][1]), 1))

        mem = _read_mem_mb()
        load1 = _read_load1()

        sector = 512.0
        r_mb = max(0.0, (disk1[0] - disk0[0]) * sector / dt / (1024 * 1024))
        w_mb = max(0.0, (disk1[1] - disk0[1]) * sector / dt / (1024 * 1024))

        return {
            "cpu_percent": round(cpu, 1),
            "cpu_per_core": per_core,
            "mem_used_mb": mem["mem_used_mb"],
            "mem_buffers_mb": mem["mem_buffers_mb"],
            "mem_cached_mb": mem["mem_cached_mb"],
            "mem_total_mb": mem["mem_total_mb"],
            "swap_used_mb": mem["swap_used_mb"],
            "swap_total_mb": mem["swap_total_mb"],
            "load1": round(load1, 2),
            "disk_read_mb_s": round(r_mb, 1),
            "disk_write_mb_s": round(w_mb, 1),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main probe logic
# ---------------------------------------------------------------------------


def probe(
    own_user: str | None = None,
    reserved_offsets: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Collect GPU information via NVML and return as a dict.

    Args:
        own_user: Highlight processes for this user.
        reserved_offsets: Cached {gpu_index: reserved_mb} from prior
            calibration. If None, NVML v2 is tried first, then
            nvidia-smi is used for one-time calibration.
    """
    t_start = time.monotonic()

    # First host sample (CPU/disk) — finish after NVML so we usually avoid an extra sleep
    host_sample0 = None
    host_t0 = None
    try:
        _agg0, _cores0 = _read_cpu_times_all()
        _disk0 = _read_disk_sectors()
        host_sample0 = (_agg0, _cores0, _disk0)
        host_t0 = time.monotonic()
    except Exception:
        host_sample0 = None
        host_t0 = None

    # Find and load libnvidia-ml
    lib_path = ctypes.util.find_library("nvidia-ml")
    if lib_path is None:
        # Try common locations directly
        for candidate in (
            "libnvidia-ml.so.1",
            "libnvidia-ml.so",
            "/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
            "/usr/lib64/libnvidia-ml.so.1",
            "/usr/lib/libnvidia-ml.so.1",
        ):
            try:
                lib = ctypes.CDLL(candidate)
                lib_path = candidate
                break
            except OSError:
                continue
    else:
        lib = ctypes.CDLL(lib_path)

    if lib_path is None:
        return {
            "ok": False,
            "error": "Cannot find libnvidia-ml.so -- NVIDIA driver not installed?",
            "elapsed_ms": (time.monotonic() - t_start) * 1000,
        }

    _setup_nvml(lib)

    # Initialize NVML
    rc = lib.nvmlInit()
    if rc != NVML_SUCCESS:
        return {
            "ok": False,
            "error": f"nvmlInit failed with code {rc}",
            "elapsed_ms": (time.monotonic() - t_start) * 1000,
        }

    try:
        # Get GPU count
        count = ctypes.c_uint(0)
        rc = lib.nvmlDeviceGetCount(ctypes.byref(count))
        if rc != NVML_SUCCESS:
            return {
                "ok": False,
                "error": f"nvmlDeviceGetCount failed with code {rc}",
                "elapsed_ms": (time.monotonic() - t_start) * 1000,
            }

        gpus: list[dict[str, Any]] = []
        handle = ctypes.c_void_p()

        # Lazy cache for nvidia-smi process fallback.
        nvsmi_cache: list[dict[str, list[dict[str, Any]]] | None] = [None]

        # Memory offset calibration. Try NVML v2 first. If unavailable
        # and no cached offsets, calibrate once via nvidia-smi.
        if reserved_offsets is None:
            # First poll: try v2, fall back to one-time calibration
            if count.value > 0:
                test_h = ctypes.c_void_p()
                rc0 = lib.nvmlDeviceGetHandleByIndex(0, ctypes.byref(test_h))
                if rc0 == NVML_SUCCESS:
                    v2 = _try_nvml_v2_memory(lib, test_h)
                    if v2 is not None:
                        reserved_offsets = {}  # v2 available, no offsets needed
                    else:
                        reserved_offsets = _calibrate_reserved(lib, test_h, count)
                else:
                    reserved_offsets = {}
            else:
                reserved_offsets = {}
        # Cached empty dict means "already tried, fall back to raw NVML".
        # Distinguish from "not yet calibrated" by the fact that it's never
        # None on subsequent polls (collector always sends the cached value).

        for i in range(count.value):
            gpu: dict[str, Any] = {"index": i}

            # Get device handle
            rc = lib.nvmlDeviceGetHandleByIndex(i, ctypes.byref(handle))
            if rc != NVML_SUCCESS:
                gpu["error"] = f"get handle failed: {rc}"
                gpus.append(gpu)
                continue

            # Name
            try:
                name_buf = ctypes.create_string_buffer(NVML_DEVICE_NAME_BUFFER_SIZE)
                lib.nvmlDeviceGetName(handle, name_buf, NVML_DEVICE_NAME_BUFFER_SIZE)
                gpu["name"] = name_buf.value.decode("utf-8", errors="replace")
            except Exception:
                gpu["name"] = "unknown"

            # UUID
            try:
                uuid_buf = ctypes.create_string_buffer(NVML_DEVICE_UUID_BUFFER_SIZE)
                lib.nvmlDeviceGetUUID(handle, uuid_buf, NVML_DEVICE_UUID_BUFFER_SIZE)
                gpu["uuid"] = uuid_buf.value.decode("utf-8", errors="replace")
            except Exception:
                gpu["uuid"] = "unknown"

            # Memory — try v2 first (fast, no subprocess), then cached offsets
            try:
                mem = NvmlMemory()
                lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
                total_mb = int(mem.total // (1024 * 1024))
                free_mb = int(mem.free // (1024 * 1024))

                # Try NVML v2 for user-visible used (= total - free - reserved)
                v2 = _try_nvml_v2_memory(lib, handle)
                if v2 is not None:
                    used_mb, free_mb_adj = v2
                    free_mb = free_mb_adj
                elif i in reserved_offsets:
                    used_mb = total_mb - free_mb - reserved_offsets[i]
                else:
                    # No calibration available — raw NVML value
                    used_mb = total_mb - free_mb

                gpu["memory_total_mb"] = total_mb
                gpu["memory_used_mb"] = max(used_mb, 0)
                gpu["memory_free_mb"] = total_mb - max(used_mb, 0)
            except Exception:
                gpu["memory_total_mb"] = 0
                gpu["memory_used_mb"] = 0
                gpu["memory_free_mb"] = 0

            # Utilization
            try:
                util = NvmlUtilization()
                lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
                gpu["utilization_gpu"] = util.gpu
                gpu["utilization_mem"] = util.memory
            except Exception:
                gpu["utilization_gpu"] = 0
                gpu["utilization_mem"] = 0

            # Temperature
            try:
                temp = ctypes.c_uint(0)
                lib.nvmlDeviceGetTemperature(
                    handle, NVML_TEMPERATURE_GPU, ctypes.byref(temp)
                )
                gpu["temperature_c"] = temp.value
            except Exception:
                gpu["temperature_c"] = 0

            # Power usage (NVML returns milliwatts)
            try:
                power = ctypes.c_uint(0)
                lib.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power))
                gpu["power_watts"] = round(power.value / 1000.0, 1)
            except Exception:
                gpu["power_watts"] = 0.0

            # Power limit
            try:
                power_limit = ctypes.c_uint(0)
                lib.nvmlDeviceGetPowerManagementLimit(
                    handle, ctypes.byref(power_limit)
                )
                gpu["power_limit_watts"] = round(power_limit.value / 1000.0, 1)
            except Exception:
                gpu["power_limit_watts"] = 0.0

            # Compute processes: own user in detail, others aggregated
            processes, other_users = _gpu_processes(
                lib, handle,
                gpu_uuid=gpu.get("uuid", ""),
                own_user=own_user,
                nvsmi_cache=nvsmi_cache,
            )
            gpu["processes"] = processes
            gpu["other_users"] = other_users
            gpus.append(gpu)

        elapsed = (time.monotonic() - t_start) * 1000

        result = {
            "ok": True,
            "gpus": gpus,
            "elapsed_ms": round(elapsed, 1),
            "reserved_offsets": reserved_offsets if reserved_offsets else {},
        }
        host = _collect_host(sample0=host_sample0, t0=host_t0)
        if host is not None:
            result["host"] = host
        return result

    finally:
        lib.nvmlShutdown()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--own-user", default=None, help="Highlight processes for this user")
    parser.add_argument("--reserved-offsets", default=None,
                        help="JSON: {gpu_index: reserved_mb} from prior calibration")
    args = parser.parse_args()

    offsets = None
    if args.reserved_offsets:
        try:
            # Keys arrive as strings from JSON; convert to int
            raw = json.loads(args.reserved_offsets)
            offsets = {int(k): v for k, v in raw.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    result = probe(own_user=args.own_user, reserved_offsets=offsets)
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
