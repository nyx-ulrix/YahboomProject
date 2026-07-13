"""
Flask routes for the VIT / MobileCLIP scene decoder
---------------------------------------------------
GET  /api/vit/status        – latest decoded result + model/connection status
POST /api/vit/config        – set the placeholder encoded-file-size limit (KB)
POST /api/vit/clear         – clear the current session history
GET  /api/vit/export        – download the session history as a CSV file
GET  /api/vit/reference/categories – list reference categories
POST /api/vit/reference/capture    – SSH capture one snapshot on Pi + SFTP sync
POST /api/vit/reference/activate   – activate a category for edge matching
GET  /api/vit/reference/status     – reference library status
POST /api/vit/edge/encode          – encode a WebRTC frame on the backend (Edge Only)
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, Response
from app.services.vit.vit_service import vit_service
from app.services.vit.reference_capture_ssh import (
    ReferenceCaptureError,
    activate_category,
    capture_snapshot,
    get_reference_capture_status,
    list_categories,
    sync_category,
)
from config import VIT_REFERENCE_LABEL

vit_bp = Blueprint("vit", __name__, url_prefix="/api/vit")


@vit_bp.route("/status", methods=["GET"])
def get_status():
    """Latest decoded detection, confidence, and decoder/connection health."""
    return jsonify(vit_service.get_status())


@vit_bp.route("/config", methods=["POST"])
def set_config():
    """
    Update the placeholder encoded-file-size limit driven by the widget slider.

    Expected JSON: { "max_file_size_kb": <int> } or { "embedding_size_bytes": <int> }
    (512 / 1024 / 2048 — publishes embds1 / embds2 / embds3 on the embedding topic)
    """
    data = request.get_json(silent=True) or {}
    kb = data.get("max_file_size_kb", data.get("embedding_size_bytes"))
    if kb is None:
        return jsonify({"status": "error",
                        "message": "Field 'max_file_size_kb' or 'embedding_size_bytes' is required."}), 400
    try:
        kb = int(kb)
    except (TypeError, ValueError):
        return jsonify({"status": "error",
                        "message": "'max_file_size_kb' must be an integer."}), 400

    applied = vit_service.set_max_file_size(kb)
    return jsonify({"status": "ok", "max_file_size_kb": applied})


@vit_bp.route("/clear", methods=["POST"])
def clear_session():
    """Clear the accumulated session history (and latest result)."""
    vit_service.clear_session()
    return jsonify({"status": "ok", "message": "VIT session cleared."})


@vit_bp.route("/export", methods=["GET"])
def export_csv():
    """Download the session history as a CSV file."""
    csv_text = vit_service.export_csv()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"vit_session_{stamp}.csv"
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@vit_bp.route("/reference/categories", methods=["GET"])
def reference_categories():
    return jsonify({"status": "ok", "categories": list_categories()})


@vit_bp.route("/reference/status", methods=["GET"])
def reference_status():
    payload = get_reference_capture_status()
    payload["status"] = "ok"
    payload["reference_ready"] = vit_service.get_status().get("reference_ready", False)
    return jsonify(payload)


@vit_bp.route("/reference/capture", methods=["POST"])
def reference_capture():
    """
    SSH to Pi, capture one embedding snapshot into a category folder, then SFTP sync.

    Expected JSON: { "category": "black_bottle", "label": "bottle" }
    """
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if not category:
        return jsonify({"status": "error", "message": "Field 'category' is required."}), 400

    label = str(data.get("label", VIT_REFERENCE_LABEL))
    sync = bool(data.get("sync", True))

    try:
        result = capture_snapshot(category, label=label, sync=sync)
        return jsonify(result)
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 503


@vit_bp.route("/reference/activate", methods=["POST"])
def reference_activate():
    """
    Copy a synced category file to reference_embeddings.json and reload matching.

    Expected JSON: { "category": "black_bottle" }
    """
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if not category:
        return jsonify({"status": "error", "message": "Field 'category' is required."}), 400

    try:
        result = activate_category(category)
        reloaded = vit_service.reload_reference()
        result["reference_reloaded"] = reloaded
        result["reference_ready"] = vit_service.get_status().get("reference_ready", False)
        return jsonify(result)
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 400


@vit_bp.route("/reference/sync", methods=["POST"])
def reference_sync():
    """SFTP-pull a category folder from Pi without capturing a new snapshot."""
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if not category:
        return jsonify({"status": "error", "message": "Field 'category' is required."}), 400

    try:
        return jsonify(sync_category(category))
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 503


@vit_bp.route("/edge/encode", methods=["POST"])
def edge_encode():
    """
    Edge Only: the browser forwards a WebRTC video frame (JPEG) here. The backend
    encodes it with MobileCLIP and image-to-image matches it against the active
    reference library, updating /api/vit/status (which the stop poller reads).

    Accepts multipart form field 'frame' or a raw image/* request body.
    """
    if vit_service.get_detection_mode() != "edge_aware":
        return jsonify({"status": "ignored", "reason": "not in edge_aware mode"}), 200

    image_bytes: bytes | None = None
    if "frame" in request.files:
        image_bytes = request.files["frame"].read()
    elif request.data:
        image_bytes = request.data
    if not image_bytes:
        return jsonify({"status": "error", "message": "no frame provided"}), 400

    result = vit_service.encode_frame_and_match(image_bytes)
    code = 200 if result.get("status") in ("ok", "ignored") else 503
    return jsonify(result), code
