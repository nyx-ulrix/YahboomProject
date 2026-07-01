"""
Stop-Time Test Bench API — stop-mode selection and Pi cache-aware script control.
"""

from flask import Blueprint, jsonify, request

from app.services.mqtt_service import mqtt_service
from app.services.test_bench.cache_aware_ssh import (
    probe_cache_aware_script,
    start_cache_aware_script,
    stop_cache_aware_script,
)
from app.services.vit.edge_aware_estop import (
    STOP_MODE_EDGE,
    STOP_MODE_HYBRID,
    STOP_MODES,
    edge_aware_estop,
)

test_bench_bp = Blueprint("test_bench", __name__, url_prefix="/api/test_bench")


def _cache_script_fields(probe: dict | None = None, *, running: bool | None = None) -> dict:
    """Merge Pi cache-script probe fields into an API payload."""
    mqtt_ready = mqtt_service.cache_aware_embedding_ready
    if running is None:
        running = bool((probe or {}).get("running"))
    probe_ready = bool((probe or {}).get("detection_ready"))
    detection_ready = bool(running and probe_ready) or mqtt_ready
    return {
        "cache_script_running": running,
        "cache_script_detection_ready": detection_ready,
        "cache_aware_mqtt_ready": mqtt_ready,
        **({"cache_script_launch_mode": probe["launch_mode"]} if probe and probe.get("launch_mode") else {}),
        **({"cache_script_log": probe["log_path"]} if probe and probe.get("log_path") else {}),
    }


def _stop_mode_payload(
    *,
    probe: dict | None = None,
    cache_script_running: bool | None = None,
    force_probe: bool = False,
) -> dict:
    payload = edge_aware_estop.get_status()
    if probe is None:
        probe = probe_cache_aware_script(force=force_probe)
    running = cache_script_running if cache_script_running is not None else bool(probe.get("running"))
    if running and not probe.get("detection_ready") and not mqtt_service.cache_aware_embedding_ready:
        probe = {**probe, **probe_cache_aware_script(force=True)}
    payload.update(_cache_script_fields(probe, running=running))
    return payload


@test_bench_bp.route("/stop_mode", methods=["GET"])
def get_stop_mode():
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    probe = probe_cache_aware_script(force=force)
    return jsonify(_stop_mode_payload(probe=probe, force_probe=force))


@test_bench_bp.route("/cache_script/stop", methods=["POST"])
def stop_cache_script():
    """Stop cache_aware_offloading.py on the Pi and close its terminal."""
    try:
        probe = stop_cache_aware_script()
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc).strip() or "Failed to stop cache-aware script on Pi",
        }), 503
    return jsonify({
        "status": "ok",
        **_stop_mode_payload(probe=probe, cache_script_running=False),
    })


@test_bench_bp.route("/stop_mode", methods=["POST"])
def set_stop_mode():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode not in STOP_MODES:
        allowed = ", ".join(sorted(STOP_MODES))
        return jsonify({
            "status": "error",
            "message": f"Field 'mode' must be one of: {allowed}.",
        }), 400

    applied = edge_aware_estop.set_mode(mode)
    cache_running = False
    message = None
    probe: dict = {}

    try:
        if mode == STOP_MODE_EDGE:
            stop_cache_aware_script()
            cache_running = False
        else:
            reuse_existing = data.get("reuse_existing") in (True, "true", 1, "1", "yes")
            if reuse_existing:
                probe = probe_cache_aware_script(force=True)
                if probe.get("running"):
                    cache_running = True
                    if probe.get("detection_ready"):
                        message = "Cache aware enabled — using existing Pi script"
                    else:
                        message = (
                            "Pi script already running — waiting for bottle embedding "
                            "([DETECT] Text embedding ready). START unlocks when ready."
                        )
                else:
                    probe = start_cache_aware_script(wait=True)
                    cache_running = bool(probe.get("running"))
            else:
                probe = start_cache_aware_script(wait=True)
                cache_running = bool(probe.get("running"))
            if not cache_running and not message:
                message = (
                    "cache_aware_offloading.py did not start on the Pi within the "
                    "configured timeout — check SSH, vit_env, and script path."
                )
            elif cache_running and not probe.get("detection_ready") and not message:
                message = (
                    "Pi script is running — waiting for bottle embedding "
                    "([DETECT] Text embedding ready). START unlocks when ready."
                )
            if mode == STOP_MODE_HYBRID and cache_running:
                hybrid_msg = (
                    "Hybrid mode — Pi cache script and dashboard VIT bottle stop "
                    "are both armed (first trigger wins)"
                )
                message = f"{hybrid_msg}; {message}" if message else hybrid_msg
            elif probe.get("launch_mode") == "terminal" and cache_running and not message:
                message = "Opened Pi terminal — cache_aware_offloading.py"
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc).strip() or "Failed to control cache-aware script on Pi",
            **_stop_mode_payload(probe={}, cache_script_running=False),
            "mode": applied,
        }), 503

    payload = {
        "status": "ok" if cache_running or mode == STOP_MODE_EDGE else "error",
        **_stop_mode_payload(probe=probe, cache_script_running=cache_running),
        "mode": applied,
    }
    if message:
        payload["message"] = message
    code = 200 if cache_running or mode == STOP_MODE_EDGE else 503
    return jsonify(payload), code
