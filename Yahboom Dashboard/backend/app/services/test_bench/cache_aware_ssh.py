"""
SSH helpers to start/stop cache_aware_offloading.py on the Raspberry Pi.

Opens a Pi desktop terminal with:
  source ~/vit_env/bin/activate
  python3 cache_aware_offloading.py
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import PurePosixPath

import paramiko

from app.services.mqtt_service import mqtt_service
from config import (
    CACHE_SCRIPT_START_POLL_SEC,
    CACHE_SCRIPT_START_TIMEOUT_SEC,
    DEFAULT_BROKER_IP,
    PI_CACHE_AWARE_LOG,
    PI_CACHE_AWARE_SCRIPT_PATH,
    PI_CACHE_AWARE_TERMINAL_TITLE,
    PI_SSH_KEY_PATH,
    PI_SSH_PASSWORD,
    PI_SSH_USER,
    PI_TERMINAL,
    PI_VIT_VENV,
    PROBE_CACHE_TTL_SEC,
)

_probe_cache: dict = {"at": 0.0, "result": None}

PI_CACHE_LAUNCHER = "/tmp/yahboom_cache_aware.sh"


def _script_name() -> str:
    return PurePosixPath(PI_CACHE_AWARE_SCRIPT_PATH).name


def _pgrep_pattern() -> str:
    name = re.escape(_script_name() or "cache_aware_offloading.py")
    return f"[{name[0]}]{name[1:]}"


def _pkill_patterns() -> str:
    name = re.escape(_script_name())
    title = re.escape(PI_CACHE_AWARE_TERMINAL_TITLE)
    return (
        f"pkill -f 'python3?.*{name}' 2>/dev/null; "
        f"pkill -f '{name}' 2>/dev/null; "
        f"pkill -f '{title}' 2>/dev/null; "
        f"pkill -f {shlex.quote(PI_CACHE_LAUNCHER)} 2>/dev/null; "
    )


def _resolved_host() -> str:
    return mqtt_service.broker_ip or DEFAULT_BROKER_IP


def _pi_home() -> str:
    return "/root" if PI_SSH_USER == "root" else f"/home/{PI_SSH_USER}"


def _expand_pi_path(path: str, home: str) -> str:
    if path.startswith("~/"):
        return f"{home}/{path[2:]}"
    if path.startswith("~"):
        return path.replace("~", home, 1)
    return path


def _pi_paths() -> tuple[str, str, str, str]:
    home = _pi_home()
    log = _expand_pi_path(PI_CACHE_AWARE_LOG, home)
    script = _expand_pi_path(PI_CACHE_AWARE_SCRIPT_PATH, home)
    if not script.startswith("/"):
        script = f"{home}/{script}"
    workdir = str(PurePosixPath(script).parent) or home
    return home, workdir, script, log


def _ssh_client(host: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: dict = {"username": PI_SSH_USER, "timeout": 10}
    if PI_SSH_KEY_PATH:
        connect_kwargs["key_filename"] = PI_SSH_KEY_PATH
    else:
        connect_kwargs["password"] = PI_SSH_PASSWORD
    client.connect(host, **connect_kwargs)
    return client


def _log(message: str, level: str = "info") -> None:
    print(f"[cache-aware] {message}", flush=True)
    mqtt_service.log_event(level, message)


def _open_terminal(client: paramiko.SSHClient) -> None:
    home, workdir, script, log = _pi_paths()
    venv_activate = _expand_pi_path(PI_VIT_VENV, home)
    launcher = PI_CACHE_LAUNCHER
    script_name = _script_name()

    launcher_body = (
        "#!/bin/bash\n"
        f"cd {shlex.quote(workdir)}\n"
        f"truncate -s 0 {shlex.quote(log)} 2>/dev/null\n"
        f"source {shlex.quote(venv_activate)}\n"
        f"env PYTHONUNBUFFERED=1 python3 {shlex.quote(script)} 2>&1 | tee -a {shlex.quote(log)}\n"
        "exec bash\n"
    )
    write_cmd = (
        f"cat > {shlex.quote(launcher)} << 'YAHBOOM_EOF'\n"
        f"{launcher_body}"
        "YAHBOOM_EOF\n"
        f"chmod +x {shlex.quote(launcher)}"
    )
    _, stdout, stderr = client.exec_command(write_cmd, timeout=10)
    if stdout.channel.recv_exit_status() != 0:
        err = stderr.read().decode(errors="replace").strip()
        raise RuntimeError(f"Failed to write cache-aware launcher on Pi{': ' + err if err else ''}")

    xauth = home + "/.Xauthority"
    open_terminal = (
        f"DISPLAY=:0 XAUTHORITY={shlex.quote(xauth)} "
        f"nohup {PI_TERMINAL} -t {shlex.quote(PI_CACHE_AWARE_TERMINAL_TITLE)} "
        f"-e {shlex.quote(launcher)} "
        f"</dev/null >/dev/null 2>&1 & echo OPENED"
    )
    _, stdout, stderr = client.exec_command(open_terminal, timeout=10)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read()
    if exit_status != 0 or b"OPENED" not in out:
        err = stderr.read().decode(errors="replace").strip()
        raise RuntimeError(
            f"Failed to open cache-aware terminal on Pi (exit {exit_status})"
            + (f": {err}" if err else "")
        )
    _log(f"Opened Pi terminal — running {script_name} (log {log})")


def _probe_on_pi(client: paramiko.SSHClient, host: str) -> dict:
    token = re.escape(_script_name())
    probe_cmd = (
        f"( pgrep -f '[p]ython3?.*{token}' >/dev/null 2>&1 "
        f"|| pgrep -f '{token}' >/dev/null 2>&1 ) && echo RUNNING=yes || echo RUNNING=no"
    )
    _, stdout, _ = client.exec_command(probe_cmd, timeout=8)
    raw = stdout.read().decode(errors="replace")
    running = "RUNNING=yes" in raw
    return {"running": running, "host": host}


def _invalidate_probe_cache() -> None:
    _probe_cache["at"] = 0.0
    _probe_cache["result"] = None


def probe_cache_aware_script(*, force: bool = False) -> dict:
    host = _resolved_host()
    now = time.monotonic()
    if (
        not force
        and _probe_cache["result"] is not None
        and (now - _probe_cache["at"]) < PROBE_CACHE_TTL_SEC
    ):
        return _probe_cache["result"]

    result: dict = {"running": False, "host": host}
    try:
        client = _ssh_client(host)
        result = _probe_on_pi(client, host)
        client.close()
    except Exception:
        pass
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = result
    return result


def _wait_until_running(client: paramiko.SSHClient, host: str) -> bool:
    deadline = time.monotonic() + CACHE_SCRIPT_START_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if _probe_on_pi(client, host).get("running"):
            return True
        time.sleep(CACHE_SCRIPT_START_POLL_SEC)
    return False


def start_cache_aware_script(*, wait: bool = True) -> dict:
    """SSH into the Pi, open terminal running cache_aware_offloading.py."""
    host = _resolved_host()
    script_name = _script_name()
    _invalidate_probe_cache()
    client = _ssh_client(host)

    existing = _probe_on_pi(client, host)
    if existing.get("running"):
        client.close()
        _probe_cache["at"] = time.monotonic()
        _probe_cache["result"] = existing
        _log(f"Cache-aware script already running — {script_name}")
        return existing

    _, stdout, _ = client.exec_command(f"{_pkill_patterns()}sleep 0.5; true", timeout=10)
    stdout.channel.recv_exit_status()
    _open_terminal(client)
    time.sleep(1.0)

    running = _wait_until_running(client, host) if wait else _probe_on_pi(client, host).get("running", False)
    probe = {"running": bool(running), "host": host}
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe

    if running:
        _log(f"Cache-aware script started — {script_name} running on Pi")
    else:
        _log(
            f"Cache-aware script launch requested — {script_name} not detected within "
            f"{CACHE_SCRIPT_START_TIMEOUT_SEC:.0f}s",
            level="warning",
        )
    return probe


def stop_cache_aware_script() -> dict:
    """SSH into the Pi, kill cache_aware_offloading.py and close its terminal."""
    host = _resolved_host()
    script_name = _script_name()
    _invalidate_probe_cache()
    client = _ssh_client(host)
    _, stdout, _ = client.exec_command(f"{_pkill_patterns()}sleep 0.3; true", timeout=10)
    stdout.channel.recv_exit_status()
    probe = _probe_on_pi(client, host)
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    _log(f"Cache-aware script stopped — {script_name} killed on Pi")
    return probe
