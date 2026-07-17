"""Flask routes for YOLOv8 live-video object detection."""

from flask import Blueprint, jsonify, request

from app.services.yolo_service import yolo_service

yolo_bp = Blueprint("yolo", __name__, url_prefix="/api/yolo")


@yolo_bp.route("/status", methods=["GET"])
def get_status():
    """Latest YOLO detections from the live video relay."""
    return jsonify(yolo_service.get_status())


@yolo_bp.route("/config", methods=["POST"])
def set_config():
    """
    Update YOLO runtime settings.

    JSON body: { "enabled": true } and/or { "confidence": 0.25 }
    or { "confidence_percent": 25 }
    """
    data = request.get_json(silent=True) or {}
    payload: dict = {"status": "ok"}

    if "enabled" in data:
        payload["enabled"] = yolo_service.set_enabled(bool(data["enabled"]))

    conf = data.get("confidence")
    if conf is None and "confidence_percent" in data:
        try:
            conf = float(data["confidence_percent"]) / 100.0
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "confidence_percent must be a number."}), 400

    if conf is not None:
        try:
            payload["confidence_threshold"] = yolo_service.set_confidence(float(conf))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "confidence must be a number."}), 400

    payload.update(yolo_service.get_status())
    return jsonify(payload)


@yolo_bp.route("/clear", methods=["POST"])
def clear_session():
    yolo_service.clear_session()
    return jsonify({"status": "ok", "message": "YOLO session cleared."})
