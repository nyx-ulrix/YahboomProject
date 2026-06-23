"""
API routes for Yahboom bot control and connection
"""

import time
from flask import Blueprint, request, jsonify
from app.services.mqtt_service import mqtt_service
from config import DEFAULT_BROKER_IP, ALLOWED_COMMANDS, CAMERA_COMMANDS, TOPIC, PUBLISH_TIMEOUT

bot_bp = Blueprint("bot", __name__, url_prefix="/api")


@bot_bp.route("/connect", methods=["POST"])
def connect():
    """Connect to MQTT broker. Expected JSON: {"ip": "broker_ip"}"""
    data = request.get_json()
    ip = data.get("ip", DEFAULT_BROKER_IP).strip() if data else DEFAULT_BROKER_IP

    if not ip:
        ip = DEFAULT_BROKER_IP

    success, message = mqtt_service.connect_to_broker(ip)

    return jsonify({
        "status": "connected" if success else "error",
        "message": message,
        "broker_ip": ip
    }), 200 if success else 400


@bot_bp.route("/estop", methods=["POST"])
def estop():
    """Engage or release emergency stop. Expected JSON: {"active": true|false}"""
    data = request.get_json()
    active = bool(data.get("active", True)) if data else True
    mqtt_service.set_estop(active)
    return jsonify({
        "status": "ok",
        "estop_active": mqtt_service.estop_active,
    })


@bot_bp.route("/send_command", methods=["POST"])
def send_command():
    """Publish a bot movement command to MQTT."""
    data = request.get_json()
    command = data.get("command") if data else None

    if command not in ALLOWED_COMMANDS:
        return jsonify({
            "status": "error",
            "message": "Invalid command",
            "command": command
        }), 400

    # Block movement commands while emergency stop is active.
    # Camera commands are exempt; off/safety commands are allowed so modes can
    # be disabled without clearing the emergency stop latch first.
    if mqtt_service.estop_active and command not in {"stop", "auto_off", "estop_on", "estop_off"} and command not in CAMERA_COMMANDS:
        return jsonify({
            "status": "error",
            "message": "Emergency stop is active — resume control first.",
            "command": command
        }), 403

    if not mqtt_service.connected:
        reconnect_ip = mqtt_service.broker_ip or DEFAULT_BROKER_IP
        success, message = mqtt_service.connect_to_broker(reconnect_ip)
        if not success:
            return jsonify({
                "status": "error",
                "message": "MQTT broker not connected. Press Connect first.",
                "command": command
            }), 503

    start_time = time.time()

    try:
        result = mqtt_service.mqtt_client.publish(TOPIC, command)
        if PUBLISH_TIMEOUT > 0:
            result.wait_for_publish(timeout=PUBLISH_TIMEOUT)
        latency = (time.time() - start_time) * 1000
        mqtt_service.log_event("info", f"MQTT -> {TOPIC}: {command}")
        try:
            from slam_service import slam_service
            slam_service.slam.apply_command(command)
        except Exception:
            pass

        return jsonify({
            "status": "published",
            "command": command,
            "topic": TOPIC,
            "latency": round(latency, 2),
            "message": f"Published '{command}' to topic '{TOPIC}'"
        })

    except Exception as e:
        mqtt_service.connected = False
        mqtt_service.log_event('error', f"Publish failed: {str(e)}")
        return jsonify({
            "status": "error",
            "command": command,
            "message": str(e)
        }), 500


@bot_bp.route("/status", methods=["GET"])
def status():
    """Fast path — no SSH. Video state is updated by stream routes / background probe."""
    from app.routes.stream_routes import get_stream_probe_snapshot

    probe = get_stream_probe_snapshot()
    return jsonify({
        "connected":      mqtt_service.connected,
        "broker_ip":      mqtt_service.broker_ip,
        "topic":          TOPIC,
        "estop_active":   mqtt_service.estop_active,
        "stream_running": mqtt_service.stream_running,
        "server_present": probe["server_present"],
        "video_url":      mqtt_service.video_stream_url,
    })


@bot_bp.route("/events", methods=["GET"])
def events():
    """Return the shared server-side event log (newest-last order)."""
    return jsonify(mqtt_service.get_events())


@bot_bp.route("/safety_status", methods=["GET"])
def safety_status():
    """Return the latest LiDAR safety status received from MQTT."""
    return jsonify(mqtt_service.get_safety_status())


@bot_bp.route("/grid_status", methods=["GET"])
def grid_status():
    """Return the latest GRID_TOPIC LiDAR JSON payload (parsed)."""
    return jsonify(mqtt_service.get_grid_status())


@bot_bp.route("/safety_topic_status", methods=["GET"])
def safety_topic_status():
    """Return the latest SAFETY_TOPIC text payload (parsed)."""
    return jsonify(mqtt_service.get_safety_topic_status())


@bot_bp.route("/config", methods=["GET"])
def config():
    """Expose backend defaults so the frontend never has to hard-code them."""
    return jsonify({
        "default_broker_ip": DEFAULT_BROKER_IP,
    })
