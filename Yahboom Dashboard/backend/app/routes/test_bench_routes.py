"""
Stop-Time Test Bench API — stop-mode selection and cache-aware MQTT control.

Cache-aware offloading is toggled by publishing Cao_ON / Cao_OFF over MQTT
(see mqtt_service.publish_cache_aware_command). Readiness is driven solely by
the Pi's retained {"ready": ...} status on yahboom/cache_aware/ready.
"""

from flask import Blueprint, jsonify, request

from app.services.mqtt_service import mqtt_service
from app.services.vit.cloud_aware_estop import (
    STOP_MODE_CACHE,
    STOP_MODE_CLOUD,
    STOP_MODES,
    cloud_aware_estop,
)
from app.services.test_bench.session import test_bench_session
from app.services.vit.vit_service import vit_service

test_bench_bp = Blueprint("test_bench", __name__, url_prefix="/api/test_bench")


def _apply_stop_mode(mode: str) -> str:
    """Mirror stop mode to VIT + YOLO (YOLO runs only in cloud_aware / YOLO mode)."""
    applied = cloud_aware_estop.set_mode(mode)
    vit_service.set_detection_mode(applied)
    from app.services.yolo_service import yolo_service
    yolo_service.sync_detection_mode(applied)
    return applied


def _cache_script_fields() -> dict:
    """Cache-aware readiness derived from the Pi's MQTT ready flag only (no SSH)."""
    mqtt_ready = mqtt_service.cache_aware_embedding_ready
    return {
        "cache_script_running": mqtt_ready,
        "cache_script_detection_ready": mqtt_ready,
        "cache_aware_mqtt_ready": mqtt_ready,
    }


def _stop_mode_payload() -> dict:
    payload = cloud_aware_estop.get_status()
    payload.update(_cache_script_fields())
    return payload


@test_bench_bp.route("/stop_mode", methods=["GET"])
def get_stop_mode():
    return jsonify(_stop_mode_payload())


@test_bench_bp.route("/latest_detection", methods=["GET", "DELETE"])
def latest_detection():
    """Latest Pi cache-aware bottle detection from MQTT yahboom/detect/status."""
    if request.method == "DELETE":
        mqtt_service.clear_latest_cache_detection()
        return jsonify({"status": "ok"})
    return jsonify({
        "detection": mqtt_service.get_latest_cache_detection(),
    })


@test_bench_bp.route("/cache_aware", methods=["POST"])
def set_cache_aware():
    """
    Turn cache-aware offloading on/off by publishing Cao_ON / Cao_OFF over MQTT.

    Expected JSON: { "on": true|false }
    """
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on"))

    success, message = mqtt_service.publish_cache_aware_command(on)
    mode = STOP_MODE_CACHE if on else STOP_MODE_CLOUD
    _apply_stop_mode(mode)

    payload = {
        "status": "ok" if success else "error",
        **_stop_mode_payload(),
        "message": message,
    }
    return jsonify(payload), 200 if success else 503


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

    applied = _apply_stop_mode(mode)
    return jsonify({
        "status": "ok",
        **_stop_mode_payload(),
        "mode": applied,
    })


@test_bench_bp.route("/session", methods=["GET", "PATCH", "DELETE"])
def session():
    if request.method == "GET":
        return jsonify(test_bench_session.get_status())

    if request.method == "DELETE":
        return jsonify(test_bench_session.clear())

    data = request.get_json(silent=True) or {}
    active_start_ms = data.get("active_start_ms")
    frozen_elapsed_ms = data.get("frozen_elapsed_ms")
    fields: dict = {}
    if active_start_ms is not None:
        fields["active_start_ms"] = active_start_ms
    if frozen_elapsed_ms is not None:
        fields["frozen_elapsed_ms"] = frozen_elapsed_ms
    if not fields:
        return jsonify({
            "status": "error",
            "message": "Provide active_start_ms and/or frozen_elapsed_ms.",
        }), 400
    return jsonify(test_bench_session.update(**fields))


@test_bench_bp.route("/session/start", methods=["POST"])
def session_start():
    data = request.get_json(silent=True) or {}
    origin = str(data.get("origin") or "").strip()
    command_sent_at_ms = data.get("command_sent_at_ms")
    stop_mode = data.get("stop_mode")
    session_start_wall_ms = data.get("session_start_wall_ms")

    if not origin:
        return jsonify({"status": "error", "message": "Field 'origin' is required."}), 400
    if command_sent_at_ms is None or not isinstance(command_sent_at_ms, (int, float)):
        return jsonify({
            "status": "error",
            "message": "Field 'command_sent_at_ms' must be a number.",
        }), 400
    if stop_mode not in STOP_MODES:
        return jsonify({
            "status": "error",
            "message": f"Field 'stop_mode' must be one of: {', '.join(sorted(STOP_MODES))}.",
        }), 400
    if session_start_wall_ms is None or not isinstance(session_start_wall_ms, (int, float)):
        return jsonify({
            "status": "error",
            "message": "Field 'session_start_wall_ms' must be a number.",
        }), 400

    ok, payload, reason = test_bench_session.start(
        origin=origin,
        command_sent_at_ms=float(command_sent_at_ms),
        stop_mode=str(stop_mode),
        session_start_wall_ms=float(session_start_wall_ms),
    )
    if not ok:
        return jsonify({**payload, "status": "error", "message": reason}), 409
    return jsonify({**payload, "status": "ok"})


@test_bench_bp.route("/session/complete", methods=["POST"])
def session_complete():
    data = request.get_json(silent=True) or {}
    origin = str(data.get("origin") or "").strip()
    run = data.get("run")
    if not origin:
        return jsonify({"status": "error", "message": "Field 'origin' is required."}), 400
    if not isinstance(run, dict):
        return jsonify({"status": "error", "message": "Field 'run' must be an object."}), 400

    recorded, payload = test_bench_session.complete(run, origin)
    return jsonify({**payload, "status": "ok", "recorded": recorded})
