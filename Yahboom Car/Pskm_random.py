# webrtc_server.py
import asyncio
import base64
import json
import cv2
import av
import socket
import threading
import time

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack

import open_clip
import torch
import numpy as np
from PIL import Image

import paho.mqtt.client as mqtt


# =========================
# SETTINGS
# =========================

CAMERA_INDEX = 0
WIDTH = 640
HEIGHT = 480
FPS = 30
PORT = 8080

BROKER_IP = "localhost"
BROKER_PORT = 1883

# VIT / embedding topics
TOPIC_CLIP = "yahboom/vit/embedding"
TOPIC_STATUS = "yahboom/vit/status"
TOPIC_COMMAND = "yahboom/vit/command"

# Robot command topic
# IMPORTANT:
# Your mqtt_ros_node.py must listen to this topic
# and convert "stop" into /cmd_vel zero.
TOPIC_ROBOT_CMD = "yahboom/cmd"
STOP_COMMAND = "stop"

INFERENCE_EVERY_N_FRAMES = 5
SHOW_PREVIEW = False


# =========================
# EDGE BOTTLE DETECTION SETTINGS
# =========================

BOTTLE_DETECTION_ENABLED = True

# If bottle detection is too weak, lower these slightly.
# If it stops wrongly too often, increase these.
BOTTLE_SCORE_THRESHOLD = 0.50
BOTTLE_MARGIN_THRESHOLD = 0.05

# Prevent repeated stop spam
STOP_COOLDOWN_SECONDS = 2.0


# =========================
# EMBEDDING SETTINGS
# Options:
# 512 bytes  -> 128 dims
# 1024 bytes -> 256 dims
# 2048 bytes -> 512 dims full
#
# Can be changed at runtime via MQTT command:
# embds1 / embds2 / embds3
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
    """
    Switch embedding size at runtime.
    Thread-safe.
    """
    global _current_embedding_bytes, _current_target_dims

    if new_bytes not in EMBEDDING_BYTES_TO_DIMS:
        print(f"[CMD] Invalid embedding bytes: {new_bytes}. Ignoring.")
        return

    with embedding_lock:
        _current_embedding_bytes = new_bytes
        _current_target_dims = EMBEDDING_BYTES_TO_DIMS[new_bytes]

    print(
        f"[CMD] Embedding switched -> {new_bytes} bytes "
        f"| {EMBEDDING_BYTES_TO_DIMS[new_bytes]} dims"
    )


# =========================
# GLOBALS
# =========================

pcs = set()
camera = None
latest_frame = None
frame_lock = threading.Lock()
stop_event = threading.Event()

model = None
preprocess = None
device = None
mqtt_client = None

# Text-side features for bottle detection
tokenizer = None
text_features = None
text_labels = None
bottle_label_indexes = []
non_bottle_label_indexes = []

last_stop_time = 0.0


# =========================
# CAMERA SETUP
# =========================

