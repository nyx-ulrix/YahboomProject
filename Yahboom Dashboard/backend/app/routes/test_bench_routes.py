"""
Stop-Time Test Bench API — stop-mode selection and cache-aware MQTT control.

Cache-aware offloading is toggled by publishing Cae_ON / Cae_OFF over MQTT
(see mqtt_service.publish_cache_aware_command). Readiness is driven solely by
the Pi's retained {"ready": ...} status on yahboom/cache_aware/ready.
"""

from flask import Blueprint, jsonify, request

from app.services.mqtt_service import mqtt_service
from app.services.vit.edge_aware_estop import (
    STOP_MODE_CACHE,
    STOP_MODE_EDGE,
    STOP_MODES,
    edge_aware_estop,
)
from app.services.vit.vit_service import vit_service

test_bench_bp = Blueprint("test_bench", __name__, url_prefix="/api/test_bench")


def _cache_script_fields() -> dict:
    """Cache-aware readiness derived from the Pi's MQTT ready flag only (no SSH)."""
    mqtt_ready = mqtt_service.cache_aware_embedding_ready
    return {
        "cache_script_running": mqtt_ready,
        "cache_script_detection_ready": mqtt_ready,
        "cache_aware_mqtt_ready": mqtt_ready,
    }


def _stop_mode_payload() -> dict:
    payload = edge_aware_estop.get_status()
    payload.update(_cache_script_fields())
    return payload


@test_bench_bp.route("/stop_mode", methods=["GET"])
def get_stop_mode():
    return jsonify(_stop_mode_payload())


@test_bench_bp.route("/latest_detection", methods=["GET"])
def get_latest_detection():
    """Latest Pi cache-aware bottle detection from MQTT yahboom/detect/status."""
    return jsonify({
        "detection": mqtt_service.get_latest_cache_detection(),
    })


@test_bench_bp.route("/cache_aware", methods=["POST"])
def set_cache_aware():
    """
    Turn cache-aware offloading on/off by publishing Cae_ON / Cae_OFF over MQTT.

    Expected JSON: { "on": true|false }
    """
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on"))

    success, message = mqtt_service.publish_cache_aware_command(on)
    mode = STOP_MODE_CACHE if on else STOP_MODE_EDGE
    edge_aware_estop.set_mode(mode)
    vit_service.set_detection_mode(mode)

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

    applied = edge_aware_estop.set_mode(mode)
    vit_service.set_detection_mode(applied)
    return jsonify({
        "status": "ok",
        **_stop_mode_payload(),
        "mode": applied,
    })
