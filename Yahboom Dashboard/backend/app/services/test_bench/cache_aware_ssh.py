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
from app.services.pi_remote_launch import (
    gui_session_available,
    start_lxterminal,
    write_launcher,
)
from config import (
    CACHE_SCRIPT_EMBEDDING_READY_SNIPPET,
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
PI_CACHE_HEADLESS_LAUNCHER = "/tmp/yahboom_cache_aware_headless.sh"
PI_CACHE_LOG_VIEWER = "/tmp/yahboom_cache_aware_viewer.sh"


def _probe_meta(
    host: str,
    *,
    running: bool,
    launch_mode: str | None = None,
    detection_ready: bool = False,
) -> dict:
    _, _, _, log = _pi_paths()
    return {
        "running": running,
        "detection_ready": detection_ready,
        "host": host,
        "log_path": log,
        **({"launch_mode": launch_mode} if launch_mode else {}),
    }


def _script_name() -> str:
    return PurePosixPath(PI_CACHE_AWARE_SCRIPT_PATH).name


def _pgrep_pattern() -> str:
    name = re.escape(_script_name() or "cache_aware_offloading.py")
    return f"[{name[0]}]{name[1:]}"


def _terminal_bin() -> str:
    return PI_TERMINAL.split()[0]


def _pkill_patterns() -> str:
    name = re.escape(_script_name())
    launcher = re.escape(PI_CACHE_LAUNCHER)
    return (
        f"pkill -f 'python3?.*{name}' 2>/dev/null; "
        f"pkill -f '{name}' 2>/dev/null; "
        f"pkill -f '{launcher}' 2>/dev/null; "
        f"pkill -f '{re.escape(PI_CACHE_HEADLESS_LAUNCHER)}' 2>/dev/null; "
        f"pkill -f '{re.escape(PI_CACHE_LOG_VIEWER)}' 2>/dev/null; "
    )


def _close_terminal_cmd(*, sleep_sec: float = 0.3) -> str:
    """Kill cache-aware processes and close the Pi desktop terminal window."""
    term = re.escape(_terminal_bin())
    launcher = re.escape(PI_CACHE_LAUNCHER)
    title = re.escape(PI_CACHE_AWARE_TERMINAL_TITLE)
    home = _pi_home()
    xauth = shlex.quote(f"{home}/.Xauthority")
    title_q = shlex.quote(PI_CACHE_AWARE_TERMINAL_TITLE)
    return (
        f"{_pkill_patterns()}"
        # lxterminal often omits -t from /proc/cmdline — match -e launcher instead.
        f"pkill -f '{term}.*{launcher}' 2>/dev/null; "
        f"pkill -f '{term} -e {shlex.quote(PI_CACHE_LAUNCHER)}' 2>/dev/null; "
        f"pkill -f '{term}.*{title}' 2>/dev/null; "
        f"pkill -f '{title}' 2>/dev/null; "
        f"DISPLAY=:0 XAUTHORITY={xauth} wmctrl -c {title_q} 2>/dev/null; "
        f"sleep {sleep_sec}; true"
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
    if level not in ("warning", "error"):
        return
    print(f"[cache-aware] {message}", flush=True)
    mqtt_service.log_event(level, message, tag="cache-aware")


def _launcher_body() -> tuple[str, str]:
    home, workdir, script, log = _pi_paths()
    venv_activate = _expand_pi_path(PI_VIT_VENV, home)
    body = (
        "#!/bin/bash\n"
        f"cd {shlex.quote(workdir)}\n"
        f"truncate -s 0 {shlex.quote(log)} 2>/dev/null\n"
        f"source {shlex.quote(venv_activate)}\n"
        f"env PYTHONUNBUFFERED=1 stdbuf -oL -eL python3 {shlex.quote(script)} 2>&1 | tee -a {shlex.quote(log)}\n"
        "exec bash\n"
    )
    return home, body


def _cache_terminal_open(client: paramiko.SSHClient) -> bool:
    """True if lxterminal is already open for cache-aware script or log viewer."""
    term = re.escape(_terminal_bin())
    for pattern in (re.escape(PI_CACHE_LAUNCHER), re.escape(PI_CACHE_LOG_VIEWER)):
        cmd = f"pgrep -f '{term}.*{pattern}' >/dev/null 2>&1 && echo YES || echo NO"
        _, stdout, _ = client.exec_command(cmd, timeout=5)
        if b"YES" in stdout.read():
            return True
    title = re.escape(PI_CACHE_AWARE_TERMINAL_TITLE)
    _, stdout, _ = client.exec_command(
        f"pgrep -f '{term}.*{title}' >/dev/null 2>&1 && echo YES || echo NO",
        timeout=5,
    )
    return b"YES" in stdout.read()


def _open_pi_log_terminal(client: paramiko.SSHClient) -> bool:
    """Open one lxterminal tailing the script log — does not start or restart the script."""
    if _cache_terminal_open(client):
        return False
    home, _, _, log = _pi_paths()
    if not gui_session_available(client, home):
        return False
    viewer_body = (
        "#!/bin/bash\n"
        f"touch {shlex.quote(log)}\n"
        f"tail -n 80 -f {shlex.quote(log)}\n"
        "exec bash\n"
    )
    write_launcher(client, PI_CACHE_LOG_VIEWER, viewer_body)
    start_lxterminal(
        client,
        home=home,
        title=PI_CACHE_AWARE_TERMINAL_TITLE,
        launcher_path=PI_CACHE_LOG_VIEWER,
        label="cache_aware_log",
    )
    return True


def _start_in_pi_terminal(client: paramiko.SSHClient) -> str:
    """Open lxterminal on the Pi running cache_aware_offloading.py. Returns session id."""
    if _cache_terminal_open(client):
        return "existing"
    home, body = _launcher_body()
    if not gui_session_available(client, home):
        raise RuntimeError(
            "Pi desktop session not available (no Wayland/X11). "
            "Log in via RealVNC or HDMI so lxterminal can open for cache_aware_offloading.py."
        )
    write_launcher(client, PI_CACHE_LAUNCHER, body)
    used = start_lxterminal(
        client,
        home=home,
        title=PI_CACHE_AWARE_TERMINAL_TITLE,
        launcher_path=PI_CACHE_LAUNCHER,
        label="cache_aware_offloading",
    )
    return used


def _script_is_running(client: paramiko.SSHClient) -> bool:
    """True when cache_aware_offloading.py is running on the Pi."""
    name = re.escape(_script_name())
    probe_cmd = (
        f"( pgrep -af '{name}' 2>/dev/null | grep -v pgrep | grep -q . && echo RUNNING=yes ) "
        f"|| ( pgrep -f '[p]ython3?.*{name}' >/dev/null 2>&1 && echo RUNNING=yes ) "
        f"|| ( pgrep -f '{name}' >/dev/null 2>&1 && echo RUNNING=yes ) "
        f"|| echo RUNNING=no"
    )
    _, stdout, _ = client.exec_command(probe_cmd, timeout=8)
    return b"RUNNING=yes" in stdout.read()


def _mqtt_embedding_ready() -> bool:
    return bool(getattr(mqtt_service, "cache_aware_embedding_ready", False))


def _embedding_detection_ready(client: paramiko.SSHClient, *, running: bool) -> bool:
    if not running:
        return False
    if _log_has_embedding_ready(client):
        return True
    return _mqtt_embedding_ready()


def _log_has_embedding_ready(client: paramiko.SSHClient) -> bool:
    """True when the Pi log file contains the bottle text-embedding ready line."""
    _, _, _, log = _pi_paths()
    log_q = shlex.quote(log)
    snippet = shlex.quote(CACHE_SCRIPT_EMBEDDING_READY_SNIPPET)
    primary = (
        f"test -f {log_q} && grep -Fq {snippet} {log_q} 2>/dev/null "
        f"&& echo READY=yes || echo READY=no"
    )
    _, stdout, _ = client.exec_command(primary, timeout=8)
    if b"READY=yes" in stdout.read():
        return True
    # Fallback: line may include a different @ N dims suffix than the configured snippet.
    fallback = (
        f"test -f {log_q} && "
        f"tail -n 400 {log_q} 2>/dev/null | "
        f"grep -a -F '[DETECT] Text embedding ready' | grep -a -F 'water bottle' | "
        f"grep -q . && echo READY=yes || echo READY=no"
    )
    _, stdout2, _ = client.exec_command(fallback, timeout=8)
    return b"READY=yes" in stdout2.read()


def _probe_on_pi(client: paramiko.SSHClient, host: str) -> dict:
    running = _script_is_running(client)
    detection_ready = _embedding_detection_ready(client, running=running)
    return _probe_meta(host, running=running, detection_ready=detection_ready)


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

    result: dict = _probe_meta(host, running=False)
    try:
        client = _ssh_client(host)
        result = _probe_on_pi(client, host)
        cached_mode = (_probe_cache.get("result") or {}).get("launch_mode")
        if result.get("running") and cached_mode:
            result["launch_mode"] = cached_mode
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
    """SSH into the Pi, open terminal running cache_aware_offloading.py (one terminal per call)."""
    host = _resolved_host()
    script_name = _script_name()
    _invalidate_probe_cache()
    client = _ssh_client(host)
    home = _pi_home()

    existing = _probe_on_pi(client, host)
    if existing.get("running"):
        if gui_session_available(client, home) and not _cache_terminal_open(client):
            _open_pi_log_terminal(client)
        if _cache_terminal_open(client):
            existing["launch_mode"] = "terminal"
        client.close()
        _probe_cache["at"] = time.monotonic()
        _probe_cache["result"] = existing
        return existing

    if _cache_terminal_open(client):
        running = (
            _wait_until_running(client, host)
            if wait
            else _script_is_running(client)
        )
        detection_ready = _embedding_detection_ready(client, running=bool(running))
        probe = _probe_meta(
            host,
            running=bool(running),
            launch_mode="terminal",
            detection_ready=detection_ready,
        )
        client.close()
        _probe_cache["at"] = time.monotonic()
        _probe_cache["result"] = probe
        if not running:
            _log(
                f"Cache-aware terminal open — waiting for {script_name}",
                level="warning",
            )
        return probe

    _, stdout, _ = client.exec_command(_close_terminal_cmd(sleep_sec=0.5), timeout=10)
    stdout.channel.recv_exit_status()
    mqtt_service.clear_cache_aware_ready()
    _start_in_pi_terminal(client)
    launch_mode = "terminal"
    time.sleep(1.0)

    running = _wait_until_running(client, host) if wait else _script_is_running(client)
    detection_ready = _embedding_detection_ready(client, running=bool(running))
    probe = _probe_meta(
        host,
        running=bool(running),
        launch_mode=launch_mode,
        detection_ready=detection_ready,
    )
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    if not running:
        _log(
            f"Cache-aware script not detected within {CACHE_SCRIPT_START_TIMEOUT_SEC:.0f}s",
            level="warning",
        )
    return probe


def stop_cache_aware_script() -> dict:
    """SSH into the Pi, kill cache_aware_offloading.py and close its terminal."""
    host = _resolved_host()
    _invalidate_probe_cache()
    mqtt_service.clear_cache_aware_ready()
    client = _ssh_client(host)
    _, stdout, _ = client.exec_command(_close_terminal_cmd(), timeout=10)
    stdout.channel.recv_exit_status()
    probe = _probe_on_pi(client, host)
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    return probe
