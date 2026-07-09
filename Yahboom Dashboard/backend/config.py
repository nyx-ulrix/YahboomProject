"""
Configuration settings for the Yahboom Dashboard backend
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# Yahboom WiFi / Raspberry Pi MQTT broker
DEFAULT_BROKER_IP = os.getenv("MQTT_BROKER_IP", "raspberrypi.local")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
TOPIC = os.getenv("MQTT_TOPIC", "yahboom/cmd")
SAFETY_TOPIC = os.getenv("MQTT_SAFETY_TOPIC", "yahboom/safety/status")
GRID_TOPIC = os.getenv("MQTT_GRID_TOPIC", "yahboom/grid")
DRIVE_STATUS_TOPIC = os.getenv("MQTT_DRIVE_STATUS_TOPIC", "yahboom/drive/status")
CACHE_AWARE_READY_TOPIC = os.getenv(
    "MQTT_CACHE_AWARE_READY_TOPIC", "yahboom/cache_aware/ready")
DETECT_STATUS_TOPIC = os.getenv("MQTT_DETECT_STATUS_TOPIC", "yahboom/detect/status")
# Flask settings
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "3000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")

# Raspberry Pi SSH settings (used for cache_aware_offloading.py on the test bench)
PI_SSH_USER = os.getenv("PI_SSH_USER",          "pi")
PI_SSH_PASSWORD = os.getenv("PI_SSH_PASSWORD",      "raspberry")
# optional: path to private key file
PI_SSH_KEY_PATH = os.getenv("PI_SSH_KEY_PATH",      "")
VIDEO_SERVER_PORT = int(os.getenv("VIDEO_SERVER_PORT", "8080"))
# WebRTC server prints a base URL; leave empty so /video_feed is not appended.
VIDEO_FEED_PATH = os.getenv("VIDEO_FEED_PATH", "")
# True = legacy MJPEG relay (/api/video_feed); False = use [DASHBOARD LINK] URL in browser.
VIDEO_USE_MJPEG_RELAY = os.getenv("VIDEO_USE_MJPEG_RELAY", "false").lower() in (
    "true", "1", "yes",
)
# Clients load video from this backend route (hub); Pi URL is upstream only.
PUBLIC_VIDEO_FEED_PATH = os.getenv("PUBLIC_VIDEO_FEED_PATH", "/api/video_feed")
PROBE_CACHE_TTL_SEC = float(os.getenv("PROBE_CACHE_TTL_SEC", "4"))
# Terminal emulator to open on the Pi's display (lxterminal for LXDE, xterm as fallback)
PI_TERMINAL = os.getenv("PI_TERMINAL",          "lxterminal")
# Force X display for SSH-launched terminals (e.g. ":1" for RealVNC); empty = auto-detect
PI_DISPLAY = os.getenv("PI_DISPLAY", "").strip()

# MQTT settings
MQTT_TIMEOUT = int(os.getenv("MQTT_TIMEOUT", "60"))
# Seconds to wait for MQTT publish ack (0 = fire-and-forget, lowest movement latency).
PUBLISH_TIMEOUT = float(os.getenv("MQTT_PUBLISH_TIMEOUT", "0"))
VIDEO_PROBE_INTERVAL_SEC = float(os.getenv("VIDEO_PROBE_INTERVAL_SEC", "12"))

# Event log retention (0 = unlimited)
EVENT_LOG_MAX = int(os.getenv("EVENT_LOG_MAX", "0"))
# Per-event message truncation (0 = no truncation)
EVENT_LOG_MESSAGE_MAXLEN = int(os.getenv("EVENT_LOG_MESSAGE_MAXLEN", "0"))

# Camera commands are exempt from the e-stop movement lock.
CAMERA_COMMANDS = {
    "up", "down", "cright", "cleft", "upcright", "upcleft", "downcright", "downcleft",
    "crst", "cstop",
}

AUTO_COMMANDS = {"auto_on", "auto_off"}
ESTOP_COMMANDS = {"estop_on", "estop_off"}

# VIT encoder venv on the Pi (shared with cache_aware_offloading.py)
PI_VIT_VENV = os.getenv("PI_VIT_VENV", "~/vit_env/bin/activate")

# Cache-aware offloading script on the Pi (test bench — cache aware stop mode)
PI_CACHE_AWARE_SCRIPT_PATH = os.getenv(
    "PI_CACHE_AWARE_SCRIPT_PATH", "cache_aware_offloading.py")
PI_CACHE_AWARE_LOG = os.getenv(
    "PI_CACHE_AWARE_LOG", "/tmp/yahboom_cache_aware.log")
PI_CACHE_AWARE_TERMINAL_TITLE = os.getenv(
    "PI_CACHE_AWARE_TERMINAL_TITLE", "Cache Aware Offloading")
CACHE_SCRIPT_START_TIMEOUT_SEC = float(os.getenv("CACHE_SCRIPT_START_TIMEOUT_SEC", "30"))
CACHE_SCRIPT_START_POLL_SEC = float(os.getenv("CACHE_SCRIPT_START_POLL_SEC", "1"))
# Pi log line that unlocks test-bench START in cache-aware offloading mode (see cache_aware_offloading.py).
CACHE_SCRIPT_EMBEDDING_READY_SNIPPET = os.getenv(
    "CACHE_SCRIPT_EMBEDDING_READY_SNIPPET",
    "[DETECT] Text embedding ready: 'a water bottle'",
)

# VIT / MobileCLIP scene decoder MQTT topics (see vit_service.py)
VIT_EMBEDDING_TOPIC = os.getenv(
    "MQTT_VIT_EMBEDDING_TOPIC", "yahboom/vit/embedding")
VIT_CLIP_EMBEDDING_TOPIC = os.getenv(
    "MQTT_VIT_CLIP_EMBEDDING_TOPIC", "yahboom/clip_embedding")
VIT_STATUS_TOPIC = os.getenv("MQTT_VIT_STATUS_TOPIC",    "yahboom/vit/status")
VIT_ROBOT_STATUS_TOPIC = os.getenv(
    "MQTT_VIT_ROBOT_STATUS_TOPIC", "yahboom/status")
VIT_RESULT_TOPIC = os.getenv("MQTT_VIT_RESULT_TOPIC",    "yahboom/vit/result")
VIT_CONFIG_TOPIC = os.getenv("MQTT_VIT_CONFIG_TOPIC",    "yahboom/vit/config")
VIT_COMMAND_TOPIC = os.getenv("MQTT_VIT_COMMAND_TOPIC",  "yahboom/vit/command")
# CLIP-style zero-shot is softer than detector confidence; 60% is a sensible default.
VIT_CONFIDENCE_THRESHOLD = float(os.getenv("VIT_CONFIDENCE_THRESHOLD", "60.0"))
# Optional: set VIT_EMBED_DIM in .env to force 128/256/512-dim decode (default: auto per payload).

# SLAM settings
# yahboom/scan  – raw LaserScan JSON (angle_min, angle_increment, ranges[])
# yahboom/grid  – existing occupancy-grid JSON (fallback, same as GRID_TOPIC)
SLAM_SCAN_TOPIC = os.getenv("SLAM_SCAN_TOPIC",       "yahboom/scan")
SLAM_OUTPUT_FILE = os.getenv("SLAM_OUTPUT_FILE",
                             str(Path(__file__).parent / "slam_map.json"))
SLAM_MAP_SIZE_M = float(os.getenv("SLAM_MAP_SIZE_M",  "20.0"))
SLAM_RESOLUTION_M = float(os.getenv("SLAM_RESOLUTION_M", "0.05"))

# Allowed commands
ALLOWED_COMMANDS = [
    # Movement
    "fwd", "bck", "left", "right", "fwdleft", "fwdright", "bckleft", "bckright", "stop",
    # Camera
    *CAMERA_COMMANDS,
    # Autonomous movement
    *AUTO_COMMANDS,
    # Emergency stop latch
    *ESTOP_COMMANDS,
]
