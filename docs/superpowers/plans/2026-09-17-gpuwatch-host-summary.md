# GPUWatch Host Summary Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight per-server host summary line (CPU%, memory, load1, disk R/W MB/s) to the GPUWatch TUI without a process table.

**Architecture:** Extend the existing remote `nvml_probe.py` so one SSH round-trip also samples `/proc` for host metrics; parse into optional `HostInfo` on `ServerSnapshot`; render one summary line above each server's GPU list in `server_panel.py`.

**Tech Stack:** Python 3.10+, stdlib on remote, Textual TUI locally, existing SSH probe path. No new runtime dependencies.

## Global Constraints

- Zero new remote packages; `/proc` + stdlib only for host metrics
- Sample delta sleep ≈ 0.1s; do not raise `refresh_seconds`
- Physical disks only (skip `loop*`, `ram*`); prefer whole-disk rows
- Missing/failed `host` must not break GPU display
- No process table in v1
- Keep changes on fork `Som5ra/gpuwatch` (no upstream PR)
- Conventional Commits (`feat(scope): ...`); author style matches repo

---

### Task 1: HostInfo model + probe JSON parsing

**Files:**
- Modify: `gpuwatch/models.py`
- Create: `tests/test_host_info.py` (add `pytest` as optional/dev if needed; or a tiny stdlib unittest module under `tests/`)

**Interfaces:**
- Produces: `@dataclass class HostInfo` with `cpu_percent: float`, `mem_used_mb: int`, `mem_total_mb: int`, `load1: float`, `disk_read_mb_s: float`, `disk_write_mb_s: float`
- Produces: `HostInfo.from_probe(data: dict[str, Any]) -> HostInfo`
- Produces: `ServerSnapshot.host: HostInfo | None = None`; `from_probe` sets `host` from `data["host"]` when present and valid

- [ ] **Step 1: Write failing tests** for `HostInfo.from_probe` happy path and `ServerSnapshot.from_probe` with/without `host` key
- [ ] **Step 2: Run tests — expect fail**
- [ ] **Step 3: Implement `HostInfo` and wire into `ServerSnapshot`**
- [ ] **Step 4: Run tests — expect pass**
- [ ] **Step 5: Commit** `feat(models): add optional HostInfo on ServerSnapshot`

---

### Task 2: Collect host metrics inside nvml_probe

**Files:**
- Modify: `gpuwatch/nvml_probe.py`
- Create/Modify: `tests/test_host_probe_unit.py` (pure functions tested locally; mock `/proc` via temp files or injected readers if refactored)

**Interfaces:**
- Produces: helpers `_read_cpu_times()`, `_cpu_percent(t0, t1)`, `_read_mem()`, `_read_load1()`, `_read_disk_sectors()`, `_disk_rates(s0, s1, dt)` 
- Produces: `probe()` return dict gains `"host": { ... }` on success; on host failure omit `host` or set `"host": null` while still returning `gpus` when GPU ok
- Constraint: single ~0.1s sleep shared by CPU + disk sample pair; no subprocess for host path

Algorithm sketch:
1. Read CPU totals + disk sector counters
2. `time.sleep(0.1)`
3. Read again; compute deltas
4. Read mem + load (single shot is fine)
5. Attach `host` object to successful probe result

Disk filter: include devices that look like whole disks (`sdX`, `nvmeXnY`, `vdX`, `hdX`, `xvdX`, `mmcblkN` without `p` partition suffix); exclude `loop`, `ram`, `dm-` optional (mapper often duplicates — **exclude `dm-` and partitions** to avoid double count).

- [ ] **Step 1: Unit tests for CPU percent and disk device filter / rate math**
- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement host collectors and call from `probe()`**
- [ ] **Step 4: Run — expect pass**
- [ ] **Step 5: Commit** `feat(probe): collect CPU mem load disk rates in SSH probe`

---

### Task 3: Summary line in server panel UI

**Files:**
- Modify: `gpuwatch/ui/server_panel.py` (primary)
- Possibly: `gpuwatch/ui/gpu_bar.py` only if color helpers are reused from there — prefer small local color helper or reuse existing threshold functions without duplication if already exported

**Interfaces:**
- Consumes: `ServerSnapshot.host: HostInfo | None`
- Produces: one Textual line above GPU widgets: `CPU …% | MEM …/…G (…%) | LOAD … | DISK ↑… ↓… MB/s`
- Compact mode: keep the line
- `host is None` or error: show `—` placeholders; GPUs unchanged

- [ ] **Step 1: Add a render helper `_format_host_summary(host: HostInfo | None) -> str` with a tiny unit test if pure**
- [ ] **Step 2: Insert the summary widget/line into the server panel layout above GPU list**
- [ ] **Step 3: Manually sanity-check layout logic (compact vs normal) in code review**
- [ ] **Step 4: Commit** `feat(ui): show host summary bar above GPU list`

---

### Task 4: Docs + design/plan in repo

**Files:**
- Modify: `README.md` — short note that each server shows CPU/mem/load/disk summary
- Create: `docs/superpowers/specs/2026-09-17-gpuwatch-host-summary-design.md` (from attached design)
- Create: `docs/superpowers/plans/2026-09-17-gpuwatch-host-summary.md` (this plan)

- [ ] **Step 1: Update README feature list**
- [ ] **Step 2: Add docs from attachments**
- [ ] **Step 3: Commit** `docs: host summary design and README`

---

### Task 5: Verification

- [ ] Run unit tests
- [ ] `uv sync` / `uv run python -c` import check
- [ ] If no live SSH in CI, document manual check: enable one host in `servers.yml`, confirm summary updates and GPU still works when host block forced to fail

**Done when:** PR (or branch) on `Som5ra/gpuwatch` implements all of the above; no new runtime deps; light `/proc` sampling only.
