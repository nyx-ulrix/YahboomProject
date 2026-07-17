"""
YOLOv8 object detection on the live Pi video relay.

Uses Ultralytics YOLOv8 weights from the Hugging Face model hub:
https://huggingface.co/Ultralytics/YOLOv8
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import numpy as np

from config import (
    YOLO_CONFIDENCE,
    YOLO_ENABLED,
    YOLO_HF_REPO,
    YOLO_IMGSZ,
    YOLO_INFERENCE_INTERVAL_SEC,
    YOLO_MODEL_FILE,
    YOLO_READINGS_STALE_MS,
    VIDEO_SERVER_PORT,
)

log = logging.getLogger("yolo")

_MAX_SESSION = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_age_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        return int((time.time() - ts) * 1000)
    except (TypeError, ValueError):
        return None


class YoloService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._model_error: str | None = None
        self._model_path: str | None = None
        self._enabled = YOLO_ENABLED
        self._confidence = YOLO_CONFIDENCE
        self._imgsz = YOLO_IMGSZ
        self._interval_sec = max(0.1, YOLO_INFERENCE_INTERVAL_SEC)
        self._last_infer_at = 0.0
        self._last_frame_at: str | None = None
        self._last_mqtt_frame_at = 0.0
        self._latest: dict[str, Any] | None = None
        self._session: list[dict[str, Any]] = []
        self._video_active = False
        self._inference_count = 0
        self._started = False
        self._frame_listener = self._on_frame
        self._detection_mode = "cloud_aware"
        self._frame_poll_stop = threading.Event()
        self._frame_poll_thread: threading.Thread | None = None
        self._last_mqtt_processed_at = 0.0

    def _is_inference_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def sync_detection_mode(self, mode: str) -> bool:
        """Run YOLO inference only in cloud_aware (YOLO) test-bench mode."""
        if mode == "edge_aware":
            mode = "cloud_aware"
        should_run = mode == "cloud_aware" and YOLO_ENABLED
        with self._lock:
            prev_mode = self._detection_mode
            was_enabled = self._enabled
            self._detection_mode = mode
        if not should_run:
            self.clear_session()
        enabled = self.set_enabled(should_run)
        if enabled:
            self._ensure_frame_poll_thread()
        if prev_mode != mode or was_enabled != enabled:
            if enabled:
                log.info("YOLO running — detection mode %s", mode)
            else:
                log.info("YOLO paused — detection mode %s", mode)
        return enabled

    def start_background(self) -> None:
        if self._started:
            return
        self._started = True
        from app.services.video_relay import video_relay
        video_relay.add_frame_listener(self._frame_listener)
        try:
            from app.services.vit.vit_service import vit_service
            self.sync_detection_mode(vit_service.get_detection_mode())
        except Exception:
            pass
        if self._is_inference_enabled():
            threading.Thread(
                target=self._load_model_async,
                name="yolo-model-load",
                daemon=True,
            ).start()
        self._ensure_frame_poll_thread()

    def _ensure_frame_poll_thread(self) -> None:
        if self._frame_poll_thread and self._frame_poll_thread.is_alive():
            return
        self._frame_poll_stop.clear()
        self._frame_poll_thread = threading.Thread(
            target=self._pi_frame_poll_loop,
            name="yolo-pi-frame-poll",
            daemon=True,
        )
        self._frame_poll_thread.start()

    def _pi_host(self) -> str | None:
        try:
            from app.services.mqtt_service import mqtt_service
            from config import DEFAULT_BROKER_IP

            return mqtt_service.broker_ip or DEFAULT_BROKER_IP
        except Exception:
            return None

    def _pi_frame_poll_loop(self) -> None:
        """HTTP fallback: grab /frame.jpg from webrtc_server when MQTT is quiet."""
        while not self._frame_poll_stop.is_set():
            time.sleep(max(0.2, self._interval_sec))
            if not self._is_inference_enabled():
                continue
            if time.monotonic() - self._last_mqtt_processed_at < 2.0:
                continue
            host = self._pi_host()
            if not host:
                continue
            url = f"http://{host}:{VIDEO_SERVER_PORT}/frame.jpg"
            try:
                with urllib.request.urlopen(url, timeout=4) as resp:
                    if resp.status >= 400:
                        continue
                    jpeg = resp.read()
            except (urllib.error.URLError, OSError, TimeoutError, ValueError):
                continue
            if not jpeg:
                continue
            self._apply_frame_hop_delay()
            self._process_jpeg(jpeg)

    def _load_model_async(self) -> None:
        try:
            self._ensure_model()
        except Exception as exc:
            log.warning("YOLO model load failed: %s", exc)

    def _resolve_weights_path(self) -> str:
        """Load YOLOv8 weights from Ultralytics/YOLOv8 on Hugging Face when available."""
        try:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(repo_id=YOLO_HF_REPO, filename=YOLO_MODEL_FILE)
            log.info("Loaded YOLOv8 weights from Hugging Face %s (%s)", YOLO_HF_REPO, YOLO_MODEL_FILE)
            return path
        except Exception as exc:
            log.warning(
                "Hugging Face download failed (%s); using Ultralytics hub for %s",
                exc,
                YOLO_MODEL_FILE,
            )
            return YOLO_MODEL_FILE

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from ultralytics import YOLO

                weights = self._resolve_weights_path()
                model = YOLO(weights)
                self._model = model
                self._model_path = weights
                self._model_error = None
                log.info("YOLOv8 ready (%s)", weights)
            except Exception as exc:
                self._model_error = str(exc)
                log.error("Failed to load YOLOv8: %s", exc)
                raise

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self._enabled = enabled
        if enabled and self._model is None:
            threading.Thread(
                target=self._load_model_async,
                name="yolo-model-load",
                daemon=True,
            ).start()
        if enabled:
            self._ensure_frame_poll_thread()
        return self._enabled

    def set_confidence(self, value: float) -> float:
        conf = max(0.01, min(1.0, float(value)))
        with self._lock:
            self._confidence = conf
        return conf

    def clear_session(self) -> None:
        with self._lock:
            self._session.clear()
            self._latest = None
            self._last_frame_at = None
            self._video_active = False

    def _note_frame(self) -> None:
        self._last_frame_at = _now_iso()
        self._video_active = True

    def _frame_input_fresh(self) -> bool:
        if self._last_frame_at is None:
            return False
        age = _iso_age_ms(self._last_frame_at)
        return age is not None and age < YOLO_READINGS_STALE_MS

    def _expire_stale_readings(self) -> None:
        with self._lock:
            if self._latest is not None and not self._frame_input_fresh():
                self._latest = None
            if not self._frame_input_fresh():
                self._video_active = False

    @staticmethod
    def _apply_frame_hop_delay() -> float:
        """Simulate wired-backhaul delay on YOLO frame ingest when hops are enabled."""
        try:
            from app.services.backhaul_delay import backhaul_delay

            return backhaul_delay.apply()
        except Exception:
            return 0.0

    def handle_mqtt_frame(self, payload: dict[str, Any]) -> None:
        """Decode a JPEG frame published by webrtc_server.py on yahboom/camera/frame."""
        if not self._is_inference_enabled():
            return
        jpg_b64 = payload.get("jpg_b64")
        if not jpg_b64:
            return
        try:
            jpeg = base64.b64decode(jpg_b64)
        except Exception:
            return
        self._last_mqtt_frame_at = time.monotonic()
        self._apply_frame_hop_delay()
        self._process_jpeg(jpeg)
        self._last_mqtt_processed_at = time.monotonic()

    def _on_frame(self, jpeg: bytes) -> None:
        if not self._is_inference_enabled():
            return
        self._apply_frame_hop_delay()
        self._process_jpeg(jpeg)

    def _process_jpeg(self, jpeg: bytes) -> None:
        if not self._is_inference_enabled():
            return
        self._note_frame()

        now = time.monotonic()
        if now - self._last_infer_at < self._interval_sec:
            return
        self._last_infer_at = now

        try:
            self._ensure_model()
        except Exception:
            return

        if self._model is None:
            return

        try:
            import cv2

            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return

            height, width = frame.shape[:2]
            with self._lock:
                conf = self._confidence
                imgsz = self._imgsz

            results = self._model.predict(
                frame,
                verbose=False,
                conf=conf,
                imgsz=imgsz,
            )
            detections = self._parse_results(results[0])
            if not detections:
                # Keep the last reading visible — empty frames are normal between hits.
                return

            top = detections[0]
            payload = {
                "timestamp": _now_iso(),
                "frame_width": width,
                "frame_height": height,
                "detections": detections,
                "top_detection": top,
            }
            with self._lock:
                self._latest = payload
                self._session.append({
                    "timestamp": payload["timestamp"],
                    "top_label": top["label"],
                    "top_confidence": top["confidence_percent"],
                    "detection_count": len(detections),
                })
                if len(self._session) > _MAX_SESSION:
                    self._session = self._session[-_MAX_SESSION:]
                self._inference_count += 1
        except Exception as exc:
            with self._lock:
                self._model_error = str(exc)
            log.warning("YOLO inference error: %s", exc)

    @staticmethod
    def _parse_results(result) -> list[dict[str, Any]]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        names = result.names or {}
        parsed: list[dict[str, Any]] = []
        for box in boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            label = str(names.get(cls_id, f"class_{cls_id}"))
            xyxy = [float(v) for v in box.xyxy[0].tolist()]
            parsed.append({
                "label": label,
                "class_id": cls_id,
                "confidence": conf,
                "confidence_percent": round(conf * 100.0, 1),
                "bbox": xyxy,
            })

        parsed.sort(key=lambda d: d["confidence"], reverse=True)
        return parsed

    def get_status(self) -> dict[str, Any]:
        self._expire_stale_readings()
        with self._lock:
            latest = self._latest
            session_count = len(self._session)
            enabled = self._enabled
            model_ready = self._model is not None
            model_error = self._model_error
            model_path = self._model_path
            confidence = self._confidence
            inference_count = self._inference_count
            last_frame_at = self._last_frame_at
            readings_fresh = self._frame_input_fresh()
            detection_mode = self._detection_mode
            paused_for_cache_aware = detection_mode == "cache_aware_offloading"
        try:
            from app.services.video_relay import video_relay
            stream_relay_active = video_relay.is_active()
        except Exception:
            stream_relay_active = False

        return {
            "enabled": enabled,
            "detection_mode": detection_mode,
            "paused_for_cache_aware": paused_for_cache_aware,
            "stream_relay_active": stream_relay_active,
            "model_ready": model_ready,
            "model_error": model_error,
            "model_file": YOLO_MODEL_FILE,
            "model_repo": YOLO_HF_REPO,
            "model_path": model_path,
            "model_family": "YOLOv8",
            "video_active": readings_fresh,
            "readings_fresh": readings_fresh,
            "last_frame_at": last_frame_at,
            "readings_stale_ms": YOLO_READINGS_STALE_MS,
            "confidence_threshold": confidence,
            "confidence_threshold_percent": round(confidence * 100.0, 1),
            "inference_interval_sec": self._interval_sec,
            "inference_count": inference_count,
            "latest": latest,
            "detection_count": len(latest["detections"]) if latest else 0,
            "session_count": session_count,
        }


yolo_service = YoloService()
