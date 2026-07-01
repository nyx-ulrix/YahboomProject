# cache_aware_offloading.py
# Runs on the Yahboom Pi
#
# Purpose:
# 1. Load cached bottle embedding from /home/pi/cache_embeddings.json
# 2. Tell client/dashboard cache-aware is ready
# 3. Wait for client Start / auto_on
# 4. Compare live ViT embedding against cached bottle embedding
# 5. If similarity >= threshold for multiple frames:
#       - stop Yahboom
#       - send auto_off to Yahboom command topic
#       - send auto_off status to client/dashboard
#
# This script checks EMBEDDINGS only.
# It does NOT load torch/open_clip.
# It does NOT send estop_on.

import json
import time
import base64
import sys
import threading
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt


# =========================
# SETTINGS
# =========================

BROKER_IP = "localhost"
BROKER_PORT = 1883

CACHE_FILE_PATH = "/home/pi/cache_embeddings.json"

# Incoming live embedding topic
TOPIC_EMBEDDING = "yahboom/vit/embedding"
TOPIC_STATUS = "yahboom/vit/status"

# Command topic used by robot and client
TOPIC_COMMAND = "yahboom/cmd"

# Detection output topic
TOPIC_DETECT = "yahboom/detect/status"

# Ready topic used by client Start button
TOPIC_READY = "yahboom/cache_aware/ready"

# Extra client UI status topics
TOPIC_AUTO_STATUS = "yahboom/auto/status"
TOPIC_CACHE_EVENT = "yahboom/cache_aware/event"

# Object label inside cache JSON
DETECTION_LABEL = "bottle"

# Default threshold if JSON does not contain one
DEFAULT_DETECTION_THRESHOLD = 0.70

# Commands
AUTO_ON_COMMAND = "auto_on"
AUTO_OFF_COMMAND = "auto_off"
STOP_COMMAND = "stop"

START_COMMANDS = {
    "auto_on",
    "start",
    "start_auto",
    "explore_on",
}

STOP_OR_OFF_COMMANDS = {
    "auto_off",
    "stop",
    "estop_on",
    "manual",
}

# Only start checking after client presses Start / auto_on
TEST_ONLY_AFTER_AUTO_ON = True

# Need this many continuous hits before stopping
CONSECUTIVE_HITS_REQUIRED = 3

# Prevent repeated auto_off spam
DETECTION_COOLDOWN_S = 2.0

# Repeat stop command to make sure Yahboom receives zero velocity
STOP_REPEAT_COUNT = 8
STOP_REPEAT_DELAY_S = 0.05

# Logs
LOG_EVERY_N_FRAMES = 50

# Ready heartbeat
READY_HEARTBEAT_S = 2.0


# =========================
# GLOBALS
# =========================

cached_objects = []
cache_ready = False
test_active = False

_last_detection_time = 0.0
_hit_streak = 0

_stop_ready_heartbeat = False
_stop_sequence_running = False


# =========================
# EMBEDDING HELPERS
# =========================

def normalise_embedding(embedding: np.ndarray) -> np.ndarray:
    embedding = embedding.astype(np.float32)
    norm = np.linalg.norm(embedding)

    if norm <= 1e-12:
        return embedding

    return embedding / norm


def decode_cached_embedding(obj: dict) -> np.ndarray:
    """
    Supports two cache JSON formats.

    Format 1, recommended:
        "data": "base64 float32 embedding"

    Format 2:
        "embedding": [0.1, 0.2, ...]
    """

    if "data" in obj:
        raw_bytes = base64.b64decode(obj["data"])
        embedding = np.frombuffer(raw_bytes, dtype=np.float32).copy()

    elif "embedding" in obj:
        embedding = np.array(obj["embedding"], dtype=np.float32)

    else:
        raise ValueError("Cached object has no 'data' or 'embedding' field.")

    return normalise_embedding(embedding)


def load_cached_embeddings():
    """
    Load cached bottle embedding from /home/pi/cache_embeddings.json.
    """
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
    Compare live embedding against cached bottle embedding.

    Returns:
        best_match, best_similarity
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
# READY / CLIENT STATUS
# =========================

