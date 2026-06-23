"""
Flask routes for the SLAM service
----------------------------------
GET  /api/slam/map     – full map JSON (cells + robot pose + trajectory)
GET  /api/slam/status  – lightweight status (no cells array)
POST /api/slam/reset   – wipe the accumulated map and restart from origin
POST /api/slam/connect – manually connect SLAM client to a broker IP
"""

from flask import Blueprint, jsonify, request
from slam_service import slam_service

slam_bp = Blueprint("slam", __name__, url_prefix="/api/slam")


@slam_bp.route("/map", methods=["GET"])
def get_map():
    """
    Return the full SLAM state:
    robot pose, occupancy grid cells, trajectory, and stats.

    Consumers poll this endpoint (typically every 500 ms) to render a
    live top-down map of explored space.
    """
    crop = str(request.args.get("crop", "")).lower() in {"1", "true", "yes", "on"}
    return jsonify(slam_service.get_map(crop=crop))


@slam_bp.route("/status", methods=["GET"])
def get_status():
    """
    Lightweight status endpoint – returns connection, scan count, pose
    and coverage without the large cells array.  Useful for health checks
    and status widgets.
    """
    return jsonify(slam_service.get_status())


@slam_bp.route("/reset", methods=["POST"])
def reset_map():
    """
    Clear the accumulated occupancy map, robot pose, and trajectory.
    The SLAM service resets on the next writer-thread cycle.
    """
    slam_service.request_reset()
    return jsonify({"status": "ok", "message": "SLAM reset requested."})


@slam_bp.route("/connect", methods=["POST"])
def slam_connect():
    """
    Manually connect the SLAM MQTT client to the given broker.
    Normally this happens automatically when the main MQTT service
    connects, but this route allows a direct override.

    Expected JSON: { "ip": "192.168.x.x" }
    """
    data = request.get_json(silent=True) or {}
    ip   = str(data.get("ip", "")).strip()
    if not ip:
        return jsonify({"status": "error",
                        "message": "Field 'ip' is required."}), 400

    slam_service.connect(ip)
    return jsonify({"status": "ok",
                    "message": f"SLAM client connecting to {ip}."})
