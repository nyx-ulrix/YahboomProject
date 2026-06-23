"""
Video stream control routes — start/stop webrtc_server.py on the Raspberry Pi.

Upstream URL comes from [DASHBOARD LINK] in the Pi log (WebRTC on :8080).
MJPEG relay is optional (VIDEO_USE_MJPEG_RELAY); default is direct WebRTC page URL.
"""

import json
import re
import shlex
import threading
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath

from flask import Blueprint, Response, jsonify, request, stream_with_context
import paramiko
from app.services.mqtt_service import mqtt_service
from app.services.video_relay import video_relay
from config import (
    DEFAULT_BROKER_IP,
    PI_SSH_USER, PI_SSH_PASSWORD, PI_SSH_KEY_PATH,
    PI_VIDEO_VENV, PI_VIDEO_SERVER_PATH, PI_TERMINAL,
    PI_VIDEO_SERVER_LOG, VIDEO_SERVER_PORT, VIDEO_FEED_PATH,
    VIDEO_USE_MJPEG_RELAY, PUBLIC_VIDEO_FEED_PATH,
    VIDEO_LINK_WAIT_SEC, PROBE_CACHE_TTL_SEC, VIDEO_PROBE_INTERVAL_SEC,
)

_probe_cache: dict = {"at": 0.0, "result": None}
# After an explicit stop, ignore probe hysteresis that would flip running back on.
_stream_force_off_until: float = 0.0
# Keep server_present true from terminal open until explicit stop.
_server_present_locked: bool = False

stream_bp = Blueprint("stream", __name__, url_prefix="/api")

# webrtc_server.py prints e.g. [DASHBOARD LINK] http://10.x.x.x:8080
# Older scripts used "Dashboard: http://..." without brackets.
DASHBOARD_LINK_RE = re.compile(
    r"(?:\[DASHBOARD\s+LINK\]|Dashboard)\s*:?\s*(https?://[^\s\]\>,\"']+)",
    re.IGNORECASE,
)


def _resolved_host() -> str:
    """Return the Pi IP — from the active MQTT connection or the .env default."""
    return mqtt_service.broker_ip or DEFAULT_BROKER_IP


def _pi_home() -> str:
    """Absolute home directory for the SSH Pi user."""
    return "/root" if PI_SSH_USER == "root" else f"/home/{PI_SSH_USER}"


def _expand_pi_path(path: str, home: str) -> str:
    """Expand ~ on Pi-side paths used over SSH."""
    if path.startswith("~/"):
        return f"{home}/{path[2:]}"
    if path.startswith("~"):
        return path.replace("~", home, 1)
    return path


def _pi_webrtc_paths() -> tuple[str, str, str, str]:
    """Return (home, workdir, script, log) for webrtc_server.py on the Pi."""
    home = _pi_home()
    log = _expand_pi_path(PI_VIDEO_SERVER_LOG, home)
    script = _expand_pi_path(PI_VIDEO_SERVER_PATH, home)
    if not script.startswith("/"):
        script = f"{home}/{script}"
    workdir = str(PurePosixPath(script).parent) or home
    return home, workdir, script, log


PI_WEBRTC_LAUNCHER = "/tmp/yahboom_webrtc.sh"
PI_WEBRTC_TERMINAL_TITLE = "WebRTC Server"


def _pi_terminal_start_webrtc(host: str) -> None:
    """
    Open a terminal window on the Pi desktop and run:
      source ~/vit_env/bin/activate
      python3 webrtc_server.py

    Uses two short SSH sessions (write launcher, open terminal) so Windows
    Paramiko does not EINVAL on a long-lived channel.
    """
    home, workdir, script, log = _pi_webrtc_paths()
    venv_activate = _expand_pi_path(PI_VIDEO_VENV, home)
    launcher = PI_WEBRTC_LAUNCHER

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

    def write_launcher(client: paramiko.SSHClient) -> None:
        code, _, err = _ssh_exec(client, write_cmd, timeout=10)
        if code != 0:
            err_text = err.decode(errors="replace").strip()
            raise RuntimeError(
                f"Failed to write launcher script on Pi{': ' + err_text if err_text else ''}"
            )

    xauth = home + "/.Xauthority"
    open_terminal_cmd = (
        f"DISPLAY=:0 XAUTHORITY={shlex.quote(xauth)} "
        f"nohup {PI_TERMINAL} -t {shlex.quote(PI_WEBRTC_TERMINAL_TITLE)} -e {shlex.quote(launcher)} "
        f"</dev/null >/dev/null 2>&1 & echo OPENED"
    )

    def open_terminal(client: paramiko.SSHClient) -> None:
        code, out, err = _ssh_exec(client, open_terminal_cmd, timeout=10)
        if code != 0 or b"OPENED" not in out:
            err_text = err.decode(errors="replace").strip()
            raise RuntimeError(
                f"Failed to open terminal on Pi (exit {code})"
                + (f": {err_text}" if err_text else "")
            )

    _with_ssh(host, write_launcher)
    _with_ssh(host, open_terminal)
    _video_debug(f"Opened Pi terminal — running webrtc_server.py (log {log})")