def publish_cache_ready(client, ready: bool, reason: str, wait: bool = False, log: bool = True):
    """
    Tell dashboard/client whether cache-aware script is ready.

    retain=True is important so the client receives the latest ready state
    even if the dashboard opens after this script starts.
    """

    payload = {
        "ready": bool(ready),
        "cache_ready": bool(ready),
        "embedding_ready": bool(ready),
        "cache_aware_ready": bool(ready),
        "status": "ready" if ready else "not_ready",

        "script": "cache_aware_offloading.py",
        "mode": "cached_embedding",
        "label": DETECTION_LABEL,
        "cache_file": CACHE_FILE_PATH,
        "cached_count": len(cached_objects),

        "test_active": bool(test_active),
        "requires_auto_on": bool(TEST_ONLY_AFTER_AUTO_ON),

        "reason": reason,
        "timestamp": time.time(),
    }

    result = client.publish(
        TOPIC_READY,
        json.dumps(payload),
        qos=1,
        retain=True
    )

    if wait:
        try:
            result.wait_for_publish(timeout=2.0)
        except TypeError:
            result.wait_for_publish()

    if log:
        print(f"[READY] Published to {TOPIC_READY}: {payload}")


def ready_heartbeat_loop(client):
    """
    Keep publishing ready=True while cache is loaded.
    This helps if the client misses the first retained ready message.
    """

    global _stop_ready_heartbeat

    while not _stop_ready_heartbeat:
        try:
            if cache_ready:
                publish_cache_ready(
                    client,
                    True,
                    "cache_loaded_heartbeat",
                    wait=False,
                    log=False
                )
        except Exception as e:
            print(f"[READY] Heartbeat publish failed: {e}")

        time.sleep(READY_HEARTBEAT_S)


def publish_client_auto_status(
    client,
    auto_on: bool,
    reason: str,
    similarity=None,
    threshold=None,
    frame=None
):
    """
    Tell the client/dashboard whether auto mode is on or off.
    """

    payload = {
        "auto": bool(auto_on),
        "auto_mode": bool(auto_on),
        "state": "auto_on" if auto_on else "auto_off",
        "command": AUTO_ON_COMMAND if auto_on else AUTO_OFF_COMMAND,

        "source": "cache_aware_offloading.py",
        "reason": reason,
        "label": DETECTION_LABEL,

        "similarity": similarity,
        "threshold": threshold,
        "frame": frame,
        "timestamp": time.time(),
    }

    # Retained state for UI
    client.publish(
        TOPIC_AUTO_STATUS,
        json.dumps(payload),
        qos=1,
        retain=True
    )

    # Non-retained event log
    client.publish(
        TOPIC_CACHE_EVENT,
        json.dumps(payload),
        qos=0,
        retain=False
    )

    print(f"[CLIENT] Published auto status to {TOPIC_AUTO_STATUS}: {payload}")


# =========================
# COMMAND PARSING
# =========================

def parse_command_payload(raw_payload: bytes) -> str:
    """
    Supports:
        auto_on
        auto_off
        stop

    Also supports JSON:
        {"cmd": "auto_on"}
        {"command": "auto_on"}
        {"action": "auto_on"}
        {"type": "auto_on"}
    """

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


def handle_command_message(client, msg):
    """
    Used so cache-aware detection only starts after client sends auto_on.
    """

    global test_active, _hit_streak

    cmd = parse_command_payload(msg.payload)

    if not cmd:
        return

    if cmd in START_COMMANDS:
        test_active = True
        _hit_streak = 0

        print(f"[TEST] Received '{cmd}'. Cache-aware detection is ACTIVE.")

        publish_cache_ready(
            client,
            True,
            f"test_active_by_{cmd}",
            wait=False,
            log=True
        )

        publish_client_auto_status(
            client,
            True,
            f"test_active_by_{cmd}"
        )

    elif cmd in STOP_OR_OFF_COMMANDS:
        if test_active:
            print(f"[TEST] Received '{cmd}'. Cache-aware detection is INACTIVE.")

        test_active = False
        _hit_streak = 0

        if cache_ready:
            publish_cache_ready(
                client,
                True,
                f"test_inactive_by_{cmd}",
                wait=False,
                log=True
            )

        publish_client_auto_status(
            client,
            False,
            f"test_inactive_by_{cmd}"
        )


# =========================
# ROBOT STOP LOGIC
# =========================

def safe_publish_command(client, command: str, repeat: int = 1):
    """
    Publish command to Yahboom command topic.
    """

    for _ in range(repeat):
        result = client.publish(TOPIC_COMMAND, command, qos=1)

        try:
            result.wait_for_publish(timeout=2.0)
        except TypeError:
            result.wait_for_publish()

        print(f"[CMD] Sent '{command}' to {TOPIC_COMMAND}")
        time.sleep(STOP_REPEAT_DELAY_S)


