"""
Flask routes for the VIT / MobileCLIP scene decoder
---------------------------------------------------
GET  /api/vit/status        – latest decoded result + model/connection status
POST /api/vit/config        – set the placeholder encoded-file-size limit (KB)
POST /api/vit/clear         – clear the current session history
GET  /api/vit/export        – download the session history as a CSV file
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, Response
from app.services.vit.vit_service import vit_service

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