def get_local_ip():
    """
    Gets the Raspberry Pi's current local IP address.
    Used only for printing the dashboard link.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
        return ip_address
    except Exception:
        return "127.0.0.1"


def init_camera():
    global camera

    if camera is not None and camera.isOpened():
        return camera

    print(f"[INFO] Opening camera index {CAMERA_INDEX}...")

    camera = cv2.VideoCapture(CAMERA_INDEX)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    camera.set(cv2.CAP_PROP_FPS, FPS)

    if not camera.isOpened():
        raise RuntimeError(
            f"Could not open camera index {CAMERA_INDEX}. "
            f"Try changing CAMERA_INDEX to 1, 2, or 3."
        )

    print("[INFO] Camera opened successfully.")
    return camera


# =========================
# MODEL
# =========================

def load_model():
    """
    Loads MobileCLIP-S1 and prepares text prompts for bottle detection.
    This lets the Pi do edge-side detection before sending stop command.
    """
    global model, preprocess, device
    global tokenizer, text_features, text_labels
    global bottle_label_indexes, non_bottle_label_indexes

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model, _, preprocess = open_clip.create_model_and_transforms(
        "MobileCLIP-S1",
        pretrained="datacompdr",
        device=device
    )

    tokenizer = open_clip.get_tokenizer("MobileCLIP-S1")
    model.eval()

    # Bottle prompts
    # Descriptive prompts usually work better than only "bottle".
    bottle_prompts = [
        "a plastic water bottle",
        "a bottle on the floor",
        "a clear water bottle",
        "a blue water bottle",
        "a drinking bottle",
        "a bottle in front of a robot",
        "a small bottle on the ground",
        "a water bottle obstacle",
    ]

    # Non-bottle prompts
    non_bottle_prompts = [
        "an empty floor",
        "a wall",
        "a chair",
        "a table",
        "a box",
        "a person",
        "a room with no bottle",
        "an obstacle that is not a bottle",
        "a robot view with no object",
        "a plain indoor room",
    ]

    text_labels = bottle_prompts + non_bottle_prompts

    bottle_label_indexes = list(range(len(bottle_prompts)))
    non_bottle_label_indexes = list(range(len(bottle_prompts), len(text_labels)))

    with torch.no_grad():
        text_tokens = tokenizer(text_labels).to(device)
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    print(f"[INFO] Loaded MobileCLIP-S1 on {device}")
    print("[INFO] Bottle edge detection prompts loaded.")
    print(f"[INFO] Bottle prompts: {len(bottle_prompts)}")
    print(f"[INFO] Non-bottle prompts: {len(non_bottle_prompts)}")


# =========================
# MQTT
# =========================

def publish_status(client, status, extra=None):
    payload = {
        "status": status,
        "timestamp": time.time()
    }

    if extra:
        payload.update(extra)

    try:
        client.publish(TOPIC_STATUS, json.dumps(payload), qos=0)
    except Exception as e:
        print("[WARNING] Failed to publish status:", e)


def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()

    def _on_connect(c, _ud, _flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected to {BROKER_IP}:{BROKER_PORT}")

            c.subscribe(TOPIC_COMMAND, qos=0)

            print(f"[MQTT] Subscribed to command topic: {TOPIC_COMMAND}")
            print(f"[MQTT] Publishing embeddings -> {TOPIC_CLIP}")
            print(f"[MQTT] Publishing status     -> {TOPIC_STATUS}")
            print(f"[MQTT] Robot stop topic      -> {TOPIC_ROBOT_CMD}")
        else:
            print(f"[MQTT] Connect failed. rc={rc}")

    def _on_message(c, _ud, msg):
        """
        Handles incoming MQTT commands.
        Currently supports:
        embds1 -> 128 dims
        embds2 -> 256 dims
        embds3 -> 512 dims
        """
        try:
            command = msg.payload.decode("utf-8").strip().lower()
            print(f"[CMD] Received command: '{command}' on {msg.topic}")

            if command in COMMAND_TO_EMBEDDING_BYTES:
                new_bytes = COMMAND_TO_EMBEDDING_BYTES[command]
                set_embedding_size(new_bytes)

                publish_status(
                    c,
                    "embedding_size_changed",
                    {
                        "command": command,
                        "embedding_bytes": new_bytes,
                        "target_dims": EMBEDDING_BYTES_TO_DIMS[new_bytes],
                    },
                )

            else:
                print(
                    f"[CMD] Unknown command: '{command}'. "
                    "Valid commands: embds1, embds2, embds3"
                )

        except Exception as e:
            print(f"[CMD] Error handling command: {e}")

    client.on_connect = _on_connect
    client.on_message = _on_message

    return client


# =========================
# EMBEDDING
# normalize -> slice -> re-normalize
# =========================

def get_embedding(frame):
    """
    Creates image embedding from the current camera frame.
    This keeps your original VIT encoding function.
    """
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    img_tensor = preprocess(pil_img).unsqueeze(0).to(device)

    with torch.no_grad():
        emb = model.encode_image(img_tensor.float())

    # Normalize full 512-dim embedding
    emb = emb / emb.norm(dim=-1, keepdim=True)

    # Runtime-selected target dims
    target_dims = get_target_dims()

    # Slice embedding
    emb = emb[:, :target_dims]

    # Re-normalize after slicing
    emb = emb / emb.norm(dim=-1, keepdim=True)

    return emb.cpu().numpy().astype(np.float32)


# =========================
# EDGE BOTTLE DETECTION
# =========================

def detect_bottle_on_edge(frame):
    """
    Runs bottle detection directly on the Pi using MobileCLIP.

    This does not use a YOLO bounding box.
    It compares the whole camera frame against bottle text prompts.

    Returns:
        is_bottle: bool
        result: dict
    """
    global text_features

    if text_features is None:
        return False, {
            "error": "text_features_not_loaded"
        }

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    img_tensor = preprocess(pil_img).unsqueeze(0).to(device)

    with torch.no_grad():
        image_features = model.encode_image(img_tensor.float())
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)

        # Compare image embedding with text embeddings
        logits = 100.0 * image_features @ text_features.T
        probs = logits.softmax(dim=-1).cpu().numpy()[0]

    best_index = int(np.argmax(probs))
    best_label = text_labels[best_index]
    best_score = float(probs[best_index])

    bottle_score = float(max(probs[i] for i in bottle_label_indexes))
    non_bottle_score = float(max(probs[i] for i in non_bottle_label_indexes))
    margin = bottle_score - non_bottle_score

    is_bottle = (
        bottle_score >= BOTTLE_SCORE_THRESHOLD
        and margin >= BOTTLE_MARGIN_THRESHOLD
    )

    result = {
        "is_bottle": bool(is_bottle),
        "best_label": best_label,
        "best_score": best_score,
        "bottle_score": bottle_score,
        "non_bottle_score": non_bottle_score,
        "margin": margin,
        "bottle_score_threshold": BOTTLE_SCORE_THRESHOLD,
        "bottle_margin_threshold": BOTTLE_MARGIN_THRESHOLD,
    }

    return is_bottle, result


def stop_robot_because_bottle(detection_result):
    """
    Sends a stop command to the robot via MQTT.

    IMPORTANT:
    mqtt_ros_node.py must receive this message and publish /cmd_vel zero.
    """
    global last_stop_time

    now = time.time()

    if now - last_stop_time < STOP_COOLDOWN_SECONDS:
        return

    last_stop_time = now

    try:
        mqtt_client.publish(TOPIC_ROBOT_CMD, STOP_COMMAND, qos=0)

        publish_status(
            mqtt_client,
            "bottle_detected_stop_sent",
            {
                "robot_cmd_topic": TOPIC_ROBOT_CMD,
                "stop_command": STOP_COMMAND,
                "detection": detection_result,
            },
        )

        print(
            "[EDGE AI] Bottle detected. "
            f"Stop command sent to {TOPIC_ROBOT_CMD}: {STOP_COMMAND}"
        )

    except Exception as e:
        print("[EDGE AI] Failed to send stop command:", e)


# =========================
# WORKERS
# =========================

def camera_worker():
    """
    Continuously reads frames from camera.
    Latest frame is shared between WebRTC and VIT worker.
    """
    global latest_frame

    cap = init_camera()

    while not stop_event.is_set():
        ret, frame = cap.read()

        if not ret:
            print("[CAMERA] Failed to read frame.")
            time.sleep(0.05)
            continue

        with frame_lock:
            latest_frame = frame.copy()

        if SHOW_PREVIEW:
            cv2.imshow("Camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                stop_event.set()
                break


def vit_worker():
    """
    Runs MobileCLIP inference.

    It does two things:
    1. Edge bottle detection on the Pi.
    2. Keeps publishing image embeddings to MQTT.
    """
    global mqtt_client

    frame_count = 0
    embedding_count = 0

    while not stop_event.is_set():
        with frame_lock:
            frame = None if latest_frame is None else latest_frame.copy()

        if frame is None:
            time.sleep(0.01)
            continue

        frame_count += 1

        if frame_count % INFERENCE_EVERY_N_FRAMES == 0:
            try:
                # =========================
                # 1. Edge bottle detection
                # =========================

                bottle_detected = False
                detection_result = {}

                if BOTTLE_DETECTION_ENABLED:
                    bottle_detected, detection_result = detect_bottle_on_edge(frame)

                    publish_status(
                        mqtt_client,
                        "edge_detection_result",
                        {
                            "frame": frame_count,
                            "detection": detection_result,
                        },
                    )

                    print(
                        "[EDGE AI] "
                        f"frame={frame_count} "
                        f"bottle={detection_result.get('is_bottle')} "
                        f"bottle_score={detection_result.get('bottle_score'):.3f} "
                        f"non_bottle_score={detection_result.get('non_bottle_score'):.3f} "
                        f"margin={detection_result.get('margin'):.3f} "
                        f"best='{detection_result.get('best_label')}'"
                    )

                    if bottle_detected:
                        stop_robot_because_bottle(detection_result)

                # =========================
                # 2. Original embedding publish
                # =========================

                embedding = get_embedding(frame)
                raw_bytes = embedding.tobytes()

                image_file_size = int(frame.nbytes)
                emb_bytes = get_embedding_bytes()
                target_dims = get_target_dims()

                payload = json.dumps(
                    {
                        "raw_bytes": len(raw_bytes),
                        "embedding_dim": int(embedding.shape[-1]),
                        "embedding_bytes": emb_bytes,
                        "dtype": "float32",
                        "frame": frame_count,
                        "image_file_size": image_file_size,
                        "data": base64.b64encode(raw_bytes).decode("utf-8"),
                        "edge_detection": detection_result,
                    }
                )

                mqtt_client.publish(TOPIC_CLIP, payload, qos=0)
                embedding_count += 1

                if embedding_count % 10 == 0:
                    print(
                        f"[VIT] Published {embedding_count} embeddings "
                        f"| dims={target_dims} "
                        f"| embedding={len(raw_bytes)} B "
                        f"| frame={frame_count}"
                    )

                publish_status(
                    mqtt_client,
                    "running",
                    {
                        "frames_seen": frame_count,
                        "embeddings_sent": embedding_count,
                        "embedding_shape": list(embedding.shape),
                        "embedding_bytes": emb_bytes,
                        "embedding_size_bytes": len(raw_bytes),
                        "dtype": str(embedding.dtype),
                        "topic": TOPIC_CLIP,
                        "image_file_size": image_file_size,
                        "edge_detection": detection_result,
                    },
                )

            except Exception as e:
                print("[ERROR] During VIT / bottle detection:", e)

                publish_status(
                    mqtt_client,
                    "embedding_error",
                    {
                        "error": str(e),
                        "frame_count": frame_count,
                    },
                )

        time.sleep(0.001)


# =========================
# WEBRTC VIDEO TRACK
# =========================

class CameraVideoTrack(VideoStreamTrack):
    """
    Sends camera frames to the browser using WebRTC.
    """

    def __init__(self):
        super().__init__()

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        with frame_lock:
            frame = None if latest_frame is None else latest_frame.copy()

        if frame is None:
            frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

        frame = cv2.resize(frame, (WIDTH, HEIGHT))

        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame


# =========================
# WEB PAGE
# =========================

async def index(request):
    html = """<!DOCTYPE html>