def publish_stop_and_auto_off(client, similarity=None, threshold=None, frame=None):
    """
    Bottle detected:
    1. Stop Yahboom movement
    2. Send auto_off to Yahboom command topic
    3. Tell client/dashboard auto mode is off
    4. Repeat stop to ensure robot velocity becomes zero
    """

    global test_active, _hit_streak, _stop_sequence_running

    if _stop_sequence_running:
        return

    _stop_sequence_running = True

    try:
        test_active = False
        _hit_streak = 0

        print("[STOP] Bottle detected. Sending stop + auto_off...")

        # 1. Stop robot immediately
        safe_publish_command(client, STOP_COMMAND, repeat=1)

        # 2. Send auto_off to robot/backend/client command topic
        safe_publish_command(client, AUTO_OFF_COMMAND, repeat=1)

        # 3. Tell client UI auto is off
        publish_client_auto_status(
            client,
            False,
            "bottle_detected_cache_similarity",
            similarity=similarity,
            threshold=threshold,
            frame=frame
        )

        # 4. Repeat stop for safety
        safe_publish_command(client, STOP_COMMAND, repeat=STOP_REPEAT_COUNT)

        # 5. Keep cache-aware ready, but inactive
        publish_cache_ready(
            client,
            True,
            "bottle_detected_auto_off_sent",
            wait=False,
            log=True
        )

        print("[STOP] Stop + auto_off sequence completed.")

    finally:
        _stop_sequence_running = False


# =========================
# MQTT CALLBACKS
# =========================

def on_connect(client, userdata, flags, reason_code, properties=None):
    rc_text = str(reason_code)

    if reason_code == 0 or rc_text == "Success":
        print(f"[MQTT] Connected to broker at {BROKER_IP}:{BROKER_PORT}")

        client.subscribe(TOPIC_EMBEDDING, qos=0)
        client.subscribe(TOPIC_STATUS, qos=0)
        client.subscribe(TOPIC_COMMAND, qos=0)

        print(f"[MQTT] Subscribed to embeddings : {TOPIC_EMBEDDING}")
        print(f"[MQTT] Subscribed to status     : {TOPIC_STATUS}")
        print(f"[MQTT] Subscribed to commands   : {TOPIC_COMMAND}")
        print(f"[MQTT] Publishing detections to : {TOPIC_DETECT}")
        print(f"[MQTT] Publishing ready flag to : {TOPIC_READY}")
        print(f"[MQTT] Publishing auto status to: {TOPIC_AUTO_STATUS}")

        if cache_ready:
            publish_cache_ready(client, True, "cache_loaded_on_connect", wait=False, log=True)
        else:
            publish_cache_ready(client, False, "starting_or_cache_not_loaded", wait=False, log=True)

    else:
        print(f"[MQTT] Connection failed. reason_code={reason_code}")


def on_message(client, userdata, msg):
    global _last_detection_time, _hit_streak

    topic = msg.topic

    if topic == TOPIC_COMMAND:
        handle_command_message(client, msg)
        return

    if topic == TOPIC_STATUS:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            status = payload.get("status", "unknown")

            if status not in ("running",):
                print(f"[STATUS] VIT status: {status}")

        except Exception:
            pass

        return

    if topic != TOPIC_EMBEDDING:
        return

    try:
        if not cache_ready:
            print("[WARN] Cache not ready yet. Ignoring embedding.")
            return

        payload = json.loads(msg.payload.decode("utf-8"))

        embedding_dims = int(payload["embedding_dim"])
        frame = payload.get("frame", -1)

        if TEST_ONLY_AFTER_AUTO_ON and not test_active:
            if isinstance(frame, int) and frame % LOG_EVERY_N_FRAMES == 0:
                print(
                    f"[WAIT] frame={frame} | cache ready, "
                    f"waiting for client Start / auto_on..."
                )
            return

        raw_bytes = base64.b64decode(payload["data"])
        live_embedding = np.frombuffer(raw_bytes, dtype=np.float32).copy()

        if len(live_embedding) != embedding_dims:
            print(
                f"[WARN] Embedding length mismatch. "
                f"decoded={len(live_embedding)}, declared={embedding_dims}. Skipping."
            )
            return

        best_match, similarity = check_detection(live_embedding)

        if best_match is None:
            print("[WARN] No valid cached embedding match.")
            return

        threshold = best_match["threshold"]

        if similarity >= threshold:
            _hit_streak += 1
        else:
            _hit_streak = 0

        now = time.time()
        cooldown_active = (now - _last_detection_time) < DETECTION_COOLDOWN_S

        if (
            similarity >= threshold
            and _hit_streak >= CONSECUTIVE_HITS_REQUIRED
            and not cooldown_active
        ):
            _last_detection_time = now
            _hit_streak = 0

            detect_payload = {
                "detected": True,
                "label": best_match["label"],
                "similarity": round(similarity, 4),
                "threshold": threshold,
                "hit_streak_required": CONSECUTIVE_HITS_REQUIRED,
                "commands_sent": [
                    STOP_COMMAND,
                    AUTO_OFF_COMMAND,
                    STOP_COMMAND,
                ],
                "dims": embedding_dims,
                "frame": frame,
                "timestamp": now,
                "mode": "cached_embedding",
                "source": "cache_aware_offloading.py",
            }

            client.publish(
                TOPIC_DETECT,
                json.dumps(detect_payload),
                qos=0,
                retain=False
            )

            print(
                f"[DETECT] *** {best_match['label'].upper()} DETECTED — STOPPING *** "
                f"similarity={similarity:.4f} | "
                f"threshold={threshold} | "
                f"hits={CONSECUTIVE_HITS_REQUIRED} | "
                f"dims={embedding_dims} | "
                f"frame={frame}"
            )

            stop_thread = threading.Thread(
                target=publish_stop_and_auto_off,
                args=(client, round(similarity, 4), threshold, frame),
                daemon=True
            )
            stop_thread.start()

        else:
            if isinstance(frame, int) and frame % LOG_EVERY_N_FRAMES == 0:
                cooldown_msg = " | cooldown active" if cooldown_active else ""

                print(
                    f"[DETECT] frame={frame} | "
                    f"active={test_active} | "
                    f"best_label={best_match['label']} | "
                    f"similarity={similarity:.4f} | "
                    f"threshold={threshold} | "
                    f"hit_streak={_hit_streak}/{CONSECUTIVE_HITS_REQUIRED} | "
                    f"dims={embedding_dims}"
                    f"{cooldown_msg}"
                )

                if similarity < 0.35:
                    print(
                        "[HINT] Similarity is very low. "
                        "Your live embedding may still be from the whole camera frame. "
                        "For better bottle detection, crop the red bottle before creating the live embedding."
                    )

    except KeyError as e:
        print(f"[WARN] Missing field in embedding payload: {e}")

    except Exception as e:
        print(f"[ERROR] Failed to process embedding: {e}")


