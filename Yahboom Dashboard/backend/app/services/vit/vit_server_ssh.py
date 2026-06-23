"""
SSH helpers to start/stop robot_sender.py on the Raspberry Pi (VIT encoder).

Mirrors the video stream SSH pattern in stream_routes.py but uses the VIT venv
and script — the video webrtc_server paths are not touched.
"""

from __future__ import annotations

import re
import shlex
import threading
import time
from pathlib import PurePosixPath

import paramiko

from app.services.mqtt_service import mqtt_service
from config import (
    DEFAULT_BROKER_IP,
    PI_SSH_USER,
    PI_SSH_PASSWORD,
    PI_SSH_KEY_PATH,
    PI_TERMINAL,
    PI_VIT_VENV,
    PI_VIT_SERVER_PATH,
    PI_VIT_SERVER_LOG,
    PROBE_CACHE_TTL_SEC,
    VIT_PROBE_INTERVAL_SEC,
)

_probe_cache: dict = {"at": 0.0, "result": None}

PI_VIT_LAUNCHER = "/tmp/yahboom_vit_sender.sh"
PI_VIT_TERMINAL_TITLE = "VIT Sender"


def _vit_script_name() -> str:
    """Basename of the Pi-side encoder script (e.g. robot_sender.py)."""
    return PurePosixPath(PI_VIT_SERVER_PATH).name


def _vit_script_pgrep_pattern() -> str:
    """pgrep -f pattern for the encoder process (bracket trick avoids self-match)."""
    name = re.escape(_vit_script_name() or "robot_sender.py")
    return f"[{name[0]}]{name[1:]}"


def _vit_probe_script_names() -> list[str]:
    """Only the dedicated VIT encoder (robot_sender.py) — not webrtc_server.py."""
    name = PurePosixPath(PI_VIT_SERVER_PATH).name
    return [name] if name else ["robot_sender.py"]


def _vit_pkill_patterns() -> str:
    """Shell fragments to stop the configured encoder script."""
    name = re.escape(_vit_script_name())
    return (
        f"pkill -f 'python3?.*{name}' 2>/dev/null; "
        f"pkill -f '{name}' 2>/dev/null; "
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


def _pi_vit_paths() -> tuple[str, str, str, str]:
    """Return (home, workdir, script, log) for the VIT encoder on the Pi."""
    home = _pi_home()
    log = _expand_pi_path(PI_VIT_SERVER_LOG, home)
    script = _expand_pi_path(PI_VIT_SERVER_PATH, home)
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


def _vit_debug(message: str, level: str = "info") -> None:
    print(f"[vit-server] {message}", flush=True)
    mqtt_service.log_event(level, message)


def _pi_terminal_start_vit(client: paramiko.SSHClient) -> None:
    """
    Open a terminal on the Pi desktop and run:
      source ~/vit_env/bin/activate
      python3 robot_sender.py
    """
    home, workdir, script, log = _pi_vit_paths()
    venv_activate = _expand_pi_path(PI_VIT_VENV, home)
    launcher = PI_VIT_LAUNCHER
    script_name = _vit_script_name()

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
        raise RuntimeError(f"Failed to write VIT launcher on Pi{': ' + err if err else ''}")

    xauth = home + "/.Xauthority"
    open_terminal = (
        f"DISPLAY=:0 XAUTHORITY={shlex.quote(xauth)} "
        f"nohup {PI_TERMINAL} -t {shlex.quote(PI_VIT_TERMINAL_TITLE)} "
        f"-e {shlex.quote(launcher)} "
        f"</dev/null >/dev/null 2>&1 & echo OPENED"
    )
    _, stdout, stderr = client.exec_command(open_terminal, timeout=10)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read()
    if exit_status != 0 or b"OPENED" not in out:
        err = stderr.read().decode(errors="replace").strip()
        raise RuntimeError(
            f"Failed to open VIT terminal on Pi (exit {exit_status})"
            + (f": {err}" if err else "")
        )
    _vit_debug(f"Opened Pi terminal — running {script_name} (log {log})")


def _probe_on_pi(client: paramiko.SSHClient, host: str) -> dict:
    checks = []
    for script_name in _vit_probe_script_names():
        token = re.escape(script_name)
        checks.append(
            f"pgrep -f '[p]ython3?.*{token}' >/dev/null 2>&1 "
            f"|| pgrep -f '{token}' >/dev/null 2>&1"
        )
    probe_cmd = (
        f"( {' || '.join(checks)} ) && echo RUNNING=yes || echo RUNNING=no"
    )
    _, stdout, _ = client.exec_command(probe_cmd, timeout=8)
    raw = stdout.read().decode(errors="replace")
    running = "RUNNING=yes" in raw
    return {"running": running, "host": host}


def apply_vit_probe(probe: dict) -> None:
    """Update vit_server_running; clear live readout when the Pi process dies."""
    from app.services.vit.vit_service import vit_service

    was_running = vit_service.vit_server_running
    running = bool(probe.get("running"))
    vit_service.vit_server_running = running
    if not running:
        vit_service.stop_embedding_size_requests()
    if was_running and not running:
        with vit_service._lock:
            vit_service._latest = None
            vit_service._last_embedding_at = None
            vit_service._last_decode_at = None
            vit_service._last_status_at = None


def _background_vit_probe_loop() -> None:
    while True:
        try:
            probe = probe_vit_server(force=True)
            apply_vit_probe(probe)
        except Exception:
            pass
        time.sleep(VIT_PROBE_INTERVAL_SEC)


def start_background_vit_probe() -> None:
    """Daemon thread — keeps vit_server_running in sync with the Pi process."""
    thread = threading.Thread(
        target=_background_vit_probe_loop, daemon=True, name="vit-probe"
    )
    thread.start()


def probe_vit_server(*, force: bool = False) -> dict:
    """SSH pgrep for the configured VIT encoder script; cached to limit load."""
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


def _invalidate_probe_cache() -> None:
    _probe_cache["at"] = 0.0
    _probe_cache["result"] = None


def start_vit_server() -> dict:
    """SSH into the Pi and launch the VIT encoder in a desktop terminal."""
    host = _resolved_host()
    script_name = _vit_script_name()
    _invalidate_probe_cache()
    client = _ssh_client(host)
    _, stdout, _ = client.exec_command(
        f"{_vit_pkill_patterns()}sleep 0.5; true",
        timeout=10,
    )
    stdout.channel.recv_exit_status()
    _pi_terminal_start_vit(client)
    time.sleep(1.0)
    probe = _probe_on_pi(client, host)
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    apply_vit_probe(probe)
    if probe["running"]:
        _vit_debug(f"VIT encoder started — {script_name} running on Pi")
    else:
        _vit_debug(
            f"VIT encoder launch requested — {script_name} not detected yet (may still be starting)",
            level="warning",
        )
    return probe


def stop_vit_server() -> dict:
    """SSH into the Pi and kill the VIT encoder process."""
    host = _resolved_host()
    script_name = _vit_script_name()
    _invalidate_probe_cache()
    client = _ssh_client(host)
    _, stdout, _ = client.exec_command(
        f"{_vit_pkill_patterns()}"
        "pkill -f 'yahboom_vit_sender' 2>/dev/null; "
        f"pkill -f {shlex.quote(PI_VIT_TERMINAL_TITLE)} 2>/dev/null; "
        "sleep 0.3; true",
        timeout=10,
    )
    stdout.channel.recv_exit_status()
    probe = _probe_on_pi(client, host)
    client.close()
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    apply_vit_probe(probe)
    _vit_debug(f"VIT encoder stopped — {script_name} killed on Pi")
    return probe