def _ssh_client(host: str) -> paramiko.SSHClient:
    """Return a connected SSHClient to the Pi."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: dict = {"username": PI_SSH_USER, "timeout": 10}
    if PI_SSH_KEY_PATH:
        connect_kwargs["key_filename"] = PI_SSH_KEY_PATH
    else:
        connect_kwargs["password"] = PI_SSH_PASSWORD
    client.connect(host, **connect_kwargs)
    return client


def _ssh_exec(client: paramiko.SSHClient, command: str, *, timeout: int = 10) -> tuple[int, bytes, bytes]:
    """
    Run a remote command and drain stdout/stderr.

    Paramiko on Windows can raise OSError(22) on read/recv_exit_status/close —
    never let that abort the caller when the remote command already ran.
    """
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
    except OSError as exc:
        return -1, b"", str(exc).encode(errors="replace")

    out = b""
    err = b""
    try:
        out = stdout.read()
    except OSError:
        pass
    try:
        err = stderr.read()
    except OSError:
        pass
    try:
        code = stdout.channel.recv_exit_status()
    except OSError:
        code = -1
    return code, out, err


def _ssh_close(client: paramiko.SSHClient | None) -> None:
    if client is None:
        return
    try:
        transport = client.get_transport()
        if transport is not None:
            try:
                transport.close()
            except OSError:
                pass
    except Exception:
        pass
    try:
        client.close()
    except OSError:
        pass


def _with_ssh(host: str, fn):
    """Connect, run *fn(client)*, return its result, and always close."""
    client: paramiko.SSHClient | None = None
    try:
        client = _ssh_client(host)
        return fn(client)
    finally:
        _ssh_close(client)


def _normalize_feed_url(url: str | None) -> str | None:
    """Use URL as printed; append VIDEO_FEED_PATH only for legacy MJPEG mode."""
    if not url:
        return None
    base = url.rstrip("/").rstrip(".,;)")
    if not VIDEO_FEED_PATH:
        return base
    suffix = VIDEO_FEED_PATH if VIDEO_FEED_PATH.startswith("/") else f"/{VIDEO_FEED_PATH}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def _parse_dashboard_link(text: str) -> str | None:
    """Return the last http(s) URL after [DASHBOARD LINK] in *text*, or None."""
    matches = DASHBOARD_LINK_RE.findall(text)
    if not matches:
        return None
    return _normalize_feed_url(matches[-1].rstrip(".,;)"))


def _read_pi_log_tail(client: paramiko.SSHClient, lines: int = 80) -> str:
    _, _, _, log = _pi_webrtc_paths()
    _, out, _ = _ssh_exec(
        client,
        f"tail -n {lines} {shlex.quote(log)} 2>/dev/null || true",
        timeout=8,
    )
    return out.decode(errors="replace")


def _camera_error_from_log(text: str) -> str | None:
    """Return a short camera error snippet from webrtc_server log output."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if "Could not open camera" in stripped or "Camera index out of range" in stripped:
            return stripped
        if stripped.startswith("[ERROR]"):
            return stripped
    return None


def _fetch_dashboard_link(client: paramiko.SSHClient) -> str | None:
    """Read the Pi video-server log and extract the dashboard link."""
    return _parse_dashboard_link(_read_pi_log_tail(client, lines=200))


def _invalidate_probe_cache() -> None:
    _probe_cache["at"] = 0.0
    _probe_cache["result"] = None


