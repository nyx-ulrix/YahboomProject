"""
Flask routes for the VIT / MobileCLIP scene decoder
---------------------------------------------------
GET  /api/vit/status        – latest decoded result + model/connection status
POST /api/vit/config        – set the placeholder encoded-file-size limit (KB)
POST /api/vit/clear         – clear the current session history
GET  /api/vit/export        – download the session history as a CSV file
GET  /api/vit/reference/categories – list reference categories
POST /api/vit/reference/capture    – save latest Pi MQTT embedding
POST /api/vit/reference/activate   – activate a category for edge matching
GET  /api/vit/reference/status     – reference library status
GET  /api/vit/reference/active     – active reference embeddings (for browser i2i)
GET  /api/vit/reference/library    – full reference library for browser scan
POST /api/vit/reference/upload     – encode a static image into the library
GET  /api/vit/reference/samples    – list all library samples (for move UI)
POST /api/vit/reference/move       – move a sample to another category
GET  /api/vit/reference/sample-image/<category>/<sample_id> – preview image
GET  /api/vit/client/latest_embedding – latest Pi embedding relayed to the browser
POST /api/vit/client/match_result  – record a match the browser computed
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, Response
from app.services.vit.vit_service import vit_service
from app.services.vit.reference_capture import (
    ReferenceCaptureError,
    activate_category,
    capture_snapshot_from_image,
    capture_snapshot_from_relay,
    get_active_category,
    get_active_embedding_size_bytes,
    get_reference_capture_status,
    get_sample_image_path,
    list_categories,
    list_library_samples,
    load_library_for_client,
    move_library_sample,
    name_to_category,
)
from app.services.vit.image_encoder import ImageEncoderError
from config import (
    EDGE_AWARE_REFERENCE_THRESHOLD,
    VIT_REFERENCE_DEFAULT_THRESHOLD,
    VIT_REFERENCE_EMBEDDINGS_FILE,
    VIT_REFERENCE_LABEL,
    VIT_STOP_REFERENCE_CATEGORY,
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
    Save the latest Pi MQTT embedding relayed by vit_service into a local category
    folder.

    Form fields: category (required), label, seq (optional dedupe)
    JSON body also accepted: { "category", "label", "seq" }
    """
    data = request.form.to_dict() if request.form else {}
    if not data:
        data = request.get_json(silent=True) or {}

    category = data.get("category")
    if not category:
        return jsonify({"status": "error", "message": "Field 'category' is required."}), 400

    label = str(data.get("label", VIT_REFERENCE_LABEL))
    seq_raw = data.get("seq")
    expected_seq = None
    if seq_raw is not None:
        try:
            expected_seq = int(seq_raw)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Field 'seq' must be an integer."}), 400

    embedding = vit_service.get_latest_client_embedding()
    try:
        result = capture_snapshot_from_relay(
            category,
            embedding,
            label=label,
            expected_seq=expected_seq,
        )
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

    Expected JSON: { "category": "target_bottle", "embedding_size_bytes": 2048 }
    When embedding_size_bytes is omitted, uses the active size or the largest available.
    """
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if not category:
        return jsonify({"status": "error", "message": "Field 'category' is required."}), 400

    size_raw = data.get("embedding_size_bytes")
    embedding_size_bytes = None
    if size_raw is not None:
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Field 'embedding_size_bytes' must be an integer (512, 1024, or 2048).",
            }), 400

    try:
        result = activate_category(category, embedding_size_bytes=embedding_size_bytes)
        reloaded = vit_service.reload_reference()
        result["reference_reloaded"] = reloaded
        result["reference_ready"] = vit_service.get_status().get("reference_ready", False)
        return jsonify(result)
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 400


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
        "active_embedding_size_bytes": get_active_embedding_size_bytes(),
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


@vit_bp.route("/reference/library", methods=["GET"])
def reference_library():
    """
    Full reference library for browser image-to-image scan.

    Returns every captured category at the requested embedding size. The client
    matches live Pi embeddings against all objects for display; only
    ``stop_category`` (default ``target_bottle``) qualifies for edge stop.
    """
    size_raw = request.args.get("embedding_size_bytes")
    embedding_size_bytes = None
    if size_raw is not None:
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Query param 'embedding_size_bytes' must be an integer.",
            }), 400
    return jsonify(load_library_for_client(embedding_size_bytes))


@vit_bp.route("/reference/upload", methods=["POST"])
def reference_upload():
    """
    Encode a static image with MobileCLIP-S1 and save to the reference library.

    Form fields:
      - image (file, required)
      - name (display label, required) — also used to derive category slug
      - category (optional slug override)
      - embedding_size_bytes (optional, default 2048)
    """
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"status": "error", "message": "Field 'image' (file) is required."}), 400

    name = str(request.form.get("name", "")).strip()
    if not name:
        return jsonify({"status": "error", "message": "Field 'name' is required."}), 400

    category_raw = request.form.get("category")
    try:
        category = (
            name_to_category(str(category_raw))
            if category_raw and str(category_raw).strip()
            else name_to_category(name)
        )
    except ReferenceCaptureError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    size_raw = request.form.get("embedding_size_bytes")
    embedding_size_bytes = 2048
    if size_raw is not None and str(size_raw).strip():
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Field 'embedding_size_bytes' must be an integer (512, 1024, or 2048).",
            }), 400

    try:
        image_bytes = image.read()
        if not image_bytes:
            return jsonify({"status": "error", "message": "Uploaded image is empty."}), 400
        result = capture_snapshot_from_image(
            category,
            image_bytes,
            label=name,
            original_filename=image.filename,
            embedding_size_bytes=embedding_size_bytes,
        )
        return jsonify(result)
    except ImageEncoderError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 503
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 400


@vit_bp.route("/reference/samples", methods=["GET"])
def reference_samples():
    """All captured samples for the upload/move widget."""
    size_raw = request.args.get("embedding_size_bytes")
    embedding_size_bytes = None
    if size_raw is not None:
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Query param 'embedding_size_bytes' must be an integer.",
            }), 400
    samples = list_library_samples(embedding_size_bytes)
    return jsonify({
        "status": "ok",
        "embedding_size_bytes": embedding_size_bytes or get_active_embedding_size_bytes() or 2048,
        "samples": samples,
        "count": len(samples),
        "categories": list_categories(),
    })


@vit_bp.route("/reference/move", methods=["POST"])
def reference_move():
    """
    Move a sample from one category to another.

    Expected JSON:
      { "from_category", "sample_id", "to_category", "label"?, "embedding_size_bytes"? }
    """
    data = request.get_json(silent=True) or {}
    from_category = data.get("from_category")
    to_category = data.get("to_category")
    sample_id_raw = data.get("sample_id")

    if not from_category or not to_category:
        return jsonify({
            "status": "error",
            "message": "Fields 'from_category' and 'to_category' are required.",
        }), 400
    if sample_id_raw is None:
        return jsonify({"status": "error", "message": "Field 'sample_id' is required."}), 400

    try:
        sample_id = int(sample_id_raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Field 'sample_id' must be an integer."}), 400

    label = data.get("label")
    size_raw = data.get("embedding_size_bytes")
    embedding_size_bytes = None
    if size_raw is not None:
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Field 'embedding_size_bytes' must be an integer.",
            }), 400

    try:
        to_slug = name_to_category(str(to_category))
        result = move_library_sample(
            str(from_category),
            sample_id,
            to_slug,
            label=str(label).strip() if label is not None else None,
            embedding_size_bytes=embedding_size_bytes,
        )
        return jsonify(result)
    except ReferenceCaptureError as exc:
        payload = {"status": "error", "message": str(exc)}
        if exc.details:
            payload["details"] = exc.details
        return jsonify(payload), 400


@vit_bp.route("/reference/sample-image/<category>/<int:sample_id>", methods=["GET"])
def reference_sample_image(category: str, sample_id: int):
    """Serve a stored preview image for a library sample."""
    size_raw = request.args.get("embedding_size_bytes")
    embedding_size_bytes = None
    if size_raw is not None:
        try:
            embedding_size_bytes = int(size_raw)
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Query param 'embedding_size_bytes' must be an integer.",
            }), 400

    try:
        path = get_sample_image_path(category, sample_id, embedding_size_bytes)
    except ReferenceCaptureError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    if path is None or not path.exists():
        return jsonify({"status": "error", "message": "Sample image not found."}), 404

    suffix = path.suffix.lower()
    mimetype = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    return Response(path.read_bytes(), mimetype=mimetype)


@vit_bp.route("/client/latest_embedding", methods=["GET"])
def client_latest_embedding():
    """Latest Pi embedding relayed to the browser (base64). Dedupe on ``seq``."""
    return jsonify(vit_service.get_latest_client_embedding())


@vit_bp.route("/client/match_result", methods=["POST"])
def client_match_result():
    """
    Record an image-to-image match the browser computed. Telemetry only — the
    client triggers the stop itself.

    Expected JSON: { label, category, sample_id, similarity, threshold, hit,
                     stop_hit, embedding_dim, embedding_size, image_file_size }
    """
    data = request.get_json(silent=True) or {}
    result = vit_service.record_client_match(data)
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code
