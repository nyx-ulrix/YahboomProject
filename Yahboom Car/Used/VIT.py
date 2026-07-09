
# VIT.py
# This script:
# 1. Receives camera frames from webrtc_server.py through MQTT
# 2. Runs MobileCLIP-S1 embedding
# 3. Publishes embedding data to MQTT
# 4. Listens for embedding size commands

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

TOPIC_CAMERA_FRAME = "yahboom/camera/frame"
TOPIC_CLIP = "yahboom/vit/embedding"
TOPIC_STATUS = "yahboom/vit/status"
TOPIC_COMMAND = "yahboom/vit/command"

INFERENCE_EVERY_N_FRAMES = 5
SHOW_PREVIEW = False


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
# GET EMBEDDING
# =========================
def get_embedding(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    img_tensor = preprocess(pil_img).unsqueeze(0).to(device)

    with torch.no_grad():
        emb = model.encode_image(img_tensor.float())

    # Normalize full embedding
    emb = emb / emb.norm(dim=-1, keepdim=True)

    # Slice embedding
    target_dims = get_target_dims()
    emb = emb[:, :target_dims]

    # Re-normalize after slicing
    emb = emb / emb.norm(dim=-1, keepdim=True)

    return emb.cpu().numpy().astype(np.float32)


# =========================
# PUBLISH STATUS
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


# =========================
# DECODE CAMERA FRAME
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
# MQTT SETUP
# =========================
def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()

    def _on_connect(c, _userdata, _flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected to {BROKER_IP}:{BROKER_PORT}")

            c.subscribe(TOPIC_CAMERA_FRAME, qos=0)
            c.subscribe(TOPIC_COMMAND, qos=0)

            print(f"[MQTT] Subscribed camera frames <- {TOPIC_CAMERA_FRAME}")
            print(f"[MQTT] Subscribed commands      <- {TOPIC_COMMAND}")
            print(f"[MQTT] Publishing embeddings   -> {TOPIC_CLIP}")
            print(f"[MQTT] Publishing status       -> {TOPIC_STATUS}")

            publish_status(
                "vit_encoder_started",
                {
                    "model": "MobileCLIP-S1",
                    "device": str(device),
                    "camera_frame_topic": TOPIC_CAMERA_FRAME,
                    "embedding_topic": TOPIC_CLIP,
                    "command_topic": TOPIC_COMMAND,
                    "embedding_shape": [1, get_target_dims()],
                    "embedding_bytes": get_embedding_bytes(),
                    "dtype": "float32",
                    "inference_every_n_frames": INFERENCE_EVERY_N_FRAMES,
                    "valid_commands": list(COMMAND_TO_EMBEDDING_BYTES.keys()),
                },
            )

        else:
            print(f"[MQTT] Connect failed rc={rc}")

    def _on_message(c, _userdata, msg):
        global latest_frame, latest_frame_id, frames_received

        if msg.topic == TOPIC_COMMAND:
            try:
                command = msg.payload.decode("utf-8").strip().lower()
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
                else:
                    print("[CMD] Unknown command. Use embds1, embds2, or embds3.")

            except Exception as e:
                print(f"[CMD] Error handling command: {e}")

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

    client.on_connect = _on_connect
    client.on_message = _on_message

    return client


# =========================
# VIT WORKER
# =========================
def vit_worker():
    global embeddings_sent

    last_processed_frame_id = -1

    while not stop_event.is_set():
        with frame_lock:
            frame = None if latest_frame is None else latest_frame.copy()
            frame_id = latest_frame_id

        if frame is None:
            time.sleep(0.01)
            continue

        # Do not process the same frame again
        if frame_id == last_processed_frame_id:
            time.sleep(0.005)
            continue

        last_processed_frame_id = frame_id

        # Only run inference every N frames
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

            mqtt_client.publish(TOPIC_CLIP, json.dumps(payload), qos=0)

            embeddings_sent += 1

            if embeddings_sent % 10 == 0:
                print(
                    f"[VIT] Published {embeddings_sent} embeddings "
                    f"| dims={target_dims} "
                    f"| embedding size={len(raw_bytes)} B "
                    f"| frame_id={frame_id}"
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
                },
            )

        except Exception as e:
            print(f"[VIT] ERROR during embedding publish: {e}")

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
    global mqtt_client

    load_model()

    mqtt_client = create_mqtt_client()

    try:
        mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
        mqtt_client.loop_start()
    except Exception as exc:
        print(f"[MQTT] ERROR: Could not connect to broker: {exc}")
        raise

    worker_thread = threading.Thread(target=vit_worker, daemon=True)
    worker_thread.start()

    print("\n[VIT] VIT.py is running.")
    print("[VIT] Waiting for camera frames from webrtc_server.py...")
    print("[VIT] Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[VIT] Stopping...")
        stop_event.set()

    finally:
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()

        if mqtt_client is not None:
            try:
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