def on_disconnect(client, userdata, *args):
    print("[MQTT] Disconnected. Auto-reconnect will try again.")


# =========================
# MQTT CLIENT SETUP
# =========================

def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except Exception:
            client = mqtt.Client()

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    client.reconnect_delay_set(min_delay=1, max_delay=30)

    return client


# =========================
# MAIN
# =========================

def main():
    global cache_ready, _stop_ready_heartbeat

    print("========================================")
    print("[INFO] Starting cache_aware_offloading.py")
    print("========================================")
    print(f"[INFO] Cache file       : {CACHE_FILE_PATH}")
    print(f"[INFO] Detection label  : {DETECTION_LABEL}")
    print(f"[INFO] Default threshold: {DEFAULT_DETECTION_THRESHOLD}")
    print(f"[INFO] MQTT broker      : {BROKER_IP}:{BROKER_PORT}")
    print(f"[INFO] Embedding topic  : {TOPIC_EMBEDDING}")
    print(f"[INFO] Command topic    : {TOPIC_COMMAND}")
    print(f"[INFO] Ready topic      : {TOPIC_READY}")
    print(f"[INFO] Auto status topic: {TOPIC_AUTO_STATUS}")
    print(f"[INFO] Wait for auto_on : {TEST_ONLY_AFTER_AUTO_ON}")
    print("========================================")

    client = create_mqtt_client()

    try:
        client.connect(BROKER_IP, BROKER_PORT, keepalive=60)
    except Exception as e:
        print(f"[ERROR] Could not connect to MQTT broker: {e}")
        raise

    client.loop_start()

    time.sleep(0.5)

    publish_cache_ready(client, False, "starting", wait=True, log=True)

    try:
        load_cached_embeddings()

        publish_cache_ready(client, True, "cache_loaded", wait=True, log=True)

        ready_thread = threading.Thread(
            target=ready_heartbeat_loop,
            args=(client,),
            daemon=True
        )
        ready_thread.start()

    except Exception as e:
        cache_ready = False
        print(f"[ERROR] Failed to load cached embeddings: {e}")

        try:
            publish_cache_ready(
                client,
                False,
                f"cache_load_failed: {e}",
                wait=True,
                log=True
            )
        except Exception:
            pass

        client.loop_stop()
        client.disconnect()
        sys.exit(1)

    print(f"[INFO] Listening for live embeddings on '{TOPIC_EMBEDDING}'")
    print(f"[INFO] Waiting for client Start / '{AUTO_ON_COMMAND}' before testing.")
    print("[INFO] Press CTRL+C to quit.")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down...")

        _stop_ready_heartbeat = True

        try:
            publish_cache_ready(client, False, "manual_shutdown", wait=True, log=True)
            publish_client_auto_status(client, False, "manual_shutdown")
        except Exception:
            pass

        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
	main()