<html>
<head>
  <title>Yahboom WebRTC Video</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      background: #111;
      color: white;
      text-align: center;
      margin: 0;
      padding: 20px;
    }

    video {
      width: 90%;
      max-width: 900px;
      background: black;
      border: 3px solid white;
      border-radius: 12px;
    }

    button {
      margin-top: 18px;
      padding: 12px 24px;
      font-size: 18px;
      border-radius: 8px;
      border: none;
      cursor: pointer;
    }

    #status {
      margin-bottom: 20px;
      font-size: 18px;
      color: #ccc;
    }
  </style>
</head>
<body>
  <h1>Yahboom WebRTC Live Video</h1>
  <div id="status">Starting video...</div>
  <video id="video" autoplay playsinline muted></video><br>
  <button onclick="restartVideo()">Restart Video</button>

<script>
let pc = null;

async function startVideo() {
  try {
    document.getElementById("status").innerText = "Creating WebRTC connection...";

    pc = new RTCPeerConnection();

    pc.ontrack = function(event) {
      const video = document.getElementById("video");
      video.srcObject = event.streams[0];
      document.getElementById("status").innerText = "Video connected";
    };

    pc.onconnectionstatechange = function() {
      document.getElementById("status").innerText =
        "Connection state: " + pc.connectionState;
    };

    pc.addTransceiver("video", { direction: "recvonly" });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const response = await fetch("/offer", {
      method: "POST",
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type
      }),
      headers: {
        "Content-Type": "application/json"
      }
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const answer = await response.json();
    await pc.setRemoteDescription(answer);

  } catch (error) {
    document.getElementById("status").innerText =
      "Failed to start video: " + error.message;
  }
}

