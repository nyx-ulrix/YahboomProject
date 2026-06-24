"""
MQTT Service for connecting to Yahboom bot and sending commands
"""

import paho.mqtt.client as mqtt
from datetime import datetime, timezone
import time
import json
from config import (
    BROKER_PORT, DRIVE_STATUS_TOPIC, GRID_TOPIC, TOPIC, MQTT_TIMEOUT, PUBLISH_TIMEOUT,
    SAFETY_TOPIC, EVENT_LOG_MAX, EVENT_LOG_MESSAGE_MAXLEN,
)


class MQTTService:
    """Handles MQTT connection and communication with Yahboom bot"""

    def __init__(self):
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.broker_ip = None
        self.connected = False
        self.estop_active = False
        # After a manual resume, ignore stale MQTT latch messages briefly.
        self._ignore_estop_latch_until = 0.0
        self.stream_running = False
        # Keep latest values for both sources; API will prefer GRID_TOPIC.
        self.latest_grid_status = self._parse_grid_message("")
        self.latest_safety_status = self._parse_safety_message("")
        self.latest_drive_status = self._parse_drive_message("")
        # Pi MJPEG URL (single upstream for the relay ingest thread).
        self.video_upstream_url: str | None = None
        # Path/URL for <img src> on clients — backend relay, not the Pi.
        self.video_stream_url: str | None = None
        self._events: list[dict] = []
        self._event_id = 0
        self.log_event('info', 'Backend started')

    # -------------------------------------------------------------------------
    # Event log
    # -------------------------------------------------------------------------

    def log_event(self, level: str, message: str) -> None:
        """Append a timestamped event; EVENT_LOG_MAX=0 means unlimited."""
        if EVENT_LOG_MESSAGE_MAXLEN > 0 and len(message) > EVENT_LOG_MESSAGE_MAXLEN:
            message = message[:EVENT_LOG_MESSAGE_MAXLEN] + "…"
        self._event_id += 1
        self._events.append({
            'id':        self._event_id,
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'level':     level,
            'message':   message,
        })
        if EVENT_LOG_MAX > 0 and len(self._events) > EVENT_LOG_MAX:
            self._events = self._events[-EVENT_LOG_MAX:]

    def get_events(self) -> list[dict]:
        return list(self._events)

    # -------------------------------------------------------------------------
    # Emergency stop
    # -------------------------------------------------------------------------

    def _try_latch_estop(self) -> None:
        """Latch e-stop from LiDAR/grid unless the user just cleared it."""
        if time.time() < self._ignore_estop_latch_until:
            return
        self.estop_active = True

    def set_estop(self, active: bool) -> None:
        self.estop_active = active
        if active:
            self.log_event('warning', 'Emergency stop engaged')
            cmd = 'estop_on'
        else:
            self._ignore_estop_latch_until = time.time() + 2.5
            self.log_event('info', 'Emergency stop released — control resumed')
            cmd = 'estop_off'
        if self.connected:
            try:
                self.mqtt_client.publish(TOPIC, cmd)
                self.log_event('info', f'MQTT -> {TOPIC}: {cmd}')
            except Exception as e:
                self.log_event('error', f'E-stop MQTT publish failed: {e}')

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    def connect_to_broker(self, ip: str) -> tuple[bool, str]:
        """
        Connect to MQTT broker.

        Returns:
            tuple: (success: bool, message: str)
        """
        self.broker_ip = ip.strip()

        if not self.broker_ip:
            return False, "Broker IP cannot be empty"

        try:
            self.mqtt_client.connect(self.broker_ip, BROKER_PORT, MQTT_TIMEOUT)
            self.mqtt_client.subscribe(TOPIC)
            self.mqtt_client.subscribe(SAFETY_TOPIC)
            self.mqtt_client.subscribe(GRID_TOPIC)
            self.mqtt_client.subscribe(DRIVE_STATUS_TOPIC)
            self.mqtt_client.loop_start()
            self.connected = True
            msg = f"Connected to MQTT broker at {self.broker_ip}:{BROKER_PORT}"
            self.log_event('info', msg)
            return True, msg

        except Exception as e:
            self.connected = False
            msg = f"Connection failed: {str(e)}"
            self.log_event('error', msg)
            return False, msg

    # -------------------------------------------------------------------------
    # LiDAR safety status
    # -------------------------------------------------------------------------

    def _on_connect(self, client, _userdata, _flags, _rc) -> None:
        """Subscribe to command + safety topics whenever MQTT connects/reconnects."""
        client.subscribe(TOPIC)
        client.subscribe(SAFETY_TOPIC)
        client.subscribe(GRID_TOPIC)
        client.subscribe(DRIVE_STATUS_TOPIC)

    @staticmethod
    def _robot_timestamp_seconds(obj: dict) -> float | None:
        """Pi clock from mqtt_ros_node.py payloads (time.time() seconds)."""
        raw_ts = obj.get("timestamp")
        if raw_ts is None:
            return None
        try:
            value = float(raw_ts)
        except (TypeError, ValueError):
            return None
        if not (value > 0):
            return None
        # Accept ms payloads defensively.
        if value > 1e12:
            value /= 1000.0
        return value

    def _parse_safety_message(self, raw: str) -> dict:
        """Parse messages like 'blocked,distance=0.23m,estop=true'."""
        text = raw.strip()
        parts = [part.strip() for part in text.split(",") if part.strip()]
        status = parts[0] if parts else "unknown"
        fields = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()

        estop_text = fields.get("estop", "").lower()
        estop = (
            estop_text in {"true", "1", "yes", "on"}
            or (not estop_text and status in {"blocked", "estop_triggered"})
        )

        return {
            "raw": text,
            "status": status,
            "distance": fields.get("distance"),
            "estop": estop,
            "updatedAt": int(time.time() * 1000) if text else None,
        }

    def _parse_grid_message(self, raw: str) -> dict:
        """
        Parse GRID_TOPIC payloads (expected JSON) into the same shape as safety_status.
        Accepts flexible key names so different LiDAR nodes can interop.
        """
        text = raw.strip()
        updated_at = int(time.time() * 1000) if text else None
        try:
            obj = json.loads(text) if text else {}
        except Exception:
            obj = {}

        if isinstance(obj, dict):
            status = (
                obj.get("status")
                or obj.get("state")
                or obj.get("level")
                or "grid"
            )
            distance = (
                obj.get("distance")
                or obj.get("min_distance")
                or obj.get("closest_distance")
            )
            width = obj.get("width") or obj.get("w") or 120
            height = obj.get("height") or obj.get("h") or 120
            grid = (
                obj.get("grid")
                or obj.get("cells")
                or obj.get("data")
                or obj.get("occupancy")
            )
            estop_val = (
                obj.get("estop")
                if "estop" in obj
                else obj.get("e_stop")
                if "e_stop" in obj
                else obj.get("estop_active")
            )
            estop = bool(estop_val) if estop_val is not None else False

            def _to_int(x):
                try:
                    return int(x)
                except Exception:
                    try:
                        return int(float(x))
                    except Exception:
                        return 0

            flat: list[int] | None = None
            if isinstance(grid, list):
                if len(grid) > 0 and isinstance(grid[0], list):
                    flat = [_to_int(v) for row in grid for v in (row if isinstance(row, list) else [])]
                else:
                    flat = [_to_int(v) for v in grid]

            try:
                w = int(width)
            except Exception:
                w = 120
            try:
                h = int(height)
            except Exception:
                h = 120

            expected = w * h
            if flat is not None and expected > 0 and len(flat) >= expected:
                flat = flat[:expected]
                # Normalise values to -1/0/1 where possible.
                flat = [(-1 if v < 0 else 1 if v > 0 else 0) for v in flat]
            else:
                flat = None

            robot_ts = self._robot_timestamp_seconds(obj)
            auto_mode = obj.get("auto_mode") if "auto_mode" in obj else None

            return {
                "raw": text,
                "status": str(status),
                "distance": str(distance) if distance is not None else None,
                "estop": estop,
                "updatedAt": updated_at,
                "robotTimestamp": robot_ts,
                "auto_mode": bool(auto_mode) if auto_mode is not None else None,
                "w": w,
                "h": h,
                "grid": flat,
            }

        # Non-dict JSON (or parse failed): keep raw and mark as grid.
        return {
            "raw": text,
            "status": "grid",
            "distance": None,
            "estop": False,
            "updatedAt": updated_at,
            "robotTimestamp": None,
            "auto_mode": None,
        }

    def _parse_drive_message(self, raw: str) -> dict:
        """Parse yahboom/drive/status JSON from mqtt_ros_node.py."""
        text = raw.strip()
        updated_at = int(time.time() * 1000) if text else None
        try:
            obj = json.loads(text) if text else {}
        except Exception:
            obj = {}

        if not isinstance(obj, dict):
            obj = {}

        status = obj.get("status") or obj.get("state") or "unknown"
        robot_ts = self._robot_timestamp_seconds(obj)
        auto_mode = obj.get("auto_mode") if "auto_mode" in obj else None
        estop_val = obj.get("estop_active") if "estop_active" in obj else obj.get("estop")
        estop = bool(estop_val) if estop_val is not None else False

        return {
            "raw": text,
            "status": str(status),
            "robotTimestamp": robot_ts,
            "auto_mode": bool(auto_mode) if auto_mode is not None else None,
            "estop": estop,
            "updatedAt": updated_at,
        }

    def _on_message(self, _client, _userdata, message) -> None:
        raw = message.payload.decode(errors="replace")
        if message.topic == TOPIC:
            self.log_event("info", f"MQTT <- {TOPIC}: {raw}")
            if raw == "estop_on":
                self.estop_active = True
            elif raw == "estop_off":
                self.estop_active = False
            return

        if message.topic == GRID_TOPIC:
            status = self._parse_grid_message(raw)
            self.latest_grid_status = status
            # Don't log GRID_TOPIC payloads: they can be very large (120x120 grid)
            # and will flood the dashboard event log.
            # Grid carries the Pi's authoritative estop_active flag.
            if status.get("estop"):
                self._try_latch_estop()
            else:
                self.estop_active = False
            return

        if message.topic == SAFETY_TOPIC:
            status = self._parse_safety_message(raw)
            self.latest_safety_status = status
            if status["estop"]:
                # Latch e-stop on safety trips. Clear via /api/estop or Pi grid estop=false.
                self._try_latch_estop()
            return

        if message.topic == DRIVE_STATUS_TOPIC:
            self.latest_drive_status = self._parse_drive_message(raw)
            return

        return

    def get_safety_status(self) -> dict:
        # Prefer GRID_TOPIC output (LiDAR JSON grid) over SAFETY_TOPIC text.
        grid = self.latest_grid_status or {}
        if isinstance(grid, dict) and grid.get("updatedAt"):
            return dict(grid)
        return dict(self.latest_safety_status)

    def get_grid_status(self) -> dict:
        """Return latest parsed GRID_TOPIC JSON payload."""
        return dict(self.latest_grid_status)

    def get_safety_topic_status(self) -> dict:
        """Return latest parsed SAFETY_TOPIC text payload."""
        return dict(self.latest_safety_status)

    def get_drive_status(self) -> dict:
        """Return latest parsed drive-status JSON from the Pi."""
        return dict(self.latest_drive_status)


# Global instance
mqtt_service = MQTTService()
