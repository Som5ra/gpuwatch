"""
Asynchronous SSH executor using system `ssh` binary.

Runs the NVML probe script on remote servers via SSH stdin,
captures JSON stdout, respects ~/.ssh/config automatically.

No extra SSH library needed — uses asyncio subprocess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


# Read the bundled probe script once at import time
_PROBE_PATH = Path(__file__).parent / "nvml_probe.py"
_PROBE_SCRIPT = _PROBE_PATH.read_text(encoding="utf-8")

# Default total budget for one probe: SSH handshake + auth + remote python
# startup + NVML query. 15s accommodates slow links (e.g. Tailscale DERP
# relays where a full handshake takes 8-10s); fast LAN hosts finish in <1s.
DEFAULT_TIMEOUT = 15.0

# None = unknown, False = OS refused to bind ControlPath sockets (e.g.
# sandboxed / locked-down HOME), so multiplexing is disabled for all calls.
_multiplexing_supported: bool | None = None


class SSHTimeoutError(asyncio.TimeoutError):
    """Raised when an SSH command exceeds its time limit."""


class SSHCommandError(Exception):
    """Raised when the remote command exits with a non-zero status."""

    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"SSH exited {returncode}: {self.stderr}")


class SSHAuthError(SSHCommandError):
    """Raised on SSH authentication failure (exit 255)."""


class RemotePythonNotFound(SSHCommandError):
    """Raised when python3 is not available on the remote server."""


def _base_ssh_opts() -> list[str]:
    return [
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=3",
        "-o", "StrictHostKeyChecking=accept-new",
    ]


def _can_bind_unix_socket(directory: str) -> bool:
    """Probe whether the OS lets us create AF_UNIX sockets in `directory`.

    Sandboxed / locked-down environments deny socket binds even when the
    directory is writable; ssh only discovers this *after* completing the
    (expensive) handshake, then aborts with 255. Probing upfront avoids
    paying a full handshake for nothing.
    """
    import os as _os
    import socket as _socket
    probe_path = _os.path.join(directory, f".gpuwatch-bindprobe-{_os.getpid()}")
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        sock.bind(probe_path)
        return True
    except OSError:
        return False
    finally:
        sock.close()
        try:
            _os.unlink(probe_path)
        except OSError:
            pass


def _multiplexing_opts() -> list[str] | None:
    """SSH multiplexing options, or None if unsupported on this platform.

    Reusing one TCP+SSH session across polls matters a lot on slow links:
    the expensive handshake (8-10s via DERP relay) is paid once, then every
    probe rides the existing master in ~1 round trip.
    """
    global _multiplexing_supported
    import sys as _sys
    if _sys.platform == "win32":
        # Windows OpenSSH uses named pipes and chokes on Unix-style
        # ControlPath; skip multiplexing there (overhead is negligible).
        return None
    import os as _os
    _ctrl_dir = _os.path.expanduser("~/.ssh/controlmasters")
    try:
        _os.makedirs(_ctrl_dir, mode=0o700, exist_ok=True)
    except OSError:
        return None
    if not _can_bind_unix_socket(_ctrl_dir):
        _multiplexing_supported = False
        return None
    return [
        "-o", "ControlMaster=auto",
        # Keep the master alive across idle gaps (uncheck/recheck, backoff
        # sleeps) so we rarely pay the handshake twice. Polite on exit:
        # lingering masters die within 2 minutes.
        "-o", "ControlPersist=120s",
        "-o", f"ControlPath={_ctrl_dir}/%C",
    ]


async def _exec_ssh(
    ssh_opts: list[str],
    host_alias: str,
    remote_cmd: list[str],
    timeout: float,
) -> tuple[bytes, bytes, int | None]:
    """Spawn ssh, feed the probe script, wait up to `timeout` seconds.

    Returns (stdout, stderr, returncode); returncode is None if the process
    could not be reaped (should not happen in practice).
    """
    import os as _os
    # On Windows, subprocess pipes default to the system code page (e.g. GBK).
    # PYTHONUTF8=1 forces UTF-8 for pipe I/O to avoid decode errors.
    env = {**_os.environ, "PYTHONUTF8": "1"}

    proc = await asyncio.create_subprocess_exec(
        "ssh",
        *ssh_opts,
        "--",
        host_alias,
        *remote_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=_PROBE_SCRIPT.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise SSHTimeoutError(
            f"SSH to {host_alias} timed out after {timeout}s "
            f"(slow link? raise timeout in ~/.config/gpuwatch/servers.yml)"
        ) from None
    finally:
        # Ensure the subprocess is killed/cleaned up on any failure
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass

    return stdout, stderr, proc.returncode


async def run_probe(
    host_alias: str,
    timeout: float = DEFAULT_TIMEOUT,
    own_user: str | None = None,
    reserved_offsets: dict[int, int] | None = None,
) -> tuple[str, float]:
    """Execute the NVML probe on a remote server via SSH.

    Args:
        host_alias: SSH host alias (from ~/.ssh/config).
        timeout: Maximum time to wait for the SSH command (seconds).
            Must cover the full first connection: TCP + SSH handshake +
            auth + remote python startup + NVML query. On relayed links
            (Tailscale DERP) a fresh handshake alone can take 8-10s.
        own_user: If set, passed to probe as --own-user for highlighting.

    Returns:
        (stdout_string, latency_ms) on success.

    Raises:
        SSHTimeoutError: if the command times out.
        SSHAuthError: if SSH authentication fails.
        RemotePythonNotFound: if python3 is missing on the remote.
        SSHCommandError: for other non-zero exits.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    global _multiplexing_supported

    # Build remote command — force UTF-8 on the remote side to avoid
    # GBK/cp1252 decode errors on Windows when reading the probe via stdin.
    import shlex
    import json as _json
    remote_cmd = ["env", "PYTHONIOENCODING=utf-8", "python3", "-"]
    if own_user:
        remote_cmd.extend(["--own-user", shlex.quote(own_user)])
    if reserved_offsets:
        remote_cmd.extend(["--reserved-offsets", shlex.quote(_json.dumps(reserved_offsets))])

    base_opts = _base_ssh_opts()
    mux_opts = _multiplexing_opts() if _multiplexing_supported is not False else None

    stdout, stderr, returncode = await _exec_ssh(
        base_opts + (mux_opts or []),
        host_alias,
        remote_cmd,
        timeout,
    )

    # If the OS refused to bind the ControlPath socket, ssh aborts with
    # 255 (after having already paid for the full handshake). Retry once
    # without multiplexing and remember the outcome so later calls skip
    # it entirely.
    if (
        returncode == 255
        and mux_opts
        and "cannot bind" in stderr.decode("utf-8", errors="replace").lower()
    ):
        _multiplexing_supported = False
        stdout, stderr, returncode = await _exec_ssh(
            base_opts, host_alias, remote_cmd, timeout
        )

    latency_ms = (loop.time() - start) * 1000

    if returncode == 255:
        stderr_str = stderr.decode("utf-8", errors="replace")
        if "Permission denied" in stderr_str:
            raise SSHAuthError(returncode, stderr_str)
        raise SSHCommandError(returncode, stderr_str)

    if returncode == 127:
        raise RemotePythonNotFound(
            returncode,
            stderr.decode("utf-8", errors="replace"),
        )

    if returncode != 0:
        raise SSHCommandError(
            returncode,
            stderr.decode("utf-8", errors="replace"),
        )

    return stdout.decode("utf-8"), latency_ms
