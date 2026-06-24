# detector.py
# Runs on the Yahboom Pi 5 alongside webrtc_server.py
# Subscribes to CLIP embeddings, compares against a text label,
# and publishes a stop command when similarity exceeds threshold.

import json
import time
import base64
import threading
import numpy as np
import open_clip
import torch
import paho.mqtt.client as mqtt

# =========================
# SETTINGS
# =========================
BROKER_IP   = "localhost"
BROKER_PORT = 1883

# Topics must match webrtc_server.py and mqtt_ros_node.py
TOPIC_EMBEDDING = "yahboom/vit/embedding"   # subscribe  receive CLIP embeddings
TOPIC_STATUS    = "yahboom/vit/status"      # subscribe  receive status updates
TOPIC_COMMAND   = "yahboom/cmd"             # publish   send stop command
TOPIC_DETECT    = "yahboom/detect/status"   # publish   detection events

# Detection config
DETECTION_LABEL     = "a water bottle"
DETECTION_THRESHOLD = 0.25   # cosine similarity threshold  tune this
AUTO_OFF_COMMAND    =  "Auto_off"
STOP_COMMAND        = "stop"

# Cooldown: seconds to wait after a detection before detecting again.
# Prevents flooding mqtt_ros_node.py with repeated stop commands.
DETECTION_COOLDOWN_S = 2.0

# =========================
# GLOBALS
# =========================
model       = None
device      = None

# Text embedding  recomputed whenever embedding dims change
_text_embedding      = None
_text_embedding_dims = None   # track which dims the text embedding was built for
text_lock            = threading.Lock()

# Cooldown tracking
_last_detection_time = 0.0

mqtt_client = None


# =========================
# MODEL LOADER
# =========================
def load_model():
    """Load MobileCLIP-S1  same model as webrtc_server.py."""
    global model, device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    # Load text encoder only  we don't need image encoder here
    model, _, _ = open_clip.create_model_and_transforms(
        "MobileCLIP-S1", pretrained="datacompdr", device=device
    )
    model.eval()
    print(f"[INFO] Loaded MobileCLIP-S1 on {device}")


# =========================
# TEXT EMBEDDING
# =========================
def compute_text_embedding(target_dims: int):
    """
    Encode DETECTION_LABEL into the same vector space as incoming image embeddings.
    Must mirror the normalise , slice , re-normalise pipeline in webrtc_server.py.
    """
    global _text_embedding, _text_embedding_dims

    tokenizer  = open_clip.get_tokenizer("MobileCLIP-S1")
    text_tokens = tokenizer([DETECTION_LABEL]).to(device)

    with torch.no_grad():
        emb = model.encode_text(text_tokens)

    emb = emb / emb.norm(dim=-1, keepdim=True)   # normalise full 512
    emb = emb[:, :target_dims]                    # slice to match image dims
    emb = emb / emb.norm(dim=-1, keepdim=True)   # re-normalise after slice

    new_emb = emb.cpu().numpy().astype(np.float32).flatten()

    with text_lock:
        _text_embedding      = new_emb
        _text_embedding_dims = target_dims

    print(f"[DETECT] Text embedding ready: '{DETECTION_LABEL}' @ {target_dims} dims")


def get_text_embedding():
    with text_lock:
        return _text_embedding, _text_embedding_dims


# =========================
# DETECTION LOGIC
# =========================
def check_detection(image_embedding: np.ndarray, embedding_dims: int) -> float:
    """
    Compute cosine similarity between live image embedding and stored text embedding.
    Returns similarity score, or -1.0 if dims are mismatched or text embedding missing.
    """
    text_emb, text_dims = get_text_embedding()

    if text_emb is None:
        # Text embedding not yet computed  compute it now
        print(f"[DETECT] Text embedding missing  computing for {embedding_dims} dims")
        compute_text_embedding(embedding_dims)
        text_emb, text_dims = get_text_embedding()

    if text_dims != embedding_dims:
        # Dim mismatch incoming payload changed size, recompute text embedding
        print(f"[DETECT] Dim mismatch: text={text_dims}, image={embedding_dims}  recomputing")
        compute_text_embedding(embedding_dims)
        text_emb, text_dims = get_text_embedding()

    # Both vectors are already unit-normalised  dot product == cosine similarity
    similarity = float(np.dot(image_embedding, text_emb))
    return similarity


# =========================
# MQTT CALLBACKS
# =========================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Connected to broker at {BROKER_IP}:{BROKER_PORT}")
        client.subscribe(TOPIC_EMBEDDING, qos=0)
        client.subscribe(TOPIC_STATUS,    qos=0)
        print(f"[MQTT] Subscribed to: {TOPIC_EMBEDDING}")
        print(f"[MQTT] Subscribed to: {TOPIC_STATUS}")
        print(f"[MQTT] Publishing detections to: {TOPIC_DETECT}")
        print(f"[MQTT] Publishing stop command to: {TOPIC_COMMAND}")
    else:
        print(f"[MQTT] Connection failed (rc={rc})")


