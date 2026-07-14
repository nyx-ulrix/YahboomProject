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
GET  /api/vit/reference/active     – active reference embeddings (for browser i2i)
GET  /api/vit/client/latest_embedding – latest Pi embedding relayed to the browser
POST /api/vit/client/match_result  – record a match the browser computed
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, Response
from app.services.vit.vit_service import vit_service
from app.services.vit.reference_capture_ssh import (
    ReferenceCaptureError,
    activate_category,
    capture_snapshot,
    get_active_category,
    get_reference_capture_status,
    list_categories,
    sync_category,
)
from config import (
    EDGE_AWARE_REFERENCE_THRESHOLD,
    VIT_REFERENCE_DEFAULT_THRESHOLD,
    VIT_REFERENCE_EMBEDDINGS_FILE,
    VIT_REFERENCE_LABEL,
)

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


@vit_bp.route("/reference/active", methods=["GET"])
def reference_active():
    """
    Active reference embeddings for the browser image-to-image loop.

    Returns the objects from the activated reference_embeddings.json (each with a
    base64 float32 ``data`` blob, ``embedding_dim``, and ``threshold``) plus the
    active category and the stop threshold. The browser matches Pi embeddings
    against these vectors — the backend no longer runs the live match.
    """
    path = Path(VIT_REFERENCE_EMBEDDINGS_FILE)
    payload = {
        "status": "ok",
        "active_category": get_active_category(),
        "label": VIT_REFERENCE_LABEL,
        "default_threshold": VIT_REFERENCE_DEFAULT_THRESHOLD,
        "stop_threshold": EDGE_AWARE_REFERENCE_THRESHOLD,
        "objects": [],
    }
    if not path.exists():
        payload["error"] = f"reference file not found: {path}"
        return jsonify(payload)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        payload["status"] = "error"
        payload["error"] = f"failed to read reference file: {exc}"
        return jsonify(payload), 500

    objects = data.get("objects", []) if isinstance(data, dict) else []
    cleaned = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        entry = {
            "sample_id": obj.get("sample_id"),
            "label": obj.get("label", VIT_REFERENCE_LABEL),
            "embedding_dim": obj.get("embedding_dim"),
            "threshold": obj.get("threshold", VIT_REFERENCE_DEFAULT_THRESHOLD),
        }
        if "data" in obj:
            entry["data"] = obj["data"]
        elif "embedding" in obj:
            entry["embedding"] = obj["embedding"]
        else:
            continue
        cleaned.append(entry)
    payload["objects"] = cleaned
    payload["count"] = len(cleaned)
    return jsonify(payload)


@vit_bp.route("/client/latest_embedding", methods=["GET"])
def client_latest_embedding():
    """Latest Pi embedding relayed to the browser (base64). Dedupe on ``seq``."""
    return jsonify(vit_service.get_latest_client_embedding())


@vit_bp.route("/client/match_result", methods=["POST"])
def client_match_result():
    """
    Record an image-to-image match the browser computed. Telemetry only — the
    client triggers the stop itself.

    Expected JSON: { label, sample_id, similarity, threshold, hit,
                     embedding_dim, embedding_size, image_file_size }
    """
    data = request.get_json(silent=True) or {}
    result = vit_service.record_client_match(data)
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code
