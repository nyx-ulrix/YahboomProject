# webrtc_server.py
# This script:
# 1. Opens the camera
# 2. Streams video using WebRTC
# 3. Publishes camera frames to MQTT for VIT.py

import asyncio
import base64
import json
import socket
import threading
import time

import av
import cv2
import numpy as np
import paho.mqtt.client as mqtt
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack


# =========================
# SETTINGS
# =========================
CAMERA_INDEX = 0
WIDTH = 320
HEIGHT = 240
FPS = 15
PORT = 8080

BROKER_IP = "localhost"
BROKER_PORT = 1883

TOPIC_CAMERA_FRAME = "yahboom/camera/frame"

JPEG_QUALITY = 70
PUBLISH_FRAME_EVERY_N_FRAMES = 1


# =========================
# GLOBALS
# =========================
pcs = set()
camera = None
latest_frame = None
frame_lock = threading.Lock()
stop_event = threading.Event()

mqtt_client = None
mqtt_connected = False


# =========================
# HELPER: GET LOCAL IP
# =========================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
        return ip_address
    except Exception:
        return "127.0.0.1"


# =========================
# CAMERA SETUP
# =========================
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
# MQTT SETUP
# =========================
def create_mqtt_client():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()

    def _on_connect(c, _userdata, _flags, rc):
        global mqtt_connected

        if rc == 0:
            mqtt_connected = True
            print(f"[MQTT] Connected to {BROKER_IP}:{BROKER_PORT}")
            print(f"[MQTT] Publishing camera frames -> {TOPIC_CAMERA_FRAME}")
        else:
            mqtt_connected = False
            print(f"[MQTT] Connect failed with rc={rc}")

    def _on_disconnect(c, _userdata, rc):
        global mqtt_connected
        mqtt_connected = False
        print(f"[MQTT] Disconnected rc={rc}")

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect

    return client


def publish_camera_frame(frame, frame_id):
    """
    Sends camera frame to VIT.py using MQTT.
    Frame is compressed as JPEG first.
    """

    if mqtt_client is None or not mqtt_connected:
        return

    try:
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        ok, jpg = cv2.imencode(".jpg", frame, encode_params)

        if not ok:
            print("[MQTT] Failed to encode camera frame.")
            return

        payload = {
            "frame_id": frame_id,
            "timestamp": time.time(),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "format": "jpg",
            "jpg_b64": base64.b64encode(jpg.tobytes()).decode("utf-8"),
        }

        mqtt_client.publish(TOPIC_CAMERA_FRAME, json.dumps(payload), qos=0)

    except Exception as e:
        print(f"[MQTT] Failed to publish camera frame: {e}")


# =========================
# CAMERA WORKER
# =========================
def camera_worker():
    global latest_frame

    cap = init_camera()
    frame_id = 0

    while not stop_event.is_set():
        ret, frame = cap.read()

        if not ret:
            time.sleep(0.05)
            continue

        frame = cv2.resize(frame, (WIDTH, HEIGHT))

        with frame_lock:
            latest_frame = frame.copy()

        frame_id += 1

        if frame_id % PUBLISH_FRAME_EVERY_N_FRAMES == 0:
            publish_camera_frame(frame, frame_id)

        time.sleep(0.001)


# =========================
# WEBRTC VIDEO TRACK
# =========================
class CameraVideoTrack(VideoStreamTrack):
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
# WEBRTC OFFER ROUTE
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
            text=json.dumps({
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type
            }),
        )

    except Exception as e:
        return web.Response(status=500, text=str(e))


# =========================
# SHUTDOWN
# =========================
async def on_shutdown(app):
    stop_event.set()

    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()

    global camera, mqtt_client

    if camera is not None:
        camera.release()
        camera = None
        print("[INFO] Camera released.")

    if mqtt_client is not None:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            print("[MQTT] Disconnected.")
        except Exception:
            pass


# =========================
# MAIN
# =========================
def main():
    global mqtt_client

    mqtt_client = create_mqtt_client()

    try:
        mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
        mqtt_client.loop_start()
    except Exception as exc:
        print(f"[MQTT] WARNING: Could not connect to broker: {exc}")
        print("[MQTT] WebRTC still runs, but VIT.py will not receive frames.")

    threading.Thread(target=camera_worker, daemon=True).start()

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.on_shutdown.append(on_shutdown)

    ip_address = get_local_ip()
    dashboard_url = f"http://{ip_address}:{PORT}"

    print(f"\n[DASHBOARD] {dashboard_url}\n")
    print("[INFO] Run VIT separately using:")
    print("       python3 VIT.py\n")

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()

