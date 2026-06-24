"""
Stop-Time Test Bench API — stop-mode selection and Pi cache-aware script control.
"""

from flask import Blueprint, jsonify, request

from app.services.test_bench.cache_aware_ssh import (
    probe_cache_aware_script,
    start_cache_aware_script,
    stop_cache_aware_script,
)
from app.services.vit.edge_aware_estop import (
    STOP_MODE_CACHE,
    STOP_MODE_EDGE,
    STOP_MODES,
    edge_aware_estop,
)

test_bench_bp = Blueprint("test_bench", __name__, url_prefix="/api/test_bench")


def _stop_mode_payload(*, cache_script_running: bool | None = None) -> dict:
    payload = edge_aware_estop.get_status()
    if cache_script_running is None:
        cache_script_running = bool(
            probe_cache_aware_script().get("running")
            if payload.get("mode") == STOP_MODE_CACHE
            else False
        )
    payload["cache_script_running"] = cache_script_running
    return payload


@test_bench_bp.route("/stop_mode", methods=["GET"])
def get_stop_mode():
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    running = False
    mode = edge_aware_estop.mode
    if mode == STOP_MODE_CACHE:
        running = bool(probe_cache_aware_script(force=force).get("running"))
    return jsonify(_stop_mode_payload(cache_script_running=running))


@test_bench_bp.route("/stop_mode", methods=["POST"])
def set_stop_mode():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode not in STOP_MODES:
        return jsonify({
            "status": "error",
            "message": f"Field 'mode' must be '{STOP_MODE_CACHE}' or '{STOP_MODE_EDGE}'.",
        }), 400

    prev = edge_aware_estop.mode
    applied = edge_aware_estop.set_mode(mode)
    cache_running = False
    message = None

    try:
        if mode == STOP_MODE_EDGE:
            stop_cache_aware_script()
            cache_running = False
        else:
            if prev != STOP_MODE_CACHE:
                stop_cache_aware_script()
            probe = start_cache_aware_script(wait=True)
            cache_running = bool(probe.get("running"))
            if not cache_running:
                message = (
                    "cache_aware_offloading.py did not start on the Pi within the "
                    "configured timeout — check SSH, vit_env, and script path."
                )
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc).strip() or "Failed to control cache-aware script on Pi",
            **_stop_mode_payload(cache_script_running=False),
            "mode": applied,
        }), 503

    payload = {
        "status": "ok" if cache_running or mode == STOP_MODE_EDGE else "error",
        **_stop_mode_payload(cache_script_running=cache_running),
        "mode": applied,
    }
    if message:
        payload["message"] = message
    code = 200 if cache_running or mode == STOP_MODE_EDGE else 503
    return jsonify(payload), code


@test_bench_bp.route("/cache_script/ensure", methods=["POST"])
def ensure_cache_script():
    """Start cache_aware_offloading.py when cache-aware mode is active (idempotent)."""
    if edge_aware_estop.mode != STOP_MODE_CACHE:
        return jsonify({
            "status": "ok",
            "skipped": True,
            "cache_script_running": False,
            **_stop_mode_payload(cache_script_running=False),
        })

    try:
        probe = start_cache_aware_script(wait=True)
        running = bool(probe.get("running"))
        payload = {
            "status": "ok" if running else "error",
            **_stop_mode_payload(cache_script_running=running),
        }
        if not running:
            payload["message"] = (
                "cache_aware_offloading.py is not running on the Pi"
            )
        return jsonify(payload), 200 if running else 503
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc).strip() or "Failed to start cache-aware script",
            **_stop_mode_payload(cache_script_running=False),
        }), 503
