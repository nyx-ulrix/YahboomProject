# VIT.py
# This script:
# 1. Receives camera frames from webrtc_server.py through MQTT
# 2. Runs MobileCLIP-S1 embedding on the Yahboom / Raspberry Pi
# 3. Cache-aware OFF by default when script starts
# 4. Client sends Cao_ON  -> cache-aware becomes ON
# 5. Client sends Cao_OFF -> cache-aware becomes OFF
# 6. When cache-aware is ON:
#       - live embedding is compared with /home/pi/cache_embeddings.json
#       - cache confidence is printed in the terminal
#       - if cache MISS: embedding is published to cloud/client
#       - if cache HIT: embedding is NOT sent to cloud/client
#       - if HIT is stable for required frames: robot receives stop command
#       - cache-aware stays ON until Cao_OFF is received

import os
from pathlib import Path

# =========================
# TEMP FILE LOCATION
# =========================
TEMP_DIR = Path("/home/pi/tmp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

TORCH_CACHE_DIR = TEMP_DIR / "torchinductor"
TORCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

os.environ["TMPDIR"] = str(TEMP_DIR)
os.environ["TEMP"] = str(TEMP_DIR)
os.environ["TMP"] = str(TEMP_DIR)
os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(TORCH_CACHE_DIR)

import base64
import json
import threading
import time

import cv2
import numpy as np
import open_clip
import paho.mqtt.client as mqtt
import torch
from PIL import Image


# =========================
# SETTINGS
# =========================
BROKER_IP = "localhost"
BROKER_PORT = 1883

CACHE_FILE_PATH = "/home/pi/cache_embeddings.json"

# VIT.py listens to this topic for cache-aware commands:
#   Cao_ON
#   Cao_OFF
#   embds1 / embds2 / embds3
TOPIC_COMMAND = "yahboom/cmd"

# Dashboard publishes stop-similarity percent (1–100) as plain text on this topic.
TOPIC_COSSIM = "yahboom/cossim"

# VIT.py receives camera frames from webrtc_server.py here
TOPIC_CAMERA_FRAME = "yahboom/camera/frame"

# VIT.py publishes embeddings here only when cloud/client should check
TOPIC_CLIP = "yahboom/vit/embedding"

# VIT.py status topics
TOPIC_STATUS = "yahboom/vit/status"
TOPIC_DETECT = "yahboom/detect/status"
TOPIC_READY = "yahboom/cache_aware/ready"
TOPIC_CLIENT_READY = "yahboom/cache_aware/client"
TOPIC_AUTO_STATUS = "yahboom/auto/status"
TOPIC_CACHE_EVENT = "yahboom/cache_aware/event"

# Robot movement command topic.
# IMPORTANT:
# Stop commands go here, NOT to yahboom/vit/command.
# This prevents VIT.py from receiving its own stop commands.
TOPIC_ROBOT_COMMAND = "yahboom/cmd"

INFERENCE_EVERY_N_FRAMES = 5
SHOW_PREVIEW = False

DETECTION_LABEL = "bottle"
DEFAULT_DETECTION_THRESHOLD = 0.70

# Bottle must be cache-hit this many times before sending robot stop.
CONSECUTIVE_HITS_REQUIRED = 3

# After a detection, don't trigger another stop until the bottle is gone
# for this many cache-miss checks.
UNLATCH_MISSES_REQUIRED = 5

DETECTION_COOLDOWN_S = 2.0
STOP_REPEAT_COUNT = 8
STOP_REPEAT_DELAY_S = 0.05
READY_HEARTBEAT_S = 2.0
LOG_EVERY_N_FRAMES = 50

# 1 = print every cache check.
# Change to 5 or 10 if terminal becomes too noisy.
CACHE_CONFIDENCE_LOG_EVERY_N_CHECKS = 1


# =========================
# EMBEDDING SETTINGS
# =========================
EMBEDDING_BYTES_TO_DIMS = {
    512: 128,
    1024: 256,
    2048: 512,
}

COMMAND_TO_EMBEDDING_BYTES = {
    "embds1": 512,
    "embds2": 1024,
    "embds3": 2048,
}

_current_embedding_bytes = 2048
_current_target_dims = 512
embedding_lock = threading.Lock()


def get_target_dims():
    with embedding_lock:
        return _current_target_dims


def get_embedding_bytes():
    with embedding_lock:
        return _current_embedding_bytes


def set_embedding_size(new_bytes):
    global _current_embedding_bytes, _current_target_dims

    if new_bytes not in EMBEDDING_BYTES_TO_DIMS:
        print(f"[CMD] Invalid embedding bytes: {new_bytes}")
        return False

    with embedding_lock:
        _current_embedding_bytes = new_bytes
        _current_target_dims = EMBEDDING_BYTES_TO_DIMS[new_bytes]

    print(
        f"[CMD] Embedding switched -> {new_bytes} bytes "
        f"| {_current_target_dims} dims"
    )

    return True


# =========================
# CACHE-AWARE COMMANDS
# =========================
CAO_ON_COMMAND = "cao_on"
CAO_OFF_COMMAND = "cao_off"
CAO_READY_COMMAND = "Cao_Ready"
CAO_NOT_READY_COMMAND = "Cao_NotReady"

AUTO_OFF_COMMAND = "auto_off"
STOP_COMMAND = "stop"

# Only   turns cache-aware ON.
# auto_on is NOT included here on purpose.
START_COMMANDS = {
    "cao_on",
}

# Only Cao_OFF turns cache-aware OFF.
# auto_off is NOT included here on purpose.
STOP_OR_OFF_COMMANDS = {
    "cao_off",
}


# =========================
# GLOBALS
# =========================
latest_frame = None
latest_frame_id = 0
frame_lock = threading.Lock()

stop_event = threading.Event()

model = None
preprocess = None
device = None
mqtt_client = None

frames_received = 0
embeddings_sent = 0

cached_objects = []
cache_ready = False

# This is the actual cache-aware ON/OFF state.
# It starts OFF and only becomes ON after Cao_ON.
test_active = False

# Dashboard can override the minimum cache-hit similarity via TOPIC_COSSIM (plain percent).
_stop_similarity_threshold = DEFAULT_DETECTION_THRESHOLD

_last_detection_time = 0.0
_hit_streak = 0

# Prevent repeated stop spam when bottle remains in view.
_detection_latched = False
_miss_streak_after_latch = 0

_stop_ready_heartbeat = False
_stop_sequence_running = False


# =========================
# LOAD MODEL
# =========================
def load_model():
    global model, preprocess, device

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model, _, preprocess = open_clip.create_model_and_transforms(
        "MobileCLIP-S1",
        pretrained="datacompdr",
        device=device,
    )

    model.eval()
    print(f"[INFO] Loaded MobileCLIP-S1 on {device}")


# =========================
# CACHE HELPERS
# =========================
def normalise_embedding(embedding: np.ndarray) -> np.ndarray:
    embedding = embedding.astype(np.float32)
    norm = np.linalg.norm(embedding)

    if norm <= 1e-12:
        return embedding

    return embedding / norm


def decode_cached_embedding(obj: dict) -> np.ndarray:
    if "data" in obj:
        raw_bytes = base64.b64decode(obj["data"])
        embedding = np.frombuffer(raw_bytes, dtype=np.float32).copy()
    elif "embedding" in obj:
        embedding = np.array(obj["embedding"], dtype=np.float32)
    else:
        raise ValueError("Cached object has no 'data' or 'embedding' field.")

    return normalise_embedding(embedding)


def load_cached_embeddings():
    global cached_objects, cache_ready

    cache_path = Path(CACHE_FILE_PATH)

    if not cache_path.exists():
        raise FileNotFoundError(f"Cache file not found: {CACHE_FILE_PATH}")

    with open(cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)

    objects = cache.get("objects", [])

    if not objects:
        raise ValueError("No objects found inside cache JSON.")

    loaded = []

    for obj in objects:
        label = obj.get("label", "unknown")

        if label != DETECTION_LABEL:
            continue

        embedding = decode_cached_embedding(obj)
        declared_dim = int(obj.get("embedding_dim", len(embedding)))

        if declared_dim != len(embedding):
            raise ValueError(
                f"Embedding dimension mismatch for '{label}'. "
                f"JSON says {declared_dim}, decoded embedding has {len(embedding)}."
            )

        threshold = float(obj.get("threshold", DEFAULT_DETECTION_THRESHOLD))

        loaded.append({
            "label": label,
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "threshold": threshold,
            "model": obj.get("model", "unknown"),
            "pretrained": obj.get("pretrained", "unknown"),
            "source_image": obj.get("source_image", "unknown"),
        })

    if not loaded:
        raise ValueError(
            f"No cached embedding found for label '{DETECTION_LABEL}'. "
            f"Check {CACHE_FILE_PATH}."
        )

    cached_objects = loaded
    cache_ready = True

    print(f"[CACHE] Loaded {len(cached_objects)} cached embedding(s).")

    for obj in cached_objects:
        print(
            f"[CACHE] label={obj['label']} | "
            f"dims={obj['embedding_dim']} | "
            f"threshold={obj['threshold']} | "
            f"model={obj['model']} | "
            f"source={obj['source_image']}"
        )


def check_detection(live_embedding: np.ndarray):
    """
    Compare live embedding against stored embeddings from cache_embeddings.json.

    Returns:
        best_match: cached object dict or None
        best_similarity: cosine/dot-product similarity
    """
    if not cache_ready or not cached_objects:
        return None, -1.0

    live_embedding = normalise_embedding(live_embedding)

    best_match = None
    best_similarity = -1.0

    for obj in cached_objects:
        cached_embedding = obj["embedding"]

        if len(live_embedding) != obj["embedding_dim"]:
            print(
                f"[WARN] Dimension mismatch. "
                f"Live={len(live_embedding)}, Cache={obj['embedding_dim']}"
            )
            continue

        similarity = float(np.dot(live_embedding, cached_embedding))

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = obj

    return best_match, best_similarity


# =========================
# GET LIVE EMBEDDING
# =========================
def get_embedding(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    img_tensor = preprocess(pil_img).unsqueeze(0).to(device)

    with torch.no_grad():
        emb = model.encode_image(img_tensor.float())

    emb = emb / emb.norm(dim=-1, keepdim=True)

    target_dims = get_target_dims()
    emb = emb[:, :target_dims]

    emb = emb / emb.norm(dim=-1, keepdim=True)

    return emb.cpu().numpy().astype(np.float32)


# =========================
# STATUS HELPERS
# =========================
def publish_status(status, extra=None):
    if mqtt_client is None:
        return

    payload = {
        "status": status,
        "timestamp": time.time(),
    }

    if extra:
        payload.update(extra)

    try:
        mqtt_client.publish(TOPIC_STATUS, json.dumps(payload), qos=0)
    except Exception as e:
        print(f"[MQTT] Failed to publish status: {e}")


def publish_cache_ready(ready: bool, reason: str, wait: bool = False, log: bool = True):
    if mqtt_client is None:
        return

    ready_cmd = CAO_READY_COMMAND if ready else CAO_NOT_READY_COMMAND

    payload = {
        "ready": bool(ready),
        "cache_ready": bool(ready),
        "embedding_ready": bool(ready),
        "cache_aware_ready": bool(ready),
        "status": "ready" if ready else "not_ready",
        "command": ready_cmd,
        "script": "VIT.py",
        "mode": "cached_embedding",
        "label": DETECTION_LABEL,
        "cache_file": CACHE_FILE_PATH,
        "cached_count": len(cached_objects),
        "test_active": bool(test_active),
        "requires_cao_on": True,
        "reason": reason,
        "timestamp": time.time(),
    }

    result = mqtt_client.publish(
        TOPIC_READY,
        json.dumps(payload),
        qos=1,
        retain=True
    )

    mqtt_client.publish(
        TOPIC_CLIENT_READY,
        ready_cmd,
        qos=1,
        retain=True
    )

    if wait:
        try:
            result.wait_for_publish(timeout=2.0)
        except TypeError:
            result.wait_for_publish()

    if log:
        print(f"[READY] Published JSON to {TOPIC_READY}: {payload}")
        print(f"[READY] Published text to {TOPIC_CLIENT_READY}: {ready_cmd}")


def ready_heartbeat_loop():
    global _stop_ready_heartbeat

    while not _stop_ready_heartbeat:
        try:
            if cache_ready:
                publish_cache_ready(True, "cache_loaded_heartbeat", wait=False, log=False)
        except Exception as e:
            print(f"[READY] Heartbeat publish failed: {e}")

        time.sleep(READY_HEARTBEAT_S)


def publish_client_auto_status(
    auto_on: bool,
    reason: str,
    similarity=None,
    threshold=None,
    frame_id=None
):
    """
    Publishes cache-aware status for the client/UI.

    In this script, auto_on means cache-aware active state for this status topic.
    It does not directly control robot movement.
    """
    if mqtt_client is None:
        return

    payload = {
        "auto": bool(auto_on),
        "auto_mode": bool(auto_on),
        "state": "cao_on" if auto_on else "cao_off",
        "command": CAO_ON_COMMAND if auto_on else CAO_OFF_COMMAND,
        "source": "VIT.py",
        "reason": reason,
        "label": DETECTION_LABEL,
        "similarity": similarity,
        "threshold": threshold,
        "frame_id": frame_id,
        "timestamp": time.time(),
    }

    mqtt_client.publish(TOPIC_AUTO_STATUS, json.dumps(payload), qos=1, retain=True)
    mqtt_client.publish(TOPIC_CACHE_EVENT, json.dumps(payload), qos=0, retain=False)

    print(f"[CLIENT] Published cache-aware status to {TOPIC_AUTO_STATUS}: {payload}")


# =========================
# TERMINAL CONFIDENCE CHECKER
# =========================
def print_cache_confidence(
    frame_id,
    best_match,
    similarity,
    threshold,
    hit_streak,
    result,
    latched=False,
    miss_streak_after_latch=0
):
    """
    Prints cache-side confidence in terminal.

    This only runs when:
        cache_ready == True
        test_active == True
        cache-aware comparison is actually happening
    """
    if best_match is None:
        print(
            f"[CACHE_CONF] frame_id={frame_id} | "
            f"active={test_active} | "
            f"cache_ready={cache_ready} | "
            f"latched={latched} | "
            f"best_label=None | "
            f"confidence=N/A | "
            f"similarity=N/A | "
            f"threshold=N/A | "
            f"hit_streak={hit_streak}/{CONSECUTIVE_HITS_REQUIRED} | "
            f"miss_after_latch={miss_streak_after_latch}/{UNLATCH_MISSES_REQUIRED} | "
            f"result={result}"
        )
        return

    confidence_percent = float(similarity) * 100.0
    threshold_percent = float(threshold) * 100.0

    print(
        f"[CACHE_CONF] frame_id={frame_id} | "
        f"active={test_active} | "
        f"cache_ready={cache_ready} | "
        f"latched={latched} | "
        f"best_label={best_match['label']} | "
        f"confidence={confidence_percent:.2f}% | "
        f"similarity={similarity:.4f} | "
        f"threshold={threshold_percent:.2f}% | "
        f"hit_streak={hit_streak}/{CONSECUTIVE_HITS_REQUIRED} | "
        f"miss_after_latch={miss_streak_after_latch}/{UNLATCH_MISSES_REQUIRED} | "
        f"result={result}"
    )


# =========================
# CAMERA FRAME DECODER
# =========================
def decode_camera_frame(payload_bytes):
    try:
        payload_text = payload_bytes.decode("utf-8")
        payload = json.loads(payload_text)

        jpg_b64 = payload.get("jpg_b64")

        if jpg_b64 is None:
            raise ValueError("Missing jpg_b64 field")

        jpg_bytes = base64.b64decode(jpg_b64)
        jpg_array = np.frombuffer(jpg_bytes, dtype=np.uint8)

        frame = cv2.imdecode(jpg_array, cv2.IMREAD_COLOR)

        if frame is None:
            raise ValueError("cv2.imdecode returned None")

        frame_id = int(payload.get("frame_id", 0))
        timestamp = float(payload.get("timestamp", time.time()))

        return frame, frame_id, timestamp

    except Exception as e:
        print(f"[CAMERA] Failed to decode frame: {e}")
        return None, None, None


# =========================
# COMMAND PARSING
# =========================
def parse_command_payload(raw_payload: bytes) -> str:
    text = raw_payload.decode("utf-8", errors="ignore").strip()

    if not text:
        return ""

    try:
        data = json.loads(text)

        if isinstance(data, dict):
            cmd = (
                data.get("cmd")
                or data.get("command")
                or data.get("action")
                or data.get("type")
                or ""
            )
            return str(cmd).strip().lower()

    except Exception:
        pass

    return text.strip().lower()


def set_stop_similarity_threshold(percent: int) -> bool:
    """Apply dashboard stop-similarity floor (1–100 %)."""
    global _stop_similarity_threshold
    if not 1 <= int(percent) <= 100:
        return False
    _stop_similarity_threshold = int(percent) / 100.0
    print(
        f"[CMD] Stop similarity threshold set to {percent}% "
        f"({_stop_similarity_threshold:.2f})"
    )
    publish_status(
        "stop_similarity_changed",
        {
            "threshold_percent": int(percent),
            "threshold": _stop_similarity_threshold,
        },
    )
    return True


def effective_detection_threshold(cache_object_threshold: float) -> float:
    return max(float(cache_object_threshold), _stop_similarity_threshold)


def handle_cossim_message(msg):
    """Apply stop-similarity percent from TOPIC_COSSIM (payload is e.g. ``70``)."""
    if msg.topic != TOPIC_COSSIM:
        return

    try:
        text = msg.payload.decode("utf-8", errors="ignore").strip()
        if not text:
            print("[COSSIM] Empty payload — ignored.")
            return

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                raw = data.get("percent") or data.get("threshold_percent") or data.get("value")
                if raw is not None:
                    text = str(raw).strip()
        except Exception:
            pass

        pct = int(round(float(text)))
        if set_stop_similarity_threshold(pct):
            return
        print(f"[COSSIM] Invalid stop similarity percent: {text!r}")
    except Exception as e:
        print(f"[COSSIM] Error handling message: {e}")


def handle_command_message(msg):
    global test_active, _hit_streak, _detection_latched, _miss_streak_after_latch

    if msg.topic != TOPIC_COMMAND:
        return

    try:
        command = parse_command_payload(msg.payload)
        print(f"[CMD] Received command: '{command}'")

        if command in COMMAND_TO_EMBEDDING_BYTES:
            new_bytes = COMMAND_TO_EMBEDDING_BYTES[command]
            ok = set_embedding_size(new_bytes)

            if ok:
                publish_status(
                    "embedding_size_changed",
                    {
                        "command": command,
                        "embedding_bytes": new_bytes,
                        "target_dims": EMBEDDING_BYTES_TO_DIMS[new_bytes],
                    },
                )
            return

        if command in START_COMMANDS:
            test_active = True
            _hit_streak = 0
            _detection_latched = False
            _miss_streak_after_latch = 0

            print(f"[CAO] Received '{command}'. Cache-aware offloading is ACTIVE.")
            print("[CAO] Cache-aware will stay ON until Cao_OFF is received.")

            publish_cache_ready(True, f"cache_aware_active_by_{command}", wait=False, log=True)
            publish_client_auto_status(True, f"cache_aware_active_by_{command}")
            return

        if command in STOP_OR_OFF_COMMANDS:
            test_active = False
            _hit_streak = 0
            _detection_latched = False
            _miss_streak_after_latch = 0

            print(f"[CAO] Received '{command}'. Cache-aware offloading is INACTIVE.")

            if cache_ready:
                publish_cache_ready(True, f"cache_aware_inactive_by_{command}", wait=False, log=True)
            else:
                publish_cache_ready(False, f"cache_aware_inactive_by_{command}", wait=False, log=True)

            publish_client_auto_status(False, f"cache_aware_inactive_by_{command}")
            return

        print("[CMD] Unknown command for VIT.py.")
        print("[CMD] Valid VIT commands: embds1, embds2, embds3, Cao_ON, Cao_OFF.")

    except Exception as e:
        print(f"[CMD] Error handling command: {e}")


# =========================
# ROBOT STOP LOGIC
# =========================
def safe_publish_robot_command(command: str, repeat: int = 1):
    """
    Send command to the Yahboom movement script.

    Important:
    This publishes to TOPIC_ROBOT_COMMAND, not TOPIC_COMMAND.
    That means sending 'stop' will not turn cache-aware off.
    """
    if mqtt_client is None:
        return

    for _ in range(repeat):
        result = mqtt_client.publish(TOPIC_ROBOT_COMMAND, command, qos=1)

        try:
            result.wait_for_publish(timeout=2.0)
        except TypeError:
            result.wait_for_publish()

        print(f"[ROBOT_CMD] Sent '{command}' to {TOPIC_ROBOT_COMMAND}")
        time.sleep(STOP_REPEAT_DELAY_S)


def publish_stop_and_auto_off(similarity=None, threshold=None, frame_id=None):
    """
    Stop the Yahboom when cache hit happens.

    Important:
    This does NOT turn cache-aware off.
    Cache-aware stays active until the client sends Cao_OFF.
    """
    global _hit_streak, _stop_sequence_running

    if _stop_sequence_running:
        return

    _stop_sequence_running = True

    try:
        # Reset hit streak so it does not instantly trigger again.
        # Do NOT set test_active = False here.
        _hit_streak = 0

        print("[STOP] Bottle detected. Sending stop to Yahboom...")
        print("[STOP] Cache-aware remains ON until client sends Cao_OFF.")

        # Stop movement.
        safe_publish_robot_command(STOP_COMMAND, repeat=STOP_REPEAT_COUNT)

        # If your movement script needs auto_off to exit auto mode, send it to robot topic only.
        safe_publish_robot_command(AUTO_OFF_COMMAND, repeat=1)

        # Keep client cache-aware state ON.
        publish_client_auto_status(
            True,
            "bottle_detected_robot_stopped_cache_still_on",
            similarity=similarity,
            threshold=threshold,
            frame_id=frame_id
        )

        publish_cache_ready(
            True,
            "bottle_detected_robot_stopped_cache_still_on",
            wait=False,
            log=True
        )

        print("[STOP] Stop sequence completed. Cache-aware is still ON.")

    finally:
        _stop_sequence_running = False


# =========================
# MQTT SETUP
# =========================
def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except Exception:
            client = mqtt.Client()

    def _on_connect(c, userdata, flags, reason_code, properties=None):
        rc_text = str(reason_code)

        if reason_code == 0 or rc_text == "Success":
            print(f"[MQTT] Connected to {BROKER_IP}:{BROKER_PORT}")

            c.subscribe(TOPIC_CAMERA_FRAME, qos=0)
            c.subscribe(TOPIC_COMMAND, qos=0)
            c.subscribe(TOPIC_COSSIM, qos=0)

            print(f"[MQTT] Subscribed camera frames <- {TOPIC_CAMERA_FRAME}")
            print(f"[MQTT] Subscribed VIT commands  <- {TOPIC_COMMAND}")
            print(f"[MQTT] Subscribed stop sim %   <- {TOPIC_COSSIM}")
            print(f"[MQTT] Robot commands publish  -> {TOPIC_ROBOT_COMMAND}")
            print(f"[MQTT] Publishing embeddings   -> {TOPIC_CLIP}")
            print(f"[MQTT] Publishing status       -> {TOPIC_STATUS}")
            print(f"[MQTT] Publishing detections   -> {TOPIC_DETECT}")
            print(f"[MQTT] Publishing ready JSON   -> {TOPIC_READY}")
            print(f"[MQTT] Publishing ready text   -> {TOPIC_CLIENT_READY}")
            print(f"[MQTT] Publishing auto status  -> {TOPIC_AUTO_STATUS}")

            if cache_ready:
                publish_cache_ready(True, "cache_loaded_on_connect", wait=False, log=True)
            else:
                publish_cache_ready(False, "starting_or_cache_not_loaded", wait=False, log=True)

            publish_status(
                "vit_encoder_started",
                {
                    "model": "MobileCLIP-S1",
                    "device": str(device),
                    "camera_frame_topic": TOPIC_CAMERA_FRAME,
                    "embedding_topic": TOPIC_CLIP,
                    "vit_command_topic": TOPIC_COMMAND,
                    "robot_command_topic": TOPIC_ROBOT_COMMAND,
                    "ready_topic": TOPIC_READY,
                    "client_ready_topic": TOPIC_CLIENT_READY,
                    "embedding_shape": [1, get_target_dims()],
                    "embedding_bytes": get_embedding_bytes(),
                    "dtype": "float32",
                    "inference_every_n_frames": INFERENCE_EVERY_N_FRAMES,
                    "valid_commands": list(COMMAND_TO_EMBEDDING_BYTES.keys()) + [CAO_ON_COMMAND, CAO_OFF_COMMAND],
                    "cache_aware_initial_state": bool(test_active),
                },
            )

        else:
            print(f"[MQTT] Connect failed rc={reason_code}")

    def _on_message(c, userdata, msg):
        global latest_frame, latest_frame_id, frames_received

        if msg.topic == TOPIC_COMMAND:
            handle_command_message(msg)

        elif msg.topic == TOPIC_COSSIM:
            handle_cossim_message(msg)

        elif msg.topic == TOPIC_CAMERA_FRAME:
            frame, frame_id, timestamp = decode_camera_frame(msg.payload)

            if frame is None:
                return

            with frame_lock:
                latest_frame = frame.copy()
                latest_frame_id = frame_id

            frames_received += 1

            if frames_received % 30 == 0:
                print(
                    f"[CAMERA] Received {frames_received} frames "
                    f"| latest frame_id={frame_id}"
                )

    def _on_disconnect(c, userdata, *args):
        print("[MQTT] Disconnected. Auto-reconnect will try again.")

    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    return client


# =========================
# CLOUD PUBLISH HELPER
# =========================
def publish_embedding_to_cloud(
    raw_bytes,
    embedding,
    emb_bytes,
    frame,
    frame_id,
    extra=None
):
    """
    Publishes embedding to cloud/client.

    This should happen only when:
      - cache is not ready, or
      - cache-aware is off, or
      - cache-aware is on and cache MISS happens.
    """
    image_file_size = int(frame.nbytes)

    payload = {
        "raw_bytes": len(raw_bytes),
        "embedding_dim": int(embedding.shape[-1]),
        "embedding_bytes": emb_bytes,
        "dtype": "float32",
        "frame_id": int(frame_id),
        "image_file_size": image_file_size,
        "timestamp": time.time(),
        "data": base64.b64encode(raw_bytes).decode("utf-8"),
    }

    if extra:
        payload.update(extra)

    mqtt_client.publish(TOPIC_CLIP, json.dumps(payload), qos=0)


# =========================
# VIT WORKER
# =========================
def vit_worker():
    global embeddings_sent
    global _last_detection_time, _hit_streak
    global _detection_latched, _miss_streak_after_latch

    last_processed_frame_id = -1

    while not stop_event.is_set():
        with frame_lock:
            frame = None if latest_frame is None else latest_frame.copy()
            frame_id = latest_frame_id

        if frame is None:
            time.sleep(0.01)
            continue

        if frame_id == last_processed_frame_id:
            time.sleep(0.005)
            continue

        last_processed_frame_id = frame_id

        if frame_id % INFERENCE_EVERY_N_FRAMES != 0:
            time.sleep(0.001)
            continue

        try:
            if SHOW_PREVIEW:
                cv2.imshow("VIT Input Frame", frame)
                cv2.waitKey(1)

            embedding = get_embedding(frame)
            raw_bytes = embedding.tobytes()

            emb_bytes = get_embedding_bytes()
            target_dims = get_target_dims()
            image_file_size = int(frame.nbytes)

            embeddings_sent += 1

            # =========================
            # CASE 1: CACHE NOT READY
            # Publish to cloud normally.
            # =========================
            if not cache_ready:
                publish_embedding_to_cloud(
                    raw_bytes=raw_bytes,
                    embedding=embedding,
                    emb_bytes=emb_bytes,
                    frame=frame,
                    frame_id=frame_id,
                    extra={
                        "cache_ready": False,
                        "cache_active": False,
                    }
                )

            # =========================
            # CASE 2: CACHE READY BUT CACHE-AWARE OFF
            # Publish to cloud normally.
            # =========================
            elif not test_active:
                publish_embedding_to_cloud(
                    raw_bytes=raw_bytes,
                    embedding=embedding,
                    emb_bytes=emb_bytes,
                    frame=frame,
                    frame_id=frame_id,
                    extra={
                        "cache_ready": True,
                        "cache_active": False,
                    }
                )

                if frame_id % LOG_EVERY_N_FRAMES == 0:
                    print(
                        f"[PASS] frame_id={frame_id} | "
                        f"cache-aware offloading inactive, publishing embedding normally."
                    )

            # =========================
            # CASE 3: CACHE READY AND CACHE-AWARE ON
            # Compare live embedding with local cache first.
            # =========================
            else:
                best_match, similarity = check_detection(embedding[0])

                if best_match is None:
                    _hit_streak = 0

                    if _detection_latched:
                        _miss_streak_after_latch += 1
                        if _miss_streak_after_latch >= UNLATCH_MISSES_REQUIRED:
                            _detection_latched = False
                            _miss_streak_after_latch = 0
                            print("[CACHE] Detection latch cleared because cache miss repeated.")

                    if embeddings_sent % CACHE_CONFIDENCE_LOG_EVERY_N_CHECKS == 0:
                        print_cache_confidence(
                            frame_id=frame_id,
                            best_match=None,
                            similarity=None,
                            threshold=None,
                            hit_streak=_hit_streak,
                            result="NO_MATCH_CLOUD",
                            latched=_detection_latched,
                            miss_streak_after_latch=_miss_streak_after_latch
                        )

                    # No local cache match, so cloud/client should check.
                    publish_embedding_to_cloud(
                        raw_bytes=raw_bytes,
                        embedding=embedding,
                        emb_bytes=emb_bytes,
                        frame=frame,
                        frame_id=frame_id,
                        extra={
                            "cache_ready": True,
                            "cache_active": True,
                            "cache_hit": False,
                            "similarity": None,
                            "threshold": None,
                        }
                    )

                else:
                    threshold = effective_detection_threshold(best_match["threshold"])
                    cache_hit_now = similarity >= threshold

                    if cache_hit_now:
                        _hit_streak += 1
                        _miss_streak_after_latch = 0
                    else:
                        _hit_streak = 0

                        if _detection_latched:
                            _miss_streak_after_latch += 1
                            if _miss_streak_after_latch >= UNLATCH_MISSES_REQUIRED:
                                _detection_latched = False
                                _miss_streak_after_latch = 0
                                print("[CACHE] Detection latch cleared because bottle is no longer matching.")

                    now = time.time()
                    cooldown_active = (now - _last_detection_time) < DETECTION_COOLDOWN_S

                    result_text = "HIT_NO_CLOUD" if cache_hit_now else "MISS_CLOUD"
                    if _detection_latched and cache_hit_now:
                        result_text = "HIT_LATCHED_NO_CLOUD"

                    if embeddings_sent % CACHE_CONFIDENCE_LOG_EVERY_N_CHECKS == 0:
                        print_cache_confidence(
                            frame_id=frame_id,
                            best_match=best_match,
                            similarity=similarity,
                            threshold=threshold,
                            hit_streak=_hit_streak,
                            result=result_text,
                            latched=_detection_latched,
                            miss_streak_after_latch=_miss_streak_after_latch
                        )

                    # If local cache HIT, do NOT send embedding to cloud.
                    # This is the cache-aware offloading behavior.
                    if cache_hit_now:
                        if (
                            _hit_streak >= CONSECUTIVE_HITS_REQUIRED
                            and not cooldown_active
                            and not _detection_latched
                        ):
                            _last_detection_time = now
                            _hit_streak = 0
                            _detection_latched = True
                            _miss_streak_after_latch = 0

                            detect_payload = {
                                "detected": True,
                                "label": best_match["label"],
                                "similarity": round(similarity, 4),
                                "threshold": threshold,
                                "hit_streak_required": CONSECUTIVE_HITS_REQUIRED,
                                "commands_sent": [
                                    STOP_COMMAND,
                                    AUTO_OFF_COMMAND,
                                ],
                                "dims": int(embedding.shape[-1]),
                                "frame_id": int(frame_id),
                                "timestamp": now,
                                "mode": "cached_embedding",
                                "source": "VIT.py",
                                "cache_active": True,
                                "cache_stays_on_until": "Cao_OFF",
                            }

                            mqtt_client.publish(
                                TOPIC_DETECT,
                                json.dumps(detect_payload),
                                qos=0,
                                retain=False
                            )

                            print(
                                f"[DETECT] {best_match['label'].upper()} DETECTED - STOPPING | "
                                f"similarity={similarity:.4f} | threshold={threshold} | "
                                f"hits={CONSECUTIVE_HITS_REQUIRED} | dims={target_dims} | frame_id={frame_id}"
                            )
                            print("[DETECT] Cache-aware remains ON until client sends Cao_OFF.")

                            stop_thread = threading.Thread(
                                target=publish_stop_and_auto_off,
                                args=(round(similarity, 4), threshold, frame_id),
                                daemon=True
                            )
                            stop_thread.start()

                        # No cloud publish on cache hit.
                        # Even if hit streak is only 1/3 or 2/3, this is still a cache hit.
                        pass

                    # If local cache MISS, cloud/client should check.
                    else:
                        publish_embedding_to_cloud(
                            raw_bytes=raw_bytes,
                            embedding=embedding,
                            emb_bytes=emb_bytes,
                            frame=frame,
                            frame_id=frame_id,
                            extra={
                                "cache_ready": True,
                                "cache_active": True,
                                "cache_hit": False,
                                "best_label": best_match["label"],
                                "similarity": round(similarity, 4),
                                "threshold": threshold,
                                "hit_streak": _hit_streak,
                            }
                        )

                        if frame_id % LOG_EVERY_N_FRAMES == 0:
                            cooldown_msg = " | cooldown active" if cooldown_active else ""
                            print(
                                f"[MISS] frame_id={frame_id} | active={test_active} | "
                                f"best_label={best_match['label']} | similarity={similarity:.4f} | "
                                f"threshold={threshold} | hit_streak={_hit_streak}/{CONSECUTIVE_HITS_REQUIRED} | "
                                f"dims={target_dims}{cooldown_msg}"
                            )

                            if similarity < 0.35:
                                print(
                                    "[HINT] Similarity is very low. "
                                    "Your live embedding may still be from the whole camera frame. "
                                    "For better bottle detection, crop the bottle before creating the live embedding."
                                )

            if embeddings_sent % 10 == 0:
                print(
                    f"[VIT] Processed {embeddings_sent} embeddings "
                    f"| dims={target_dims} "
                    f"| embedding size={len(raw_bytes)} B "
                    f"| frame_id={frame_id} "
                    f"| cache_active={test_active} "
                    f"| latched={_detection_latched}"
                )

            publish_status(
                "running",
                {
                    "frames_received": frames_received,
                    "embeddings_sent": embeddings_sent,
                    "embedding_shape": list(embedding.shape),
                    "embedding_bytes": emb_bytes,
                    "embedding_size_bytes": len(raw_bytes),
                    "dtype": str(embedding.dtype),
                    "topic": TOPIC_CLIP,
                    "frame_id": int(frame_id),
                    "image_file_size": image_file_size,
                    "cache_ready": cache_ready,
                    "cache_active": test_active,
                    "detection_latched": _detection_latched,
                    "miss_streak_after_latch": _miss_streak_after_latch,
                },
            )

        except Exception as e:
            print(f"[VIT] ERROR during embedding processing: {e}")

            publish_status(
                "embedding_error",
                {
                    "error": str(e),
                    "frame_id": int(frame_id),
                },
            )

        time.sleep(0.001)


# =========================
# MAIN
# =========================
def main():
    global mqtt_client, cache_ready, _stop_ready_heartbeat

    print("========================================")
    print("[INFO] Starting VIT.py with cache-aware offloading")
    print("========================================")
    print(f"[INFO] Temp dir           : {TEMP_DIR}")
    print(f"[INFO] Torch cache dir    : {TORCH_CACHE_DIR}")
    print(f"[INFO] Cache file         : {CACHE_FILE_PATH}")
    print(f"[INFO] Camera topic       : {TOPIC_CAMERA_FRAME}")
    print(f"[INFO] Embedding topic    : {TOPIC_CLIP}")
    print(f"[INFO] VIT command topic  : {TOPIC_COMMAND}")
    print(f"[INFO] Robot command topic: {TOPIC_ROBOT_COMMAND}")
    print(f"[INFO] Ready JSON topic   : {TOPIC_READY}")
    print(f"[INFO] Ready text topic   : {TOPIC_CLIENT_READY}")
    print(f"[INFO] Detection label    : {DETECTION_LABEL}")
    print(f"[INFO] Cache-aware starts : OFF")
    print(f"[INFO] Turn ON with       : Cao_ON")
    print(f"[INFO] Turn OFF with      : Cao_OFF")
    print("========================================")

    load_model()

    mqtt_client = create_mqtt_client()

    try:
        mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
        mqtt_client.loop_start()
    except Exception as exc:
        print(f"[MQTT] ERROR: Could not connect to broker: {exc}")
        raise

    time.sleep(0.5)
    publish_cache_ready(False, "starting", wait=True, log=True)

    try:
        load_cached_embeddings()
        publish_cache_ready(True, "cache_loaded_cache_aware_off", wait=True, log=True)

        ready_thread = threading.Thread(target=ready_heartbeat_loop, daemon=True)
        ready_thread.start()

    except Exception as e:
        cache_ready = False
        print(f"[ERROR] Failed to load cached embeddings: {e}")

        try:
            publish_cache_ready(False, f"cache_load_failed: {e}", wait=True, log=True)
        except Exception:
            pass

    worker_thread = threading.Thread(target=vit_worker, daemon=True)
    worker_thread.start()

    print("\n[VIT] VIT.py is running.")
    print("[VIT] Waiting for camera frames from webrtc_server.py...")
    print(f"[VIT] Client will receive '{CAO_READY_COMMAND}' on {TOPIC_CLIENT_READY} when cache is ready.")
    print("[VIT] Cache-aware is OFF at startup.")
    print("[VIT] Send 'Cao_ON' to activate cache-aware offloading.")
    print("[VIT] Send 'Cao_OFF' to deactivate cache-aware offloading.")
    print("[VIT] Cache-aware will NOT turn off automatically after detection.")
    print("[VIT] Cache confidence prints only after cache-aware is ON.")
    print("[VIT] Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[VIT] Stopping...")
        stop_event.set()
        _stop_ready_heartbeat = True

    finally:
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()

        if mqtt_client is not None:
            try:
                publish_cache_ready(False, "manual_shutdown", wait=True, log=True)
                publish_client_auto_status(False, "manual_shutdown")
                publish_status(
                    "vit_encoder_stopped",
                    {
                        "frames_received": frames_received,
                        "embeddings_sent": embeddings_sent,
                    },
                )
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
            except Exception:
                pass

        print("[VIT] Stopped.")


if __name__ == "__main__":
    main()