def _probe_on_pi(client: paramiko.SSHClient, host: str) -> dict:
    """One SSH round-trip: process check, port check, log tail."""
    _, _, _, log = _pi_webrtc_paths()
    term_pat = shlex.quote(PI_WEBRTC_TERMINAL_TITLE)
    script = (
        "pgrep -f '[p]ython3?.*webrtc_server' >/dev/null 2>&1 && echo PROC=yes || echo PROC=no; "
        "pgrep -f 'yahboom_webrtc' >/dev/null 2>&1 && echo LAUNCHER=yes || echo LAUNCHER=no; "
        f"pgrep -f {term_pat} >/dev/null 2>&1 && echo TERM=yes || echo TERM=no; "
        f"ss -tln 2>/dev/null | grep -q ':{VIDEO_SERVER_PORT} ' && echo PORT=open || echo PORT=closed; "
        f"echo '---LOG---'; tail -n 200 {shlex.quote(log)} 2>/dev/null || true"
    )
    _, raw_bytes, _ = _ssh_exec(client, script, timeout=10)
    raw = raw_bytes.decode(errors="replace")
    process_active = any(token in raw for token in ("PROC=yes", "LAUNCHER=yes", "TERM=yes"))
    port_open = "PORT=open" in raw
    log_part = raw.split("---LOG---", 1)[-1] if "---LOG---" in raw else ""
    upstream_url = _parse_dashboard_link(log_part) if port_open else None
    if port_open and not upstream_url:
        upstream_url = _normalize_feed_url(f"http://{host}:{VIDEO_SERVER_PORT}")
    server_present = process_active or port_open
    return {
        "running": port_open,
        "server_present": server_present,
        "upstream_url": upstream_url,
        "host": host,
    }


def _wait_for_dashboard_link(host: str, timeout: float | None = None) -> str | None:
    """Poll the Pi log until [DASHBOARD LINK] appears (fresh SSH each poll)."""
    deadline = time.monotonic() + (timeout if timeout is not None else VIDEO_LINK_WAIT_SEC)
    while time.monotonic() < deadline:
        try:
            link = _with_ssh(host, _fetch_dashboard_link)
            if link:
                return link
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _video_debug(message: str, level: str = "info") -> None:
    """Print to Flask console and append to the shared event log."""
    print(f"[video] {message}", flush=True)
    mqtt_service.log_event(level, message)


def probe_video_stream(*, force: bool = False) -> dict:
    """
    SSH to the Pi: port listen + [DASHBOARD LINK] from log.
    Cached for PROBE_CACHE_TTL_SEC so /api/status does not SSH every poll.
    """
    host = _resolved_host()
    now = time.monotonic()
    if (
        not force
        and _probe_cache["result"] is not None
        and (now - _probe_cache["at"]) < PROBE_CACHE_TTL_SEC
    ):
        return _probe_cache["result"]

    result: dict = {"running": False, "server_present": False, "upstream_url": None, "host": host}
    prev = _probe_cache.get("result")
    probe_ok = False
    client = None
    try:
        client = _ssh_client(host)
        result = _probe_on_pi(client, host)
        probe_ok = True
    except Exception:
        pass
    finally:
        _ssh_close(client)
    if _server_present_locked and not result.get("server_present"):
        result["server_present"] = True
    # SSH glitches (common on Windows) must not flip a known-running stream off.
    if (
        not probe_ok
        and prev
        and prev.get("running")
        and time.monotonic() >= _stream_force_off_until
    ):
        result = {**prev, "host": host}
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = result
    return result


def _client_feed_url() -> str:
    """Relative path clients use for <img src> (same origin / Vite proxy)."""
    return PUBLIC_VIDEO_FEED_PATH


def get_stream_probe_snapshot() -> dict:
    """Cached Pi probe — safe for frequent /api/status polls (no SSH)."""
    result = _probe_cache.get("result") or {}
    running = bool(result.get("running"))
    return {
        "running": running,
        "server_present": bool(result.get("server_present", running)),
        "upstream_url": result.get("upstream_url"),
        "host": result.get("host"),
    }


def _apply_probe(probe: dict) -> None:
    """Persist probe state; WebRTC uses dashboard link URL, MJPEG uses relay."""
    from app.services.vit.vit_server_ssh import apply_vit_probe

    running = probe["running"]
    upstream = probe.get("upstream_url")
    prev_upstream = mqtt_service.video_upstream_url

    mqtt_service.stream_running = running
    apply_vit_probe({"running": running, "host": probe.get("host")})
    mqtt_service.video_upstream_url = upstream if running else None

    if running and upstream:
        if VIDEO_USE_MJPEG_RELAY:
            mqtt_service.video_stream_url = _client_feed_url()
            video_relay.start(upstream)
            if upstream != prev_upstream:
                _video_debug(f"Relay ingesting Pi stream — clients use {_client_feed_url()}")
        else:
            mqtt_service.video_stream_url = upstream
            video_relay.stop()
            if upstream != prev_upstream:
                _video_debug(f"WebRTC server — clients use {upstream}")
    else:
        mqtt_service.video_stream_url = None
        video_relay.stop()

    if not running and prev_upstream:
        _video_debug("Video stream stopped — no upstream")


