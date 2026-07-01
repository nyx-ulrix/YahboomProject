"""
Shared Pi SSH launch helpers — lxterminal when a GUI session is available, headless nohup otherwise.
"""

from __future__ import annotations

import re
import shlex

import paramiko

from config import PI_DISPLAY, PI_TERMINAL


def _xauth_path(home: str) -> str:
    return f"{home}/.Xauthority"


def _pi_uid(client: paramiko.SSHClient) -> str:
    _, stdout, _ = client.exec_command("id -u", timeout=5)
    uid = stdout.read().decode(errors="replace").strip()
    return uid or "1000"


def _resolve_wayland(client: paramiko.SSHClient) -> tuple[str | None, str]:
    """Return (WAYLAND_DISPLAY name, XDG_RUNTIME_DIR) when a Wayland socket exists."""
    uid = _pi_uid(client)
    runtime = f"/run/user/{uid}"
    cmd = f"ls {shlex.quote(runtime)}/wayland-* 2>/dev/null | grep -v '\\.lock' | head -1"
    _, stdout, _ = client.exec_command(cmd, timeout=8)
    path = stdout.read().decode(errors="replace").strip()
    if not path:
        return None, runtime
    name = path.rsplit("/", 1)[-1]
    if not name.startswith("wayland-"):
        return None, runtime
    return name, runtime


def _display_usable(client: paramiko.SSHClient, home: str, display: str) -> bool:
    xauth = shlex.quote(_xauth_path(home))
    disp = shlex.quote(display)
    cmd = (
        f"DISPLAY={disp} XAUTHORITY={xauth} xdpyinfo >/dev/null 2>&1 && echo DISPLAY_OK "
        f"|| echo DISPLAY_FAIL"
    )
    _, stdout, _ = client.exec_command(cmd, timeout=8)
    return b"DISPLAY_OK" in stdout.read()


def _discover_x11_displays(client: paramiko.SSHClient) -> list[str]:
    """List X11 displays from /tmp/.X11-unix (RealVNC may expose many)."""
    _, stdout, _ = client.exec_command(
        "ls /tmp/.X11-unix/ 2>/dev/null | sed 's/^X/:/' | sort -t: -k1.2 -n",
        timeout=8,
    )
    raw = stdout.read().decode(errors="replace").strip()
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip().startswith(":")]


def resolve_pi_display(client: paramiko.SSHClient, home: str) -> tuple[str | None, str]:
    """
    Find a working X11 DISPLAY on the Pi.
    Returns (display, xauth_path) or (None, xauth_path).
    """
    xauth = _xauth_path(home)
    candidates: list[str] = []
    if PI_DISPLAY:
        candidates.append(PI_DISPLAY)
    for candidate in (":0", ":1", ":2"):
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in _discover_x11_displays(client):
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if _display_usable(client, home, candidate):
            return candidate, xauth
    return None, xauth


def gui_session_available(client: paramiko.SSHClient, home: str) -> bool:
    """True when Wayland or X11 GUI is reachable for the SSH user."""
    wayland, _ = _resolve_wayland(client)
    if wayland:
        return True
    display, _ = resolve_pi_display(client, home)
    return display is not None


def display_available(client: paramiko.SSHClient, home: str) -> bool:
    """Alias — True when lxterminal can be launched on the Pi desktop."""
    return gui_session_available(client, home)


def gui_env_prefix(client: paramiko.SSHClient, home: str) -> tuple[str, str]:
    """
    Shell env prefix for GUI apps over SSH.
    Returns (prefix, session_label) e.g. ('WAYLAND_DISPLAY=wayland-1 ...', 'wayland-1').
    """
    wayland, runtime = _resolve_wayland(client)
    if wayland:
        prefix = (
            f"XDG_RUNTIME_DIR={shlex.quote(runtime)} "
            f"WAYLAND_DISPLAY={shlex.quote(wayland)} "
            f"DBUS_SESSION_BUS_ADDRESS=unix:path={shlex.quote(runtime)}/bus "
        )
        return prefix, wayland

    display, xauth = resolve_pi_display(client, home)
    if display:
        prefix = (
            f"DISPLAY={shlex.quote(display)} "
            f"XAUTHORITY={shlex.quote(xauth)} "
        )
        return prefix, display

    return "", ""


def write_launcher(client: paramiko.SSHClient, launcher_path: str, body: str) -> None:
    write_cmd = (
        f"cat > {shlex.quote(launcher_path)} << 'YAHBOOM_EOF'\n"
        f"{body}"
        "YAHBOOM_EOF\n"
        f"chmod +x {shlex.quote(launcher_path)}"
    )
    _, stdout, stderr = client.exec_command(write_cmd, timeout=10)
    if stdout.channel.recv_exit_status() != 0:
        err = stderr.read().decode(errors="replace").strip()
        raise RuntimeError(f"Failed to write launcher on Pi{': ' + err if err else ''}")


