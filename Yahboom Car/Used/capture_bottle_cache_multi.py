# capture_bottle_cache_multi.py
# One-time helper script.
#
# It listens to live ViT embeddings from yahboom/vit/embedding.
# You move the bottle to different angles.
# Press Enter each time to save the current embedding.
# It writes many bottle embeddings into /home/pi/cache_embeddings.json.

import json
import time
import base64
import threading
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt


BROKER_IP = "localhost"
BROKER_PORT = 1883

TOPIC_EMBEDDING = "yahboom/vit/embedding"

OUTPUT_JSON_PATH = "/home/pi/cache_embeddings.json"

LABEL = "bottle"
MODEL = "MobileCLIP-S1"
PRETRAINED = "datacompdr"
THRESHOLD = 0.70

SNAPSHOT_COUNT = 6


latest_embedding = None
latest_frame = None
latest_dims = None
latest_time = None
lock = threading.Lock()


def normalise_embedding(embedding: np.ndarray) -> np.ndarray:
    embedding = embedding.astype(np.float32)
    norm = np.linalg.norm(embedding)

    if norm <= 1e-12:
        return embedding

    return embedding / norm


def encode_embedding(embedding: np.ndarray) -> str:
    embedding = normalise_embedding(embedding)
    return base64.b64encode(
        embedding.astype(np.float32).tobytes()
    ).decode("utf-8")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Connected to {BROKER_IP}:{BROKER_PORT}")
        client.subscribe(TOPIC_EMBEDDING, qos=0)
        print(f"[MQTT] Listening to {TOPIC_EMBEDDING}")
    else:
        print(f"[MQTT] Failed to connect. rc={rc}")


def on_message(client, userdata, msg):
    global latest_embedding, latest_frame, latest_dims, latest_time

    try:
        payload = json.loads(msg.payload.decode("utf-8"))

        embedding_dims = int(payload["embedding_dim"])
        frame = payload.get("frame", -1)

        raw_bytes = base64.b64decode(payload["data"])
        embedding = np.frombuffer(raw_bytes, dtype=np.float32).copy()

        if len(embedding) != embedding_dims:
            print(
                f"[WARN] Embedding mismatch. "
                f"decoded={len(embedding)}, declared={embedding_dims}"
            )
            return

        embedding = normalise_embedding(embedding)

        with lock:
            latest_embedding = embedding
            latest_frame = frame
            latest_dims = embedding_dims
            latest_time = time.time()

    except Exception as e:
        print(f"[ERROR] Failed to read embedding: {e}")


def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except Exception:
        client = mqtt.Client()

    client.on_connect = on_connect
    client.on_message = on_message
    return client


def get_latest_embedding_copy():
    with lock:
        if latest_embedding is None:
            return None, None, None, None

        return (
            latest_embedding.copy(),
            latest_frame,
            latest_dims,
            latest_time,
        )


def wait_for_embedding():
    print("[WAIT] Waiting for first live embedding...")

    while True:
        emb, frame, dims, ts = get_latest_embedding_copy()

        if emb is not None:
            print(f"[OK] First embedding received. frame={frame}, dims={dims}")
            return

        time.sleep(0.2)


def backup_existing_json():
    output_path = Path(OUTPUT_JSON_PATH)

    if output_path.exists():
        backup_path = Path(
            f"{OUTPUT_JSON_PATH}.backup_{int(time.time())}"
        )
        output_path.rename(backup_path)
        print(f"[BACKUP] Existing cache moved to: {backup_path}")


def main():
    print("========================================")
    print("[INFO] Multi-angle bottle cache capture")
    print("========================================")
    print(f"[INFO] Output JSON : {OUTPUT_JSON_PATH}")
    print(f"[INFO] Label       : {LABEL}")
    print(f"[INFO] Threshold   : {THRESHOLD}")
    print(f"[INFO] Snapshots   : {SNAPSHOT_COUNT}")
    print("========================================")
    print()
    print("Before running this:")
    print("1. Start your ViT embedding publisher first.")
    print("2. Make sure it is publishing yahboom/vit/embedding.")
    print("3. Put the bottle clearly in the camera view.")
    print()

    client = create_mqtt_client()
    client.connect(BROKER_IP, BROKER_PORT, keepalive=60)
    client.loop_start()

    wait_for_embedding()

    objects = []

    for i in range(1, SNAPSHOT_COUNT + 1):
        print()
        print("========================================")
        print(f"[SNAPSHOT {i}/{SNAPSHOT_COUNT}]")
        print("Move the bottle to a different angle.")
        print("Example: front, left, right, slightly rotated, near, far.")
        input("Press Enter when the bottle is clearly visible... ")

        time.sleep(0.3)

        emb, frame, dims, ts = get_latest_embedding_copy()

        if emb is None:
            print("[WARN] No embedding available. Skipping this snapshot.")
            continue

        encoded = encode_embedding(emb)

        obj = {
            "label": LABEL,
            "sample_id": i,
            "model": MODEL,
            "pretrained": PRETRAINED,
            "embedding_dim": int(dims),
            "threshold": THRESHOLD,
            "normalised": True,
            "dtype": "float32",
            "source": "live_camera_snapshot",
            "frame": frame,
            "data": encoded,
            "created_at": time.time()
        }

        objects.append(obj)

        print(
            f"[SAVED] sample={i} | frame={frame} | "
            f"dims={dims} | total_saved={len(objects)}"
        )

    if not objects:
        print("[ERROR] No snapshots saved. JSON was not created.")
        client.loop_stop()
        client.disconnect()
        return

    cache_data = {
        "objects": objects
    }

    backup_existing_json()

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

    print()
    print("========================================")
    print("[DONE] Multi-angle bottle cache saved.")
    print(f"[DONE] File        : {OUTPUT_JSON_PATH}")
    print(f"[DONE] Embeddings  : {len(objects)}")
    print(f"[DONE] Threshold   : {THRESHOLD}")
    print("========================================")

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