def _mark_server_present(host: str) -> None:
    """Probe cache: Pi session is launching or running (blocks duplicate SSH starts)."""
    global _server_present_locked
    _server_present_locked = True
    prev = _probe_cache.get("result") or {}
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = {
        "running": bool(prev.get("running")),
        "server_present": True,
        "upstream_url": prev.get("upstream_url"),
        "host": host,
    }


def _complete_stream_start(host: str) -> None:
    """Background: wait for dashboard link and refresh probe state."""
    try:
        link = _wait_for_dashboard_link(host)
        if link:
            probe: dict = {
                "running": True,
                "server_present": True,
                "upstream_url": link,
                "host": host,
            }
        else:
            probe = probe_video_stream(force=True)
            if not probe.get("server_present"):
                probe["server_present"] = bool(probe.get("running"))
        try:
            log_tail = _with_ssh(host, lambda client: _read_pi_log_tail(client))
        except Exception:
            log_tail = ""
        _probe_cache["at"] = time.monotonic()
        _probe_cache["result"] = probe
        _apply_probe(probe)
        if probe.get("upstream_url"):
            client_url = mqtt_service.video_stream_url or probe["upstream_url"]
            _video_debug(f"Camera server started — dashboard link {client_url}")
            cam_err = _camera_error_from_log(log_tail)
            if cam_err:
                _video_debug(f"Camera warning — {cam_err}", level="warning")
        else:
            _video_debug(
                "Camera server started — [DASHBOARD LINK] not found in log "
                f"({_pi_webrtc_paths()[3]})"
            )
    except Exception as exc:
        _video_debug(f"Stream start follow-up: {exc}", level="warning")


def run_start_stream() -> tuple[dict, int]:
    """
    SSH into the Pi, open a desktop terminal running webrtc_server.py.
    Returns as soon as the terminal is open; link probing continues in background.
    """
    global _stream_force_off_until
    host = _resolved_host()
    terminal_opened = False

    try:
        existing = probe_video_stream(force=True)
        # Only skip launch when the stream is actually live (port open).
        # server_present can stay true from a stale lock after a crashed session.
        if existing.get("running"):
            _mark_server_present(host)
            _apply_probe({**existing, "server_present": True})
            return {
                "status": "already running",
                "host": host,
                "video_url": mqtt_service.video_stream_url,
                "upstream_url": existing.get("upstream_url"),
                "running": True,
                "server_present": True,
                "terminal_opened": True,
            }, 200

        # Stale server_present lock with no live stream — clear and relaunch.
        global _server_present_locked
        if existing.get("server_present") and not existing.get("running"):
            _server_present_locked = False
            _invalidate_probe_cache()

        _invalidate_probe_cache()
        _stream_force_off_until = 0.0
        _with_ssh(
            host,
            lambda client: _ssh_exec(
                client,
                "pkill -f 'python3?.*webrtc_server\\.py' 2>/dev/null; "
                "pkill -f 'webrtc_server\\.py' 2>/dev/null; "
                "sleep 0.5; true",
                timeout=10,
            ),
        )
        _pi_terminal_start_webrtc(host)
        terminal_opened = True
        _mark_server_present(host)

        threading.Thread(
            target=_complete_stream_start,
            args=(host,),
            daemon=True,
            name="stream-start",
        ).start()

        return {
            "status": "stream started",
            "host": host,
            "video_url": mqtt_service.video_stream_url,
            "upstream_url": None,
            "running": False,
            "server_present": True,
            "terminal_opened": True,
        }, 200
    except Exception as exc:
        if terminal_opened:
            _mark_server_present(host)
            threading.Thread(
                target=_complete_stream_start,
                args=(host,),
                daemon=True,
                name="stream-start",
            ).start()
            _video_debug(
                "Camera server terminal opened — stream may still be starting",
                level="warning",
            )
            return {
                "status": "stream started",
                "host": host,
                "video_url": mqtt_service.video_stream_url,
                "upstream_url": mqtt_service.video_upstream_url,
                "running": False,
                "server_present": True,
                "terminal_opened": True,
            }, 200
        return {
            "status": "error",
            "error": _stream_user_error(exc),
            "terminal_opened": False,
        }, 500