def start_headless(
    client: paramiko.SSHClient,
    *,
    launcher_path: str,
    log_path: str,
    label: str,
) -> None:
    """Run launcher via nohup — no desktop terminal required."""
    start_cmd = (
        f"truncate -s 0 {shlex.quote(log_path)} 2>/dev/null; "
        f"nohup {shlex.quote(launcher_path)} >> {shlex.quote(log_path)} 2>&1 & echo STARTED"
    )
    _, stdout, stderr = client.exec_command(start_cmd, timeout=10)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if exit_status != 0 or "STARTED" not in out:
        raise RuntimeError(
            f"Failed to start {label} headless on Pi (exit {exit_status})"
            + (f": {err or out}" if (err or out) else "")
        )


def lxterminal_stderr_path(title: str) -> str:
    return f"/tmp/{title.replace(' ', '_')}_lx.out"


def start_lxterminal(
    client: paramiko.SSHClient,
    *,
    home: str,
    title: str,
    launcher_path: str,
    label: str,
    display: str | None = None,
) -> str:
    """Open lxterminal on the Pi desktop. Returns session id (wayland or DISPLAY)."""
    env_prefix, session = gui_env_prefix(client, home)
    if not env_prefix:
        raise RuntimeError(
            "Pi desktop session not available — log in via RealVNC or HDMI, "
            "or set PI_DISPLAY in backend .env for X11 sessions"
        )
    if display and "WAYLAND" not in env_prefix:
        xauth = shlex.quote(_xauth_path(home))
        env_prefix = f"DISPLAY={shlex.quote(display)} XAUTHORITY={xauth} "
        session = display

    err_file = lxterminal_stderr_path(title)
    open_terminal = (
        f"{env_prefix}"
        f"nohup {PI_TERMINAL} -t {shlex.quote(title)} "
        f"-e {shlex.quote(launcher_path)} "
        f"</dev/null >{shlex.quote(err_file)} 2>&1 & echo OPENED"
    )
    _, stdout, stderr = client.exec_command(open_terminal, timeout=10)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read()
    if exit_status != 0 or b"OPENED" not in out:
        err = stderr.read().decode(errors="replace").strip()
        extra = ""
        try:
            _, tail_out, _ = client.exec_command(f"tail -n 8 {shlex.quote(err_file)} 2>/dev/null", timeout=5)
            tail = tail_out.read().decode(errors="replace").strip()
            if tail:
                extra = f" — {tail}"
        except Exception:
            pass
        raise RuntimeError(
            f"Failed to open {label} terminal on Pi ({session}) (exit {exit_status})"
            + (f": {err}{extra}" if err or extra else "")
        )
    return session


def close_lxterminal(
    client: paramiko.SSHClient,
    *,
    home: str,
    title: str,
    launcher_paths: list[str],
) -> None:
    """Kill lxterminal windows (Wayland or X11) and related launcher processes."""
    term = re.escape(PI_TERMINAL.split()[0])
    title_esc = re.escape(title)
    title_q = shlex.quote(title)

    parts: list[str] = []
    for launcher_path in launcher_paths:
        launcher_esc = re.escape(launcher_path)
        launcher_q = shlex.quote(launcher_path)
        parts.append(f"pkill -f '{term}.*{launcher_esc}' 2>/dev/null")
        parts.append(f"pkill -f '{term} -e {launcher_q}' 2>/dev/null")
    parts.append(f"pkill -f '{term}.*{title_esc}' 2>/dev/null")
    parts.append(f"pkill -f '{title_esc}' 2>/dev/null")

    env_prefix, _session = gui_env_prefix(client, home)
    if env_prefix:
        parts.append(f"{env_prefix}pkill -f '{term}.*{title_esc}' 2>/dev/null")
        if "WAYLAND" not in env_prefix:
            parts.append(f"{env_prefix}wmctrl -c {title_q} 2>/dev/null")

    cmd = "; ".join(parts) + "; sleep 0.3; true"
    _, stdout, _ = client.exec_command(cmd, timeout=15)
    stdout.channel.recv_exit_status()


def start_interactive_or_headless(
    client: paramiko.SSHClient,
    *,
    home: str,
    title: str,
    launcher_path: str,
    headless_launcher_path: str,
    terminal_launcher_body: str,
    headless_launcher_body: str,
    log_path: str,
    label: str,
) -> str:
    """
    Start a Pi process in lxterminal when a GUI session is up, otherwise headless.
    Returns 'terminal' or 'headless'.
    """
    write_launcher(client, launcher_path, terminal_launcher_body)
    if gui_session_available(client, home):
        start_lxterminal(client, home=home, title=title, launcher_path=launcher_path, label=label)
        return "terminal"
    write_launcher(client, headless_launcher_path, headless_launcher_body)
    start_headless(client, launcher_path=headless_launcher_path, log_path=log_path, label=label)
    return "headless"