async function restartVideo() {
  if (pc) {
    pc.close();
  }

  pc = null;
  document.getElementById("video").srcObject = null;
  document.getElementById("status").innerText = "Restarting video...";

  await startVideo();
}

window.onload = startVideo;
</script>
</body>
</html>"""

    return web.Response(content_type="text/html", text=html)


# =========================
# ROUTES
# =========================

async def offer(request):
    try:
        params = await request.json()

        offer_desc = RTCSessionDescription(
            sdp=params["sdp"],
            type=params["type"]
        )

        pc = RTCPeerConnection()
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            print(f"[WEBRTC] Connection state: {pc.connectionState}")

            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                pcs.discard(pc)

        pc.addTrack(CameraVideoTrack())

        await pc.setRemoteDescription(offer_desc)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                }
            ),
        )

    except Exception as e:
        print("[WEBRTC] Offer error:", e)
        return web.Response(status=500, text=str(e))


async def on_shutdown(app):
    stop_event.set()

    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()

    global camera, mqtt_client

    if camera is not None:
        camera.release()
        camera = None

    if SHOW_PREVIEW:
        cv2.destroyAllWindows()

    if mqtt_client is not None:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    print("[INFO] Shutdown complete.")


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
        print(
            f"[MQTT] ERROR: could not reach broker at "
            f"{BROKER_IP}:{BROKER_PORT}: {exc}"
        )
        raise

    publish_status(
        mqtt_client,
        "vit_encoder_started",
        {
            "model": "MobileCLIP-S1",
            "device": str(device),
            "embedding_topic": TOPIC_CLIP,
            "command_topic": TOPIC_COMMAND,
            "robot_cmd_topic": TOPIC_ROBOT_CMD,
            "stop_command": STOP_COMMAND,
            "embedding_shape": [1, get_target_dims()],
            "embedding_bytes": get_embedding_bytes(),
            "dtype": "float32",
            "inference_every_n_frames": INFERENCE_EVERY_N_FRAMES,
            "bottle_detection_enabled": BOTTLE_DETECTION_ENABLED,
            "bottle_score_threshold": BOTTLE_SCORE_THRESHOLD,
            "bottle_margin_threshold": BOTTLE_MARGIN_THRESHOLD,
            "valid_commands": list(COMMAND_TO_EMBEDDING_BYTES.keys()),
        },
    )

    threading.Thread(target=camera_worker, daemon=True).start()
    threading.Thread(target=vit_worker, daemon=True).start()

    app = web.Application()

    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)

    app.on_shutdown.append(on_shutdown)

    ip_address = get_local_ip()
    dashboard_url = f"http://{ip_address}:{PORT}"

    print("\n====================================")
    print("[DASHBOARD]", dashboard_url)
    print("[EDGE AI] Bottle detection enabled:", BOTTLE_DETECTION_ENABLED)
    print("[EDGE AI] Stop topic:", TOPIC_ROBOT_CMD)
    print("[EDGE AI] Stop command:", STOP_COMMAND)
    print("====================================\n")

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