def _stream_user_error(exc: Exception) -> str:
    """Hide noisy Paramiko/socket EINVAL messages from the UI."""
    msg = str(exc).strip()
    if not msg or "errno 22" in msg.lower() or "invalid argument" in msg.lower():
        return "Could not reach the Pi — check broker IP and SSH settings"
    return msg


@stream_bp.route("/start_stream", methods=["POST"])
def start_stream():
    payload, code = run_start_stream()
    return jsonify(payload), code


def run_stop_stream() -> tuple[dict, int]:
    """SSH into the Pi and kill webrtc_server.py plus its desktop terminal."""
    global _stream_force_off_until, _server_present_locked
    host = _resolved_host()
    prev_link = mqtt_service.video_stream_url

    try:
        _invalidate_probe_cache()
        _stream_force_off_until = time.monotonic() + 60.0
        _server_present_locked = False
        _with_ssh(
            host,
            lambda client: _ssh_exec(
                client,
                "pkill -f 'python3?.*webrtc_server\\.py' 2>/dev/null; "
                "pkill -f 'webrtc_server\\.py' 2>/dev/null; "
                f"pkill -f {shlex.quote(PI_WEBRTC_LAUNCHER)} 2>/dev/null; "
                f"pkill -f {shlex.quote(PI_WEBRTC_TERMINAL_TITLE)} 2>/dev/null; "
                "sleep 0.3; true",
                timeout=10,
            ),
        )
    except Exception as exc:
        return {"status": "error", "error": _stream_user_error(exc)}, 500

    probe = {"running": False, "server_present": False, "upstream_url": None, "host": host}
    try:
        live = probe_video_stream(force=True)
        if not live.get("server_present"):
            live["running"] = False
            live["upstream_url"] = None
        probe = live
    except Exception:
        pass
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = probe
    _apply_probe(probe)

    if prev_link:
        _video_debug(f"Camera server stopped — was: {prev_link}")
    else:
        _video_debug("Camera server stopped")
    return {"status": "stream stopped", "running": False}, 200


@stream_bp.route("/stop_stream", methods=["POST"])
def stop_stream():
    payload, code = run_stop_stream()
    return jsonify(payload), code


@stream_bp.route("/stream_status", methods=["GET"])
def stream_status():
    """Pi stream state — optional ?force=1 for a live SSH probe."""
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    probe = probe_video_stream(force=force)
    if force:
        _apply_probe(probe)
    return jsonify({
        "running": probe["running"],
        "server_present": probe.get("server_present", probe["running"]),
        "host": probe["host"],
        "upstream_url": probe.get("upstream_url"),
        "video_url": mqtt_service.video_stream_url,
        "relay_active": video_relay.is_active(),
    }), 200


@stream_bp.route("/webrtc/offer", methods=["POST"])
def webrtc_offer():
    """
    Proxy WebRTC SDP exchange to the Pi's webrtc_server.py /offer endpoint.
    Lets the dashboard render only a <video> element (no full Pi web page).
    """
    upstream = mqtt_service.video_upstream_url
    if not upstream:
        return jsonify({"error": "WebRTC upstream not available — start the camera server first."}), 503

    payload = request.get_json(force=True, silent=True)
    if not payload or "sdp" not in payload or "type" not in payload:
        return jsonify({"error": "Expected JSON body with sdp and type fields."}), 400

    offer_url = f"{upstream.rstrip('/')}/offer"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        offer_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            answer = json.loads(resp.read().decode())
            return jsonify(answer), resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return jsonify({"error": body or exc.reason}), exc.code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@stream_bp.route("/video_feed")
def video_feed():
    """
    MJPEG fan-out from the backend relay. Browsers use this instead of the Pi URL.
    """
    if not video_relay.is_active():
        return jsonify({"error": "Video relay not active — start the camera server first."}), 503

    return Response(
        stream_with_context(video_relay.subscribe()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "close",
        },
    )


def _background_video_probe_loop() -> None:
    """Refresh video URL/running state without blocking /api/status or movement POSTs."""
    while True:
        time.sleep(VIDEO_PROBE_INTERVAL_SEC)
        try:
            probe = probe_video_stream(force=True)
            _apply_probe(probe)
        except Exception:
            pass


def start_background_video_probe() -> None:
    """Start daemon thread once at app startup."""
    thread = threading.Thread(target=_background_video_probe_loop, daemon=True, name="video-probe")
    thread.start()
