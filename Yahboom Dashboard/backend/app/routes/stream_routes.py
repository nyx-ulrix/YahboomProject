"""
Video stream routes — HTTP probe, WebRTC proxy, MJPEG relay.

webrtc_server.py is started manually on the Pi (no SSH from this backend).
"""

import json
import threading
import time
import urllib.error
import urllib.request

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.services.mqtt_service import mqtt_service
from app.services.video_relay import video_relay
from config import (
    DEFAULT_BROKER_IP,
    VIDEO_SERVER_PORT,
    VIDEO_FEED_PATH,
    VIDEO_USE_MJPEG_RELAY,
    PUBLIC_VIDEO_FEED_PATH,
    PROBE_CACHE_TTL_SEC,
    VIDEO_PROBE_INTERVAL_SEC,
)

_probe_cache: dict = {"at": 0.0, "result": None}

stream_bp = Blueprint("stream", __name__, url_prefix="/api")


def _resolved_host() -> str:
    """Return the Pi IP — from the active MQTT connection or the .env default."""
    return mqtt_service.broker_ip or DEFAULT_BROKER_IP


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


def _probe_video_http(host: str) -> dict:
    """Check whether the Pi WebRTC server responds on VIDEO_SERVER_PORT."""
    base = f"http://{host}:{VIDEO_SERVER_PORT}"
    upstream_url = _normalize_feed_url(base)
    port_open = False
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(base, method=method)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status < 500:
                    port_open = True
                    break
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                port_open = True
                break
        except Exception:
            continue
    return {
        "running": port_open,
        "server_present": port_open,
        "upstream_url": upstream_url if port_open else None,
        "host": host,
    }


def _video_debug(message: str, level: str = "info") -> None:
    """Print to Flask console and append to the shared event log."""
    print(f"[video] {message}", flush=True)
    mqtt_service.log_event(level, message, tag="video")


def probe_video_stream(*, force: bool = False) -> dict:
    """
    HTTP check against the Pi WebRTC port.
    Cached for PROBE_CACHE_TTL_SEC so /api/status does not probe every poll.
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
    try:
        result = _probe_video_http(host)
        probe_ok = True
    except Exception:
        pass
    # Transient probe failures must not flip a known-running stream off.
    if not probe_ok and prev and prev.get("running"):
        result = {**prev, "host": host}
    _probe_cache["at"] = time.monotonic()
    _probe_cache["result"] = result
    return result


def _client_feed_url() -> str:
    """Relative path clients use for <img src> (same origin / Vite proxy)."""
    return PUBLIC_VIDEO_FEED_PATH


def get_stream_probe_snapshot() -> dict:
    """Cached Pi probe — safe for frequent /api/status polls."""
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
    from app.services.vit.vit_service import vit_service

    running = probe["running"]
    upstream = probe.get("upstream_url")
    prev_upstream = mqtt_service.video_upstream_url

    mqtt_service.stream_running = running
    mqtt_service.video_upstream_url = upstream if running else None

    if not running:
        vit_service.stop_embedding_size_requests()

    if running and upstream:
        video_relay.start(upstream)
        if VIDEO_USE_MJPEG_RELAY:
            mqtt_service.video_stream_url = _client_feed_url()
            if upstream != prev_upstream:
                _video_debug(f"Relay ingesting Pi stream — clients use {_client_feed_url()}")
        else:
            mqtt_service.video_stream_url = upstream
            if upstream != prev_upstream:
                _video_debug(f"WebRTC server — clients use {upstream} (relay ingesting for YOLO)")
    else:
        mqtt_service.video_stream_url = None
        video_relay.stop()

    if not running and prev_upstream:
        _video_debug("Video stream stopped — no upstream")


@stream_bp.route("/stream_status", methods=["GET"])
def stream_status():
    """Pi stream state — optional ?force=1 for a live HTTP probe."""
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
        return jsonify({"error": "WebRTC upstream not available — start webrtc_server.py on the Pi first."}), 503

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
        return jsonify({"error": "Video relay not active — start webrtc_server.py on the Pi first."}), 503

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
