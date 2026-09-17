<!-- som5ra-fork-install -->
# GPU Watch（Som5ra 维护版）

这是 [GPIOX/gpuwatch](https://github.com/GPIOX/gpuwatch) 的个人维护 fork：<https://github.com/Som5ra/gpuwatch>。

相对上游，本仓库额外包括：

- 每台机器顶部的主机摘要（CPU% / 内存 / 负载 / 磁盘读写）
- 更接近 nvtop 的 GPU 历史曲线（ACS 阶梯折线）
- DGX Spark **GB10** 统一内存适配（驱动报 Memory Not Supported 时，用进程 GPU 显存合计 + 主机 MemTotal）

下面先讲**怎么安装本 fork**。更下面保留上游原有说明（安装命令仍指向 GPIOX，仅供对照）。

## 快速安装（本 fork）

本地需要 **Python 3.10+** 和 [`uv`](https://docs.astral.sh/uv/)。远程机器**不用装 gpuwatch**，有 NVIDIA 驱动 + Python 3，并配置好 **SSH 公钥免密** 即可。

```bash
uv tool install --force git+https://github.com/Som5ra/gpuwatch@main
gpuwatch
```

更新到最新：

```bash
uv tool install --force git+https://github.com/Som5ra/gpuwatch@main
```

### Windows

可以原生跑（需安装 [OpenSSH Client](https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse)，终端里能执行 `ssh`），也建议用 Windows Terminal。配置文件在用户目录：

- `%USERPROFILE%\.ssh\config`
- `%USERPROFILE%\.config\gpuwatch\servers.yml`

在 WSL 里安装运行同样可以。

### SSH（必需，免密）

本工具默认 `BatchMode=yes`，**不支持交互输密码**。请先公钥登录：

```bash
ssh-keygen -t ed25519   # 若还没有密钥
ssh-copy-id user@host   # Windows 可手动把公钥追加到远端 ~/.ssh/authorized_keys
```

`~/.ssh/config` 示例：

```sshconfig
Host 5090-1
  HostName 100.x.x.x
  User yourname

Host dgx01
  HostName 100.x.x.x
  User yourname
```

确认 `ssh 5090-1` 不再要密码。

### 服务器列表（推荐）

创建 `~/.config/gpuwatch/servers.yml`（Linux / macOS / WSL）：

```yaml
refresh_seconds: 1.5
timeout_seconds: 25.0
servers:
  - host: "5090-1"     # 必须等于 ssh config 里的 Host 别名
    label: "5090-1"    # 界面显示名，可省略
    enabled: true      # 启动时默认勾选；不写则为 false
    timeout: 25        # 可选，单机覆盖超时（秒）

  - host: "4090"       # 纯数字主机名务必加引号
    label: "4090"
    enabled: true

  - host: "dgx01"
    label: "dgx01"
    enabled: true
```

说明：

- 写了 `servers:` 之后，界面**只显示 yml 里列出的机器**。
- 没有 yml 时，会列出 `~/.ssh/config` 里大部分 Host，默认不勾选，在界面里用 `Space` 勾选。
- Tailscale / 慢链路把 `timeout_seconds` 或单机 `timeout` 调到 20～30。

### 常用按键

| 按键 | 作用 |
|------|------|
| `↑` `↓` | 移动服务器列表 |
| `Space` | 勾选 / 取消 |
| `r` | 强制刷新 |
| `c` | 紧凑模式 |
| `q` | 退出 |

### GB10 / DGX Spark 注意

`nvidia-smi` 对 GB10 常显示 Memory Not Supported。本 fork 会把 **compute 进程的 GPU Memory 合计**当作 used，把 **主机 MemTotal** 当作 total（统一内存）。若要这个行为，请安装本仓库，不要装上游 GPIOX 原版。

---

# 以下为上游 README（保留）

<!-- /som5ra-fork-install -->

# GPU Watch

同时看多台远程服务器的 GPU 状态。类似 [nvitop](https://github.com/XuehaiPan/nvitop)，但是跨机器的。

## 它能干什么

左边勾选服务器，右边实时显示每块 GPU 的利用率、显存、温度、功率。每台机器顶部还有一行主机摘要（CPU%、内存、负载、磁盘读写速率）。你自己的 GPU 进程会高亮出来，别人的进程按用户名合并显示，不用在一堆 PID 里找自己的。

远程服务器不需要装任何东西，有 NVIDIA 驱动和 Python 3 就行。数据全部通过 SSH 传输，不写文件。

## 安装

```bash
git clone https://github.com/GPIOX/gpuwatch
cd gpuwatch
uv sync
uv run gpuwatch
```

或者一行搞定：

```bash
uv tool install git+https://github.com/GPIOX/gpuwatch && gpuwatch
```

本地需要 Python 3.10 以上。远程服务器需要 NVIDIA 驱动和 Python 3，配置好 SSH 免密登录。

## 使用

```bash
gpuwatch
```

| 按键 | 作用 |
|------|------|
| `↑` `↓` | 在左侧服务器列表里移动 |
| `Space` | 勾选或取消当前服务器 |
| `r` | 强制刷新所有已选服务器 |
| `c` | 紧凑模式 |
| `q` | 退出 |

## 配置

启动时自动读 `~/.ssh/config`，把里面的 Host 都列出来（github.com 这类代码托管域名自动跳过）。

如果想自定义显示名或默认勾选某些服务器，可以建 `~/.config/gpuwatch/servers.yml`：

```yaml
refresh_seconds: 1.5   # 轮询间隔
timeout_seconds: 15.0  # 每次 SSH 探测的总超时（含 SSH 握手 + 远程执行）
servers:
  - host: two4090
    label: "2x RTX 4090"
    enabled: true
  - host: two4090-ts
    label: "2x RTX 4090 (Tailscale)"
    timeout: 25         # 慢链路可以单独给这台机器加大超时
  - host: a100-server
    label: "8x A100"
```

### 超时怎么办

`timeout_seconds` 覆盖的是**一整次探测**：TCP 连接 + SSH 握手 + 认证 + 远程 python 启动 + NVML 查询。走 Tailscale DERP 中继、跳板机或高延迟链路时，光 SSH 握手就要 8-10 秒（十几个往返），默认 15 秒就是为了留足这个预算；局域网机器一般 1 秒内完成。

某台机器老是 TIMEOUT 就给它单独加 `timeout: 30`。另外 gpuwatch 会复用 SSH 连接（ControlMaster），握手成本只在第一次和断线后付一次，之后的轮询只花一个往返。

## 配色

GPU 指标的颜色根据数值自动变化，像温度计一样一眼能看出负载高低：

| 指标 | 低 | 中 | 高 |
|------|---|---|---|
| GPU 利用率 | `#2ecc71` 绿 (<50%) | `#f39c12` 黄 (50-80%) | `#e74c3c` 红 (>80%) |
| 显存占用 | `#2ecc71` 绿 | `#f39c12` 黄 | `#e74c3c` 红 |
| 温度 | `#5dade2` 蓝 (<50°C) | `#2ecc71` 绿 (50-70°C) | `#f39c12` 黄 / `#e74c3` 红 (>85°C) |
| 功率 | `#2ecc71` 绿 (<50%) | `#f39c12` 黄 (50-80%) | `#e74c3c` 红 (>80%) |

自己的 GPU 进程用亮绿色高亮，其他人的进程用暗灰色聚合显示。

## 原理

本地通过 SSH 把一段 Python 脚本发到远程服务器执行。脚本用 ctypes 直接调 NVIDIA 驱动的 C 库（`libnvidia-ml.so`）拿 GPU 数据，和 nvitop 一样的方式。结果以 JSON 格式从 stdout 返回来，本地解析后渲染。

GPU 进程信息优先用 NVML 接口拿。如果当前用户没权限看其他用户的进程，会自动换 `nvidia-smi` 来查。

整个过程不创建任何临时文件。

## 更新日志

### 2026-09-01

修复慢链路机器（Tailscale DERP 中继、跳板机等）一直显示 TIMEOUT 的问题。命令行手动 `ssh` 能连，但 gpuwatch 里永远超时。

**根因**：高延迟链路上 SSH 握手本身就要 8-10 秒（十几个往返），而探测超时写死为 5 秒——首次探测必超时，且超时后代码把还没建完的 SSH 连接杀掉，下次轮询又从零开始付握手成本，形成永久超时死循环。

修复内容：

- 默认探测超时 5s → 15s，覆盖慢链路的完整握手 + 远程执行预算
- `servers.yml` 里的 `refresh_seconds` / `timeout_seconds` 此前只有文档没有实现，现在真正生效
- 新增按机器单独设置 `timeout`，慢机器不用拖高全局超时
- SSH 连接复用（ControlMaster）存活时间 60s → 120s，暂停再恢复监控时不用重新握手
- ControlPath 无法绑定的受限环境下自动降级为不复用连接，不再报错退出

## License

MIT