def on_message(client, userdata, msg):
    global _last_detection_time

    topic = msg.topic

    # Embedding payload
    if topic == TOPIC_EMBEDDING:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))

            # Auto-detect embedding dims from the payload field
            # webrtc_server.py always includes "embedding_dim" in the payload
            embedding_dims = int(payload["embedding_dim"])

            # Decode base64  numpy float32 vector
            raw_bytes      = base64.b64decode(payload["data"])
            image_embedding = np.frombuffer(raw_bytes, dtype=np.float32)

            # Safety check: confirm decoded length matches declared dims
            if len(image_embedding) != embedding_dims:
                print(
                    f"[WARN] Decoded embedding length {len(image_embedding)} "
                    f"!= declared dims {embedding_dims}. Skipping frame."
                )
                return

            # Run similarity check
            similarity = check_detection(image_embedding, embedding_dims)

            # Cooldown gate don't flood mqtt_ros_node.py with repeated stops
            now             = time.time()
            cooldown_active = (now - _last_detection_time) < DETECTION_COOLDOWN_S

            if similarity > DETECTION_THRESHOLD and not cooldown_active:
                _last_detection_time = now

                # Publish stop command to mqtt_ros_node.py
                client.publish(TOPIC_COMMAND, AUTO_OFF_COMMAND, STOP_COMMAND, qos=0)

                # Publish detection event for logging / dashboard
                detect_payload = json.dumps({
                    "label":      DETECTION_LABEL,
                    "similarity": round(similarity, 4),
                    "threshold":  DETECTION_THRESHOLD,
                    "stop_command":    STOP_COMMAND,
                    "auto_off_command": AUTO_OFF_COMMAND,
                    "dims":       embedding_dims,
                    "frame":      payload.get("frame", -1),
                    "timestamp":  now,
                })
                client.publish(TOPIC_DETECT, detect_payload, qos=0)

                print(
                    f"[DETECT] *** WATER BOTTLE DETECTED *** "
                    f"similarity={similarity:.4f} | dims={embedding_dims} | "
                    f"frame={payload.get('frame', '?')} | "
                    f"command='{STOP_COMMAND}' and '{AUTO_OFF_COMMAND}' sent to '{TOPIC_COMMAND}'"
                )

            else:
                # Log every 50 frames so you can monitor similarity in real time
                frame = payload.get("frame", 0)
                if isinstance(frame, int) and frame % 50 == 0:
                    cooldown_msg = " [cooldown active]" if cooldown_active else ""
                    print(
                        f"[DETECT] frame={frame} | similarity={similarity:.4f} | "
                        f"threshold={DETECTION_THRESHOLD} | dims={embedding_dims}"
                        f"{cooldown_msg}"
                    )

        except KeyError as e:
            print(f"[WARN] Missing field in embedding payload: {e}")
        except Exception as e:
            print(f"[ERROR] Failed to process embedding: {e}")

    # Status payload  useful for monitoring, no action needed
    elif topic == TOPIC_STATUS:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            status  = payload.get("status", "unknown")
            # Only log non-routine status messages
            if status not in ("running",):
                print(f"[STATUS] webrtc_server status: {status}")
        except Exception:
            pass


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[MQTT] Unexpected disconnect (rc={rc}) paho will auto-reconnect")


# =========================
# MQTT CLIENT SETUP
# =========================
def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()

    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    # Enable automatic reconnect important for long-running Pi process
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    return client


# =========================
# MAIN
# =========================
def main():
    global mqtt_client

    print("[INFO] Starting cache_aware_offloading.py")
    print(f"[INFO] Detection label : '{DETECTION_LABEL}'")
    print(f"[INFO] Threshold       : {DETECTION_THRESHOLD}")
    print(f"[INFO] Stop command    : '{STOP_COMMAND}' '{TOPIC_COMMAND}'")
    print(f"[INFO] Auto off command : '{AUTO_OFF_COMMAND}' '{TOPIC_COMMAND}'")
    print(f"[INFO] Cooldown        : {DETECTION_COOLDOWN_S}s")

    # Load model first text embedding computed on first message
    # (we don't know dims until first payload arrives)
    load_model()

    mqtt_client = create_mqtt_client()

    try:
        mqtt_client.connect(BROKER_IP, BROKER_PORT, keepalive=60)
    except Exception as e:
        print(f"[ERROR] Could not connect to broker at {BROKER_IP}:{BROKER_PORT}: {e}")
        raise

    print(f"[INFO] Connected  listening for embeddings on '{TOPIC_EMBEDDING}'")

    # loop_forever handles reconnects automatically
    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down detector.py")
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()

