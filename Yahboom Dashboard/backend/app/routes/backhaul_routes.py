"""
Flask routes for the backhaul delay simulator
----------------------------------------------
GET  /api/backhaul/config   - current backhaul config (incl. h) + sampled delay
POST /api/backhaul/config   - update h (and optionally enabled), persisted to JSON
"""

from flask import Blueprint, jsonify, request
from app.services.backhaul_delay import backhaul_delay

backhaul_bp = Blueprint("backhaul", __name__, url_prefix="/api/backhaul")


@backhaul_bp.route("/config", methods=["GET"])
def get_config():
    """Return the current backhaul config and a freshly sampled delay preview."""
    return jsonify(backhaul_delay.get_config())


@backhaul_bp.route("/config", methods=["POST"])
def set_config():
    """
    Update the backhaul config.

    Expected JSON: { "h": <int> } and/or { "enabled": <bool> }
    """
    data = request.get_json(silent=True) or {}

    if "h" in data:
        try:
            h = int(data["h"])
        except (TypeError, ValueError):
            return jsonify({"status": "error",
                            "message": "'h' must be an integer."}), 400
        if h < 1:
            return jsonify({"status": "error",
                            "message": "'h' must be >= 1."}), 400
        backhaul_delay.set_h(h)

    if "enabled" in data:
        backhaul_delay.set_enabled(bool(data["enabled"]))

    return jsonify({"status": "ok", **backhaul_delay.get_config()})
