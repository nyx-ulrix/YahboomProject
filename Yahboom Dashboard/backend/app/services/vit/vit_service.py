"""
VIT Service — MobileCLIP scene decoder and MQTT subscriber.

Subscribes to VIT embedding/status topics on the MQTT broker, decodes
embeddings locally when torch is available, and exposes state via /api/vit/*.
Falls back to yahboom/vit/result when the model is unavailable.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

# ── bootstrap path / env ──────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent          # backend/app/services/vit
_BACKEND_ROOT = _HERE.parents[2]                  # backend/

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_BACKEND_ROOT.parent / ".env")
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
_log_level = os.getenv("VIT_LOG_LEVEL", "INFO").upper()
log = logging.getLogger("vit")
if not log.handlers:
    logging.basicConfig(
        level=getattr(logging, _log_level, logging.INFO),
        format="%(asctime)s [VIT] %(levelname)s  %(message)s",
    )

# ── MQTT / broker ─────────────────────────────────────────────────────────────
_BROKER_PORT      = int(os.getenv("MQTT_BROKER_PORT", "1883"))
_MQTT_TIMEOUT     = int(os.getenv("MQTT_TIMEOUT", "60"))
_EMBEDDING_TOPIC  = os.getenv("MQTT_VIT_EMBEDDING_TOPIC", "yahboom/vit/embedding")
_CLIP_EMBED_TOPIC = os.getenv("MQTT_VIT_CLIP_EMBEDDING_TOPIC", "yahboom/clip_embedding")
# Older webrtc_server.py / robot scripts used slash-less topic names.
_LEGACY_EMBED_TOPIC = os.getenv("MQTT_VIT_LEGACY_EMBEDDING_TOPIC", "yahboomvitembedding")
_STATUS_TOPIC     = os.getenv("MQTT_VIT_STATUS_TOPIC",    "yahboom/vit/status")
_ROBOT_STATUS_TOPIC = os.getenv("MQTT_VIT_ROBOT_STATUS_TOPIC", "yahboom/status")
_LEGACY_STATUS_TOPIC = os.getenv("MQTT_VIT_LEGACY_STATUS_TOPIC", "yahboomvitstatus")
_RESULT_TOPIC     = os.getenv("MQTT_VIT_RESULT_TOPIC",    "yahboom/vit/result")
_CONFIG_TOPIC     = os.getenv("MQTT_VIT_CONFIG_TOPIC",    "yahboom/vit/config")
_COMMAND_TOPIC    = os.getenv("MQTT_VIT_COMMAND_TOPIC",   "yahboom/vit/command")

# Subscribe to legacy + robotsender_mqttv2 topics (deduped, order preserved).
_EMBEDDING_TOPICS = list(dict.fromkeys([
    t for t in (_EMBEDDING_TOPIC, _CLIP_EMBED_TOPIC, _LEGACY_EMBED_TOPIC) if t
]))
_STATUS_TOPICS = list(dict.fromkeys([
    t for t in (_STATUS_TOPIC, _ROBOT_STATUS_TOPIC, _LEGACY_STATUS_TOPIC) if t
]))

# ── Model / decode settings ───────────────────────────────────────────────────
_MODEL_NAME       = os.getenv("VIT_MODEL_NAME", "MobileCLIP-S1")
_MODEL_PRETRAINED = os.getenv("VIT_MODEL_PRETRAINED", "datacompdr")
# Optional override — when unset, embedding dims are auto-detected per payload.
_FORCE_EMBED_DIM: int | None = (
    int(os.environ["VIT_EMBED_DIM"])
    if "VIT_EMBED_DIM" in os.environ
    else None
)
# Must match robot_sender.py EMBEDDING_BYTES.
_EMBEDDING_BYTES_TO_DIMS: dict[int, int] = {512: 128, 1024: 256, 2048: 512}
_ALLOWED_DIMS = frozenset(_EMBEDDING_BYTES_TO_DIMS.values())
_ALLOWED_EMBED_BYTES = frozenset(_EMBEDDING_BYTES_TO_DIMS.keys())
_CONFIDENCE_THRESHOLD = float(os.getenv("VIT_CONFIDENCE_THRESHOLD", "60.0"))
_TOP_K            = int(os.getenv("VIT_TOP_K", "3"))
_ENABLE_MODEL     = os.getenv("VIT_ENABLE_MODEL", "true").lower() in ("true", "1", "yes", "on")
_LABELS_FILE      = Path(os.getenv("VIT_LABELS_FILE", str(_HERE / "labels.json")))

# Session history retention (0 = unlimited).
_SESSION_MAX      = int(os.getenv("VIT_SESSION_MAX", "5000"))

# Placeholder default for the encoded-file-size slider (KB).
_ALLOWED_EMBED_SIZES = (512, 1024, 2048)
_EMBED_SIZE_TO_COMMAND: dict[int, str] = {512: "embds1", 1024: "embds2", 2048: "embds3"}
_EMBED_COMMANDS = frozenset(_EMBED_SIZE_TO_COMMAND.values())
_EMBED_COMMAND_INTERVAL_SEC = float(os.getenv("VIT_EMBED_COMMAND_INTERVAL_SEC", "3"))
_DEFAULT_MAX_FILE_KB = int(os.getenv("VIT_MAX_FILE_KB", "2048"))
# Treat encoder as live if MQTT embeddings/decodes arrived within this window (ms).
_ENCODER_LIVE_MS = int(os.getenv("VIT_ENCODER_LIVE_MS", "8000"))

_DEFAULT_LABELS = [
    "a person sitting and working",
    "a person standing",
    "a person using a phone",
    "no one in the frame",
    "a computer screen or laptop",
    "food or drink",
    "a chair",
    "a table",
    "a wall",
    "a door",
    "an obstacle in front of the robot",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _snap_embed_bytes(value: int) -> int:
    return min(_ALLOWED_EMBED_SIZES, key=lambda n: abs(n - int(value)))


def _is_embed_size_command(payload: bytes) -> bool:
    try:
        return payload.decode("utf-8").strip() in _EMBED_COMMANDS
    except Exception:
        return False


def _load_labels() -> list[str]:
    """Load labels from disk, seeding the default set on first run."""
    try:
        if _LABELS_FILE.exists():
            data = json.loads(_LABELS_FILE.read_text(encoding="utf-8"))
            labels = data.get("labels") if isinstance(data, dict) else data
            if isinstance(labels, list) and labels:
                return [str(x) for x in labels]
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Failed to read labels file: %s", exc)

    try:
        _LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LABELS_FILE.write_text(
            json.dumps({"labels": _DEFAULT_LABELS}, indent=2), encoding="utf-8"
        )
    except Exception:  # pragma: no cover - defensive
        pass
    return list(_DEFAULT_LABELS)


def _infer_target_dims(raw_bytes: bytes, meta: dict | None = None) -> int:
    """
    Infer float32 embedding width from payload bytes (and optional MQTT meta).

    Supports 512 / 1024 / 2048-byte payloads (128 / 256 / 512 dims), matching
    robot_sender.py truncated outputs.
    """
    if _FORCE_EMBED_DIM is not None:
        expected = _FORCE_EMBED_DIM * 4
        if len(raw_bytes) != expected:
            raise ValueError(
                f"Bad embedding size: got {len(raw_bytes)} bytes, expected {expected} "
                f"({_FORCE_EMBED_DIM} dims × 4 bytes). VIT_EMBED_DIM override is set."
            )
        return _FORCE_EMBED_DIM

    nbytes = len(raw_bytes)
    if nbytes in _EMBEDDING_BYTES_TO_DIMS:
        return _EMBEDDING_BYTES_TO_DIMS[nbytes]

    meta_dim = _as_opt_int((meta or {}).get("embedding_dim"))
    if meta_dim is not None and meta_dim in _ALLOWED_DIMS and nbytes == meta_dim * 4:
        return meta_dim

    dims = nbytes // 4
    if dims in _ALLOWED_DIMS:
        return dims

    size_hint = ", ".join(
        f"{b} B ({d} dims)" for b, d in sorted(_EMBEDDING_BYTES_TO_DIMS.items())
    )
    raise ValueError(
        f"Bad embedding size: got {nbytes} bytes, expected one of "
        f"{sorted(_ALLOWED_EMBED_BYTES)} ({size_hint}). "
        f"Check sender EMBEDDING_BYTES matches decoder."
    )


# ═════════════════════════════════════════════════════════════════════════════
# Optional MobileCLIP decoder
# ═════════════════════════════════════════════════════════════════════════════

class MobileClipDecoder:
    """
    Wraps the MobileCLIP text head so raw image embeddings can be classified
    against a label set. Loading torch + open_clip is deferred to a background
    thread so a missing dependency never blocks Flask startup.
    """

    def __init__(self, labels: list[str]) -> None:
        self.labels = list(labels)
        self.ready = False
        self.error: str | None = None
        self._torch = None
        self._model = None
        self._tokenizer = None
        self._text_embeddings_full = None
        self._text_by_dims: dict[int, object] = {}
        self._device = "cpu"
        self._lock = threading.Lock()

    def load(self) -> None:
        """Heavy import + model build. Safe to call from a background thread."""
        try:
            import torch  # type: ignore
            import open_clip  # type: ignore
        except Exception as exc:
            self.error = f"model unavailable ({exc.__class__.__name__})"
            log.info("MobileCLIP decode disabled — %s. Falling back to result topic.", self.error)
            return

        try:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            model, _, _preprocess = open_clip.create_model_and_transforms(
                _MODEL_NAME, pretrained=_MODEL_PRETRAINED, device=self._device,
            )
            tokenizer = open_clip.get_tokenizer(_MODEL_NAME)
            model.eval()

            self._torch = torch
            self._model = model
            self._tokenizer = tokenizer
            self._build_text_embeddings()
            self.ready = True
            log.info("MobileCLIP (%s/%s) ready on %s with %d labels.",
                     _MODEL_NAME, _MODEL_PRETRAINED, self._device, len(self.labels))
        except Exception as exc:
            self.error = f"model load failed: {exc}"
            log.warning("MobileCLIP load failed: %s", exc)

    def _build_text_embeddings(self) -> None:
        torch = self._torch
        with torch.no_grad():
            tokens = self._tokenizer(self.labels).to(self._device)
            emb = self._model.encode_text(tokens)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        self._text_embeddings_full = emb
        self._text_by_dims.clear()

    def _text_embeddings_for_dims(self, target_dims: int):
        """Slice full 512-dim text embeddings to target_dims (cached per width)."""
        if target_dims not in _ALLOWED_DIMS:
            raise ValueError(f"Unsupported embedding dims: {target_dims}")
        cached = self._text_by_dims.get(target_dims)
        if cached is not None:
            return cached
        torch = self._torch
        full = self._text_embeddings_full
        if full is None:
            raise RuntimeError("text embeddings not built")
        with torch.no_grad():
            sliced = full[:, :target_dims]
            sliced = sliced / sliced.norm(dim=-1, keepdim=True)
        self._text_by_dims[target_dims] = sliced
        return sliced

    def set_labels(self, labels: list[str]) -> None:
        with self._lock:
            self.labels = list(labels)
            if self.ready:
                self._build_text_embeddings()

    def decode(
        self,
        raw_bytes: bytes,
        envelope_meta: dict | None = None,
    ) -> tuple[list[tuple[str, float]], int]:
        """Decode raw float32 embedding bytes into (label, confidence%) tuples."""
        if not self.ready:
            raise RuntimeError("decoder not ready")

        target_dims = _infer_target_dims(raw_bytes, envelope_meta)
        expected = target_dims * 4
        if len(raw_bytes) != expected:
            raise ValueError(
                f"Bad embedding size: got {len(raw_bytes)} bytes, expected {expected} "
                f"({target_dims} dims × 4 bytes)."
            )

        torch = self._torch
        import numpy as np  # local import: only needed on the decode path
        emb = np.frombuffer(raw_bytes, dtype=np.float32).reshape(1, -1)

        with self._lock:
            text_embeddings = self._text_embeddings_for_dims(target_dims)
            with torch.no_grad():
                emb_tensor = torch.tensor(emb).to(self._device)
                similarity = (emb_tensor @ text_embeddings.T).squeeze(0)
                probs = torch.softmax(similarity * 100, dim=-1)
                k = min(_TOP_K, len(self.labels))
                top_prob, top_idx = probs.topk(k)
                results = []
                for i, p in zip(top_idx, top_prob):
                    results.append((self.labels[int(i)], round(float(p.item()) * 100, 1)))
        return results, target_dims


# ═════════════════════════════════════════════════════════════════════════════
# VIT Service  (MQTT + background threads; integrates with Flask app)
# ═════════════════════════════════════════════════════════════════════════════

class VITService:
    """
    Subscribes to the VIT MQTT topics, decodes embeddings (when the model is
    available), maintains the latest result + a session history for CSV export,
    and stores the placeholder encoded-file-size limit.
    """

    def __init__(self) -> None:
        try:
            self._client = mqtt.Client(client_id="vit_service")
        except TypeError:  # paho v2 callback API
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1, client_id="vit_service"
            )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._broker_ip: str | None = None
        self.connected = False
        self._loop_running = False
        self._connect_started_at: float = 0.0
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._session: list[dict] = []
        # Most recent status fields (embedding_size / image_file_size) seen on
        # the status topic. Attached to results that don't carry their own.
        self._pending_meta: dict = {}
        self.max_file_size_kb = _DEFAULT_MAX_FILE_KB
        self._requested_embed_bytes: int | None = None
        self._last_received_embed_bytes: int | None = None
        self._embed_cmd_stop = threading.Event()
        self._embed_cmd_thread: threading.Thread | None = None
        self._embed_cmd_lock = threading.Lock()

        # Live activity telemetry (surfaced in /api/vit/status for the widget pill).
        self._embeddings_received = 0
        self._decodes_succeeded = 0
        self._decode_failures = 0
        self._last_embedding_at: str | None = None
        self._last_decode_at: str | None = None
        self._last_status_at: str | None = None
        self._last_decode_error: str | None = None

        # Pi-side robot_sender.py process (SSH start/stop via /api/vit/start_server).
        self.vit_server_running = False

        labels = _load_labels()
        self._decoder = MobileClipDecoder(labels) if _ENABLE_MODEL else None

        self._monitor_thread: threading.Thread | None = None
        self._model_thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_background(self) -> None:
        """Start background threads (called from Flask create_app)."""
        if self._decoder is not None:
            self._model_thread = threading.Thread(
                target=self._decoder.load, daemon=True, name="vit-model-load")
            self._model_thread.start()

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="vit-monitor")
        self._monitor_thread.start()
        log.info("VIT service background threads started.")

    def connect(self, broker_ip: str) -> None:
        """Connect (or reconnect) the VIT MQTT client to a broker."""
        broker_ip = broker_ip.strip()
        if not broker_ip:
            return
        if self._broker_ip == broker_ip and self.connected:
            return
        # Session died without a clean disconnect callback — allow monitor retry.
        if (
            self._broker_ip == broker_ip
            and self._loop_running
            and not self.connected
            and (time.monotonic() - self._connect_started_at) > 8.0
        ):
            log.warning("VIT MQTT stalled — tearing down and reconnecting to %s", broker_ip)
            self._teardown_client()
        # Wait briefly for on_connect after loop_start (avoid connect storms).
        if (
            self._broker_ip == broker_ip
            and self._loop_running
            and not self.connected
            and (time.monotonic() - self._connect_started_at) <= 8.0
        ):
            return
        if self._broker_ip != broker_ip:
            self._teardown_client()
        try:
            if self._loop_running or self.connected:
                self._teardown_client()
            self._client.connect(broker_ip, _BROKER_PORT, _MQTT_TIMEOUT)
            self._client.loop_start()
            self._loop_running = True
            self._broker_ip = broker_ip
            self._connect_started_at = time.monotonic()
            log.info("VIT client connecting to %s:%d", broker_ip, _BROKER_PORT)
        except Exception as exc:
            log.error("VIT broker connection failed: %s", exc)
            self.connected = False
            self._loop_running = False

    def _broker_link_up(self) -> bool:
        """True when the dashboard broker session is up (stable UI signal)."""
        try:
            from app.services.mqtt_service import mqtt_service as ms
            return bool(ms.connected)
        except Exception:
            return self.connected

    def _teardown_client(self) -> None:
        if self._loop_running:
            try:
                self._client.loop_stop()
            except OSError:
                pass
            except Exception:
                pass
            self._loop_running = False
        if self.connected:
            try:
                self._client.disconnect()
            except OSError:
                pass
            except Exception:
                pass
            self.connected = False

    def stop(self) -> None:
        self._stop.set()
        self._teardown_client()

    # ── MQTT callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client, _ud, _flags, rc) -> None:
        if rc == 0:
            for topic in _EMBEDDING_TOPICS:
                client.subscribe(topic, qos=0)
            for topic in _STATUS_TOPICS:
                client.subscribe(topic, qos=0)
            client.subscribe(_RESULT_TOPIC, qos=0)
            self.connected = True
            log.info(
                "VIT MQTT connected (rc=%d). Subscribed to embeddings %s, status %s, %s.",
                rc, _EMBEDDING_TOPICS, _STATUS_TOPICS, _RESULT_TOPIC,
            )
        else:
            log.warning("VIT MQTT connect failed rc=%d", rc)
            self.connected = False
            self._loop_running = False

    def _on_disconnect(self, _client, _ud, _rc) -> None:
        self.connected = False
        self._loop_running = False
        if _rc != 0:
            log.warning("VIT MQTT disconnected (rc=%d) — monitor will reconnect", _rc)

    def _on_message(self, _client, _ud, message) -> None:
        topic = message.topic

        if topic in _EMBEDDING_TOPICS:
            self._handle_embedding(message.payload)
            return

        if topic in _STATUS_TOPICS:
            self._handle_status(message.payload)
            return

        if topic == _RESULT_TOPIC:
            self._handle_result(message.payload)
            return

    def _handle_embedding(self, payload: bytes) -> None:
        """
        Decode embedding bytes locally (only when the model is loaded), record for
        the widget/CSV, and re-publish to the result topic.

        Accepts legacy raw float32 payloads or robotsender_mqttv2 JSON envelopes:
        {"raw_bytes", "embedding_dim", "data": "<base64>", "image_file_size", ...}
        """
        if _is_embed_size_command(payload):
            return

        raw_bytes, envelope_meta = _parse_embedding_payload(payload)
        received_size = len(raw_bytes)
        with self._lock:
            self._last_received_embed_bytes = received_size
            requested = self._requested_embed_bytes
        if requested is not None and received_size == requested:
            self._stop_embedding_command_loop()

        with self._lock:
            self._embeddings_received += 1
            first_embedding = self._embeddings_received == 1
            self._last_embedding_at = _now_iso()
            if envelope_meta:
                self._pending_meta.update(envelope_meta)
        if first_embedding:
            log.info(
                "First VIT embedding received (%d bytes, meta keys: %s)",
                len(raw_bytes),
                sorted(envelope_meta.keys()) if envelope_meta else [],
            )

        decoder = self._decoder
        if decoder is None or not decoder.ready:
            # No local model: results arrive via the result topic instead.
            return
        try:
            results, embedding_dim = decoder.decode(
                raw_bytes, envelope_meta=envelope_meta,
            )
        except Exception as exc:
            with self._lock:
                self._decode_failures += 1
                err = self._sanitize_decode_error(str(exc)[:120])
                if err:
                    self._last_decode_error = err
            log.debug("Embedding decode failed: %s", exc)
            return

        emb_size = envelope_meta.get("embedding_size") or len(raw_bytes)
        img_size = envelope_meta.get("image_file_size")
        with self._lock:
            self._decodes_succeeded += 1
            self._last_decode_at = _now_iso()
            self._last_decode_error = None
        self._record(
            results,
            embedding_size=emb_size,
            embedding_dim=embedding_dim,
            image_file_size=img_size,
            source="embedding",
        )
        self._publish_result(
            results,
            embedding_size=emb_size,
            embedding_dim=embedding_dim,
            image_file_size=img_size,
        )

    def _publish_result(
        self,
        results: list[tuple[str, float]],
        embedding_size: Optional[int] = None,
        embedding_dim: Optional[int] = None,
        image_file_size: Optional[int] = None,
    ) -> None:
        """
        Publish a decoded result to ``yahboom/vit/result`` in the same shape as
        the original client receiver, so other MQTT subscribers (robot behaviour
        nodes, other clients) keep working without the external script running.
        """
        if not results:
            return
        top_label, top_conf = results[0]
        with self._lock:
            if image_file_size is None:
                image_file_size = self._pending_meta.get("image_file_size")
        msg = {
            "top_label": top_label,
            "top_confidence": top_conf,
            "alert": top_conf < _CONFIDENCE_THRESHOLD,
            "results": [{"label": l, "confidence": c} for l, c in results],
            "timestamp": time.time(),
        }
        if embedding_size is not None:
            msg["embedding_size"] = embedding_size
        if embedding_dim is not None:
            msg["embedding_dim"] = embedding_dim
        if image_file_size is not None:
            msg["image_file_size"] = image_file_size
        try:
            self._client.publish(_RESULT_TOPIC, json.dumps(msg), qos=0)
        except Exception as exc:
            log.debug("Failed to publish VIT result: %s", exc)

    def _handle_result(self, payload: bytes) -> None:
        """Ingest a pre-decoded result JSON (fallback when no local model)."""
        # When the local model is decoding embeddings, ignore the result topic
        # to avoid recording each frame twice.
        if self._decoder is not None and self._decoder.ready:
            return
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            return
        if not isinstance(obj, dict):
            return

        results: list[tuple[str, float]] = []
        raw_results = obj.get("results")
        if isinstance(raw_results, list):
            for item in raw_results:
                if isinstance(item, dict) and "label" in item:
                    results.append((str(item["label"]),
                                    _as_float(item.get("confidence"))))
        if not results and obj.get("top_label") is not None:
            results.append((str(obj["top_label"]),
                            _as_float(obj.get("top_confidence"))))
        if not results:
            return

        emb_size = _as_opt_int(obj.get("embedding_size"))
        emb_dim = _as_opt_int(obj.get("embedding_dim"))
        img_size = _as_opt_int(
            obj.get("image_file_size")
            or obj.get("image_size")
            or obj.get("original_image_size")
        )
        with self._lock:
            self._decodes_succeeded += 1
            self._last_decode_at = _now_iso()
            self._last_decode_error = None
        self._record(
            results,
            embedding_size=emb_size,
            embedding_dim=emb_dim,
            image_file_size=img_size,
            source="result",
        )

    def _handle_status(self, payload: bytes) -> None:
        """
        Capture status JSON. The updated Pi script will publish
        ``embedding_size`` and ``image_file_size`` here; both are stored so the
        next recorded result can attach them.
        """
        try:
            obj = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            return
        if not isinstance(obj, dict):
            return

        meta = _meta_from_envelope(obj)
        if not meta:
            return
        with self._lock:
            self._pending_meta.update(meta)
            self._last_status_at = _now_iso()

    # ── Recording / state ─────────────────────────────────────────────────────

    def _record(
        self,
        results: list[tuple[str, float]],
        embedding_size: Optional[int] = None,
        embedding_dim: Optional[int] = None,
        image_file_size: Optional[int] = None,
        source: str = "embedding",
    ) -> None:
        if not results:
            return
        top_label, top_conf = results[0]
        alert = top_conf < _CONFIDENCE_THRESHOLD
        ts = _now_iso()

        with self._lock:
            meta = dict(self._pending_meta)
            emb = embedding_size if embedding_size is not None else meta.get("embedding_size")
            dim = embedding_dim if embedding_dim is not None else meta.get("embedding_dim")
            if dim is None and emb in _EMBEDDING_BYTES_TO_DIMS:
                dim = _EMBEDDING_BYTES_TO_DIMS[emb]
            img = image_file_size if image_file_size is not None else meta.get("image_file_size")

            self._latest = {
                "top_label": top_label,
                "top_confidence": top_conf,
                "alert": alert,
                "results": [{"label": l, "confidence": c} for l, c in results],
                "embedding_size": emb,
                "embedding_dim": dim,
                "image_file_size": img,
                "source": source,
                "timestamp": ts,
            }

            self._session.append({
                "timestamp": ts,
                "detected_object": top_label,
                "confidence": top_conf,
                "embedding_size": emb,
                "embedding_dim": dim,
                "image_file_size": img,
            })
            if _SESSION_MAX > 0 and len(self._session) > _SESSION_MAX:
                self._session = self._session[-_SESSION_MAX:]

        try:
            from app.services.vit.edge_aware_estop import edge_aware_estop
            edge_aware_estop.on_vit_results(results)
        except Exception as exc:
            log.debug("Edge-aware estop hook failed: %s", exc)

    def _encoder_live_locked(self) -> bool:
        """True when recent MQTT proves the encoder pipeline is producing data."""
        try:
            from app.services.mqtt_service import mqtt_service as ms
            server_on = bool(ms.stream_running or self.vit_server_running)
        except Exception:
            server_on = bool(self.vit_server_running)
        for ts in (self._last_embedding_at, self._last_decode_at, self._last_status_at):
            age = _iso_age_ms(ts)
            if age is not None and age < _ENCODER_LIVE_MS:
                return True
        # Server running but between embedding bursts — avoid "server off" flicker.
        if server_on and self._embeddings_received > 0:
            return True
        return False

    # ── Public API (used by Flask routes) ─────────────────────────────────────

    def get_status(self) -> dict:
        decoder = self._decoder
        try:
            from app.services.mqtt_service import mqtt_service as ms
            stream_on = bool(ms.stream_running)
        except Exception:
            stream_on = False
        with self._lock:
            encoder_live = self._encoder_live_locked()
            # Hide stale detections only when the encoder pipeline is fully idle.
            latest = (
                dict(self._latest)
                if self._latest and encoder_live
                else None
            )
            session_count = len(self._session)
            activity = {
                "embeddings_received": self._embeddings_received,
                "decodes_succeeded": self._decodes_succeeded,
                "decode_failures": self._decode_failures,
                "last_embedding_at": self._last_embedding_at,
                "last_decode_at": self._last_decode_at,
                "last_status_at": self._last_status_at,
                "last_decode_error": self._sanitize_decode_error(self._last_decode_error),
            }
        server_on = stream_on or self.vit_server_running
        return {
            "connected": self._broker_link_up(),
            "broker_ip": self._broker_ip,
            "vit_server_running": server_on,
            "encoder_live": encoder_live,
            "model_enabled": decoder is not None,
            "model_ready": bool(decoder and decoder.ready),
            "model_error": decoder.error if decoder else "model disabled",
            "confidence_threshold": _CONFIDENCE_THRESHOLD,
            "max_file_size_kb": self.max_file_size_kb,
            "requested_embedding_bytes": self._requested_embed_bytes,
            "embedding_command_active": self._embedding_command_active(),
            "session_count": session_count,
            "latest": latest,
            "activity": activity,
        }

    def _embedding_command_active(self) -> bool:
        with self._embed_cmd_lock:
            return (
                self._embed_cmd_thread is not None
                and self._embed_cmd_thread.is_alive()
            )

    def _publish_embed_size_command(self, command: str) -> None:
        """Publish embds1/2/3 via the main MQTT client (same path as movement commands)."""
        from app.services.mqtt_service import mqtt_service as ms
        from config import DEFAULT_BROKER_IP, PUBLISH_TIMEOUT

        if not ms.connected:
            reconnect_ip = ms.broker_ip or DEFAULT_BROKER_IP
            success, _message = ms.connect_to_broker(reconnect_ip)
            if not success:
                ms.log_event(
                    "warning",
                    f"MQTT broker not connected — skipped {_COMMAND_TOPIC}: {command}",
                    tag=_COMMAND_TOPIC,
                )
                return

        try:
            result = ms.mqtt_client.publish(_COMMAND_TOPIC, command)
            if PUBLISH_TIMEOUT > 0:
                result.wait_for_publish(timeout=PUBLISH_TIMEOUT)
            ms.log_event("info", f"MQTT -> {_COMMAND_TOPIC}: {command}", tag=_COMMAND_TOPIC)
        except Exception as exc:
            ms.connected = False
            ms.log_event("error", f"Publish failed: {exc}", tag=_COMMAND_TOPIC)

    def _stop_embedding_command_loop(self) -> None:
        self._embed_cmd_stop.set()
        with self._embed_cmd_lock:
            thread = self._embed_cmd_thread
            self._embed_cmd_thread = None
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.5)

    def _embedding_command_loop(self, size_bytes: int, command: str) -> None:
        """Re-publish embds1/2/3 every 3s until ack or server stop (first send is sync)."""
        try:
            while not self._embed_cmd_stop.wait(timeout=_EMBED_COMMAND_INTERVAL_SEC):
                if not self.vit_server_running:
                    break
                with self._lock:
                    if (
                        self._requested_embed_bytes == size_bytes
                        and self._last_received_embed_bytes == size_bytes
                    ):
                        break
                self._publish_embed_size_command(command)
        finally:
            with self._embed_cmd_lock:
                if self._embed_cmd_thread is threading.current_thread():
                    self._embed_cmd_thread = None

    def stop_embedding_size_requests(self) -> None:
        """Stop embds heartbeat (server stopped or disconnect)."""
        with self._lock:
            self._requested_embed_bytes = None
        self._stop_embedding_command_loop()

    def set_max_file_size(self, kb: int) -> int:
        """
        Store the widget slider value (512 / 1024 / 2048 B) and request that
        embedding size from the Pi via embds1/embds2/embds3 on the command topic.
        """
        size_bytes = _snap_embed_bytes(kb)
        self.max_file_size_kb = size_bytes
        with self._lock:
            self._requested_embed_bytes = size_bytes
        self._stop_embedding_command_loop()
        command = _EMBED_SIZE_TO_COMMAND[size_bytes]
        self._embed_cmd_stop = threading.Event()
        # Publish immediately on slider change (same synchronous path as /api/send_command).
        self._publish_embed_size_command(command)
        thread = threading.Thread(
            target=self._embedding_command_loop,
            args=(size_bytes, command),
            daemon=True,
            name="vit-embed-cmd",
        )
        with self._embed_cmd_lock:
            self._embed_cmd_thread = thread
        thread.start()
        log.info(
            "Requesting Pi embedding size %d B (%s on %s)",
            size_bytes, command, _COMMAND_TOPIC,
        )
        try:
            from app.services.mqtt_service import mqtt_service as ms
            from config import PUBLISH_TIMEOUT
            if ms.connected:
                result = ms.mqtt_client.publish(
                    _CONFIG_TOPIC,
                    json.dumps({"embedding_size_bytes": size_bytes, "command": command}),
                )
                if PUBLISH_TIMEOUT > 0:
                    result.wait_for_publish(timeout=PUBLISH_TIMEOUT)
        except Exception as exc:
            log.debug("Failed to publish VIT config: %s", exc)
        return size_bytes

    def clear_session(self) -> None:
        with self._lock:
            self._session.clear()
            self._latest = None

    def export_csv(self) -> str:
        """Render the session history as CSV text."""
        with self._lock:
            rows = list(self._session)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "timestamp",
            "detected_object",
            "confidence_percent",
            "embedding_size_bytes",
            "embedding_dim",
            "original_image_file_size_bytes",
        ])
        for r in rows:
            writer.writerow([
                r.get("timestamp", ""),
                r.get("detected_object", ""),
                r.get("confidence", ""),
                r.get("embedding_size") if r.get("embedding_size") is not None else "",
                r.get("embedding_dim") if r.get("embedding_dim") is not None else "",
                r.get("image_file_size") if r.get("image_file_size") is not None else "",
            ])
        return buf.getvalue()

    # ── Background thread ──────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_decode_error(msg: str | None) -> str | None:
        if not msg:
            return None
        lower = msg.lower()
        if "errno 22" in lower or "invalid argument" in lower:
            return None
        return msg

    def _monitor_loop(self) -> None:
        """Mirror the main MQTTService broker connection (connect once)."""
        while not self._stop.wait(timeout=1.5):
            try:
                from app.services.mqtt_service import mqtt_service as ms
                if ms.connected and ms.broker_ip:
                    if not self.connected:
                        self.connect(ms.broker_ip)
            except Exception:
                pass


def _iso_age_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        return int((time.time() - ts) * 1000)
    except (TypeError, ValueError):
        return None


def _meta_from_envelope(obj: dict) -> dict:
    """Extract embedding_size / image_file_size from MQTT JSON (envelope or status)."""
    meta: dict = {}
    emb = (
        _as_opt_int(obj.get("raw_bytes"))
        or _as_opt_int(obj.get("embedding_size"))
        or _as_opt_int(obj.get("embedding_size_bytes"))
    )
    if emb is not None:
        meta["embedding_size"] = emb
    else:
        shape = obj.get("embedding_shape")
        if isinstance(shape, list) and shape:
            try:
                elems = 1
                for dim in shape:
                    elems *= int(dim)
                meta["embedding_size"] = elems * 4  # float32 bytes
            except (TypeError, ValueError):
                pass
    dim = _as_opt_int(obj.get("embedding_dim"))
    if dim is not None:
        meta["embedding_dim"] = dim
    elif emb is not None and emb in _EMBEDDING_BYTES_TO_DIMS:
        meta["embedding_dim"] = _EMBEDDING_BYTES_TO_DIMS[emb]

    img = _as_opt_int(
        obj.get("image_file_size")
        or obj.get("image_payload_size_bytes")
        or obj.get("original_image_size")
        or obj.get("image_size")
        or obj.get("jpeg_size")
        or obj.get("encoded_image_bytes")
    )
    if img is not None:
        meta["image_file_size"] = img
    else:
        # Optional base64-encoded JPEG in the envelope — use decoded byte length.
        for key in ("image_data", "jpeg_data", "encoded_image", "image_b64"):
            blob = obj.get(key)
            if isinstance(blob, str) and blob:
                try:
                    meta["image_file_size"] = len(base64.b64decode(blob))
                    break
                except Exception:
                    pass
    return meta


def _parse_embedding_payload(payload: bytes) -> tuple[bytes, dict]:
    """
    Parse robotsender_mqttv2 JSON envelopes or return legacy raw float32 bytes.

    v2 envelope example::
        {"raw_bytes": 2048, "embedding_dim": 512, "data": "<base64>", ...}
    """
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload, {"embedding_size": len(payload)}

    if not isinstance(obj, dict):
        return payload, {"embedding_size": len(payload)}

    meta = _meta_from_envelope(obj)
    data = obj.get("data")
    if isinstance(data, str):
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            log.debug("Invalid base64 in embedding envelope")
            return payload, meta
        if "embedding_size" not in meta:
            meta["embedding_size"] = len(raw)
        return raw, meta

    return payload, meta


def _as_float(value) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _as_opt_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ── Global singleton (used by Flask routes) ───────────────────────────────────
vit_service = VITService()
