# capture_reference_snapshot.py
# Non-interactive single-snapshot capture for dashboard SSH workflow.
#
# Listens to yahboom/vit/embedding, saves one embedding into:
#   {output_dir}/{category}/cache_embeddings.json
#
# Prints one JSON line to stdout on success for the dashboard to parse.

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

BROKER_IP = "localhost"
BROKER_PORT = 1883
TOPIC_EMBEDDING = "yahboom/vit/embedding"

MODEL = "MobileCLIP-S1"
PRETRAINED = "datacompdr"
THRESHOLD = 0.70

CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,48}$")

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
    return base64.b64encode(embedding.astype(np.float32).tobytes()).decode("utf-8")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(TOPIC_EMBEDDING, qos=0)
    else:
        print(f"[MQTT] Failed to connect. rc={rc}", file=sys.stderr)


def on_message(client, userdata, msg):
    global latest_embedding, latest_frame, latest_dims, latest_time

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        embedding_dims = int(payload["embedding_dim"])
        frame = payload.get("frame", -1)
        raw_bytes = base64.b64decode(payload["data"])
        embedding = np.frombuffer(raw_bytes, dtype=np.float32).copy()

        if len(embedding) != embedding_dims:
            return

        embedding = normalise_embedding(embedding)

        with lock:
            latest_embedding = embedding
            latest_frame = frame
            latest_dims = embedding_dims
            latest_time = time.time()
    except Exception as exc:
        print(f"[ERROR] Failed to read embedding: {exc}", file=sys.stderr)


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
        return latest_embedding.copy(), latest_frame, latest_dims, latest_time


def expand_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def load_existing_objects(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        objects = data.get("objects", []) if isinstance(data, dict) else []
        return [obj for obj in objects if isinstance(obj, dict)]
    except Exception:
        return []


def next_sample_id(objects: list[dict]) -> int:
    ids = [int(obj.get("sample_id", 0)) for obj in objects if obj.get("sample_id") is not None]
    return (max(ids) if ids else 0) + 1


def write_cache_atomic(json_path: Path, objects: list[dict]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"objects": objects}, indent=2), encoding="utf-8")
    tmp_path.replace(json_path)


def wait_for_embedding(wait_sec: float) -> tuple:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        emb, frame, dims, ts = get_latest_embedding_copy()
        if emb is not None:
            return emb, frame, dims, ts
        time.sleep(0.2)
    return None, None, None, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one reference embedding snapshot.")
    parser.add_argument("--category", required=True, help="Category folder name (slug)")
    parser.add_argument(
        "--output-dir",
        default="~/reference_library",
        help="Base directory for category folders",
    )
    parser.add_argument("--label", default="bottle", help="Object label stored in JSON")
    parser.add_argument(
        "--wait-sec",
        type=float,
        default=10.0,
        help="Seconds to wait for a live embedding",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    category = args.category.strip().lower()

    if not CATEGORY_RE.match(category):
        print(
            json.dumps({
                "status": "error",
                "message": f"invalid category slug: {category!r}",
            }),
            flush=True,
        )
        return 1

    output_dir = expand_path(args.output_dir)
    category_dir = output_dir / category
    json_path = category_dir / "cache_embeddings.json"

    client = create_mqtt_client()
    client.connect(BROKER_IP, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        emb, frame, dims, ts = wait_for_embedding(args.wait_sec)
        if emb is None:
            print(
                json.dumps({
                    "status": "error",
                    "message": f"no embedding received within {args.wait_sec:.0f}s",
                    "category": category,
                }),
                flush=True,
            )
            return 1

        objects = load_existing_objects(json_path)
        sample_id = next_sample_id(objects)
        encoded = encode_embedding(emb)

        obj = {
            "label": args.label,
            "sample_id": sample_id,
            "model": MODEL,
            "pretrained": PRETRAINED,
            "embedding_dim": int(dims),
            "threshold": THRESHOLD,
            "normalised": True,
            "dtype": "float32",
            "source": "live_camera_snapshot",
            "frame": frame,
            "data": encoded,
            "created_at": time.time(),
        }
        objects.append(obj)
        write_cache_atomic(json_path, objects)

        print(
            json.dumps({
                "status": "ok",
                "category": category,
                "sample_id": sample_id,
                "total": len(objects),
                "label": args.label,
                "embedding_dim": int(dims),
                "frame": frame,
                "path": str(json_path),
            }),
            flush=True,
        )
        return 0
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
