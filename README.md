# Yahboom Project Overview

This README documents the main features, expected behaviour, and the responsible functions/files for both the Yahboom Car and the Yahboom Dashboard backend. MQTT topics used by the system are listed with producers and consumers. Detailed configuration for movement, camera servo control, and SLAM mapping is provided throughout this document.

---

## Table of Contents

1. [Quick Start](#quick-start---running-the-dashboard)
2. [Recent dashboard changes](#recent-dashboard-changes)
3. [Yahboom Car](#yahboom-car)
    - [Yahboom Car Core System Architecture](#yahboom-car-core-system-architecture)
    - [Yahboom Car Command Reference](#yahboom-car-command-reference)
    - [Yahboom Car Startup & Testing](#yahboom-car-startup--testing)
    - [How the System Works Together](#how-the-system-works-together)
4. [Yahboom Dashboard Backend](#yahboom-dashboard-backend)
5. [Movement & Motor Control](#movement--motor-control)
6. [Camera & Servo Control](#camera--servo-control)
7. [SLAM Mapping Settings](#slam-mapping-settings)
8. [Backend Configuration & SLAM Service Details](#backend-configuration--slam-service-details)
9. [MQTT Topics Reference](#mqtt-topics-reference)

---

## Recent dashboard changes

### Stop-Time Test Bench widget

A **control** widget measures robot stop time after explore mode starts. See [Yahboom Dashboard/README.md](Yahboom%20Dashboard/README.md#stop-time-test-bench) for full behaviour.

Summary:

- **START** sends `auto_on` (same as the EXPLORE button). Stop-mode toggle is disabled during an active session.
- **Stop modes:** *Cache aware stop* (Pi drive-status only) or *Edge aware stop* (VIT scene decoder sends `auto_soft_stop` on the `bottle` label after START, ≥ 40% confidence).
- Timestamps in **CSV export** come from the **Raspberry Pi clock** (`timestamp` / `robotTimestamp` on `yahboom/drive/status` and `yahboom/grid`), not the browser.
- **Command time** is stamped when START is pressed; **movement start** is when the Pi reports wheels in motion; **stop** is detected from Pi drive-status and e-stop (manual, grid, or edge-aware VIT).
- Live on-screen timer extrapolates from the last Pi sample between MQTT updates (display only).
- Multiple runs, per-run stopping distance, network type, and stop mode; **Export CSV** and **Clear** history.

**APIs:** `GET /api/drive_status`, `GET /api/grid_status`, `GET /api/test_bench/stop_mode`, `GET /api/vit/status` (edge-aware mode).

**Code:** `StopTestBenchWidget` in [Widgets.tsx](Yahboom%20Dashboard/src/app/components/Widgets.tsx); `edgeAwareStopLabelEstop.ts`; `useEdgeAwareStopLabelEstop()` in [hooks.ts](Yahboom%20Dashboard/src/app/hooks.ts); [test_bench_routes.py](Yahboom%20Dashboard/backend/app/routes/test_bench_routes.py).

### Drive status on the backend

- New MQTT subscription: `yahboom/drive/status` (env: `MQTT_DRIVE_STATUS_TOPIC`).
- New REST endpoint: **`GET /api/drive_status`** — used by the test bench, drive status polling, and System Status.
- Grid parser forwards **`robotTimestamp`** and **`auto_mode`** from Pi payloads.

Files: [Yahboom Dashboard/backend/config.py](Yahboom%20Dashboard/backend/config.py), [Yahboom Dashboard/backend/app/services/mqtt_service.py](Yahboom%20Dashboard/backend/app/services/mqtt_service.py), [Yahboom Dashboard/backend/app/routes/bot_routes.py](Yahboom%20Dashboard/backend/app/routes/bot_routes.py).

### Client-side auto removed

The dashboard **no longer** runs browser-based explore autopilot:

| Removed | Replacement |
|---------|-------------|
| **CLIENT AUTO** widget | Use **EXPLORE** (ROS Auto Button) → Pi `auto_on` |
| `useClientAutoPilot()` hook | Pi `auto_movement_logic()` on the car |
| `toggleClientExplore()` | `toggleRosAuto()` |
| `exploreActive` store field | `autoRunning` (set from `auto_on` / `auto_off` only) |

Manual joystick, keyboard, and Pi autonomous mode are unchanged.

---

## Yahboom Car

| Feature                         | Expected behaviour                                                                                                                                                        | Responsible functions / files                                                                                                                                                                                                                                                                           |
| :------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Movement commands               | Robot moves on manual client commands (fwd, bck, left, right, stop) and can be driven in auto mode. Movement is rate-limited and smoothed with configurable acceleration. | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): `on_message()` handling movement commands, `publish_cmd_vel()`, `ramp_value()`, `stop_robot_now()`                                                                                                                            |
| Auto navigation                 | In auto mode the node selects forward/turn behaviour based on LiDAR distances and executes safe turns or stops. Avoids obstacles dynamically.                             | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): `auto_movement_logic()`, `find_best_direction()`                                                                                                                                                                              |
| E-stop / Safety                 | Hard e-stop latches and blocks movement; LiDAR safety node triggers hard/soft stop depending on mode; drive status published to dashboard. Prevents collision.            | [Yahboom Car/Used/lidar_safety_node.py](Yahboom%20Car/Used/lidar_safety_node.py): `scan_callback()`, `trigger_manual_estop()`, `trigger_auto_soft_stop()`. Also [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): `enable_estop()`, `disable_estop()`, `publish_drive_status()` |
| Servo / Camera control          | Camera servo commands move pan/tilt servos with configurable limits and step sizes; reset and publish servo values over ROS topics.                                       | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): servo command handling in `on_message()`, `servo_tick()`, `publish_servos()`                                                                                                                                                  |
| Local occupancy grid publishing | Converts `/scan` LiDAR into a local occupancy grid and publishes it to MQTT for the dashboard and SLAM service at 5 Hz.                                                   | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): `build_grid()`, `publish_grid_mqtt()`                                                                                                                                                                                         |
| Drive / Safety status           | Periodically publish drive and safety state (front/left/right distances, auto mode, estop) to MQTT for dashboard real-time feedback.                                      | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py): `publish_drive_status()`; [Yahboom Car/Used/lidar_safety_node.py](Yahboom%20Car/Used/lidar_safety_node.py): `publish_status()` and `publish_mode_status()`                                                                    |
| VIT / Embedding encoder         | Capture camera frames, encode embeddings (MobileCLIP), publish embeddings and encoder status to MQTT for AI-assisted navigation.                                          | [Yahboom Car/Used/webrtc_server.py](Yahboom%20Car/Used/webrtc_server.py): VIT workers `camera_worker()`, `vit_worker()`, `get_embedding()`, plus WebRTC server                                                                                                                                          |
| WebRTC video + VIT control      | Serve a browser UI for live video and allow runtime commands (change embedding size) via MQTT topic. Low-latency video streaming.                                         | [Yahboom Car/Used/webrtc_server.py](Yahboom%20Car/Used/webrtc_server.py): `index()`, `offer()`, `CameraVideoTrack`, `create_mqtt_client()` and MQTT command handler                                                                                                                                     |

---

## Yahboom Car Core System Architecture

The Yahboom Car is controlled by four main Python scripts that work together to provide movement control, safety monitoring, video streaming, and AI-powered image embeddings:

1. **`mqtt_ros_node.py`** – Main robot control bridge between MQTT and ROS 2
2. **`lidar_safety_node.py`** – LiDAR-based obstacle detection and safety enforcement
3. **`webrtc_server.py`** – WebRTC video server with integrated embedding publishing

### `mqtt_ros_node.py` - MQTT to ROS 2 Bridge

**Purpose**: Handles all robot movement commands, camera servo control, LiDAR grid publishing, autonomous navigation, and emergency-stop state management.

**Key Responsibilities**:

- Receives movement commands from MQTT topic `yahboom/cmd`
- Publishes `/cmd_vel` (Twist messages) to ROS 2 for robot movement
- Controls servo motors via `/servo_s1` and `/servo_s2` topics
- Reads LiDAR data from `/scan` and builds occupancy grid
- Publishes grid and drive status to MQTT
- Handles manual, auto, and e-stop modes
- Smooths acceleration for natural movement

**Main MQTT Topics**:

- `yahboom/cmd` – Receives movement, servo, auto, and e-stop commands
- `yahboom/grid` – Publishes local occupancy grid (5 Hz)
- `yahboom/drive/status` – Publishes drive state and distances

**Key Functions**:

- `on_message()` – Command handler (movement, camera, auto, e-stop)
- `publish_cmd_vel()` – Publishes velocity commands to ROS
- `auto_movement_logic()` – Autonomous forward/turn decision logic
- `servo_tick()` – Updates servo positions every 100ms
- `build_grid()` / `publish_grid_mqtt()` – Occupancy grid creation and publishing

---

### `lidar_safety_node.py` - Safety Monitoring

**Purpose**: Continuously monitors LiDAR for obstacles and enforces safety by triggering hard e-stop in manual mode or soft stop in auto mode.

**Key Responsibilities**:

- Monitors front LiDAR cone for obstacles
- Triggers hard emergency stop in manual mode
- Triggers soft stop in auto mode (allows recovery)
- Enforces 30-second grace period after `estop_off` to prevent re-triggering
- Publishes safety status to MQTT

**Safety Parameters**:

- `WARNING_DISTANCE = 0.60m` – Advisory distance
- `BLOCK_DISTANCE = 0.35m` – Stop distance
- `FRONT_ANGLE = 20°` – Cone angle (±20° from center)
- `CONFIRM_POINTS = 8` – Require 8 LiDAR points for confirmation
- `ESTOP_REARM_DELAY = 30s` – Grace period after e-stop clear

**Main MQTT Topics**:

- `yahboom/cmd` – Subscribes to detect mode changes and e-stop commands
- `yahboom/safety/status` – Publishes safety state (clear, warning, blocked, etc.)

**Key Functions**:

- `scan_callback()` – Processes LiDAR data and detects obstacles
- `trigger_manual_estop()` – Publishes hard e-stop in manual mode
- `trigger_auto_soft_stop()` – Publishes soft stop in auto mode
- `publish_status()` – Periodic safety status updates

### `webrtc_server.py` - WebRTC Video + VIT Integration

**Purpose**: Provides low-latency WebRTC video streaming to browser and runs MobileCLIP embedding publishing in background threads.

**Key Responsibilities**:

- Opens camera via OpenCV
- Serves WebRTC video stream on port 8080
- Creates WebRTC peer connections for browser
- Runs camera worker thread for frame capture
- Runs VIT worker thread for embedding publishing
- Allows runtime embedding size changes via MQTT
- Handles browser connections and disconnections

**Web Interface**:

- Accessible at: `http://<robot-ip>:8080`
- Shows live WebRTC video, connection status, and restart button
- JavaScript auto-connects to WebRTC

**Main MQTT Topics**:

- `yahboom/vit/embedding` – Publishes embeddings from background VIT worker
- `yahboom/vit/status` – Publishes VIT status updates
- `yahboom/vit/command` – Receives embedding size commands (`embds1`, `embds2`, `embds3`)

**Embedding Size Commands**:

- `embds1` → 512 bytes, 128 dimensions
- `embds2` → 1024 bytes, 256 dimensions
- `embds3` → 2048 bytes, 512 dimensions

**Key Components**:

- `CameraVideoTrack` – Custom WebRTC video source
- `camera_worker()` – Background thread for frame capture
- `vit_worker()` – Background thread for embedding publishing
- `offer()` – WebRTC peer negotiation handler

---

## Yahboom Car Command Reference

### Movement Commands

| Command    | Action                  | Details                    |
| ---------- | ----------------------- | -------------------------- |
| `fwd`      | Move forward            | LINEAR_SPEED = 0.5 m/s     |
| `bck`      | Move backward           | LINEAR_SPEED = -0.5 m/s    |
| `left`     | Rotate left             | ANGULAR_SPEED = 1.0 rad/s  |
| `right`    | Rotate right            | ANGULAR_SPEED = -1.0 rad/s |
| `fwdleft`  | Forward-left diagonal   | 0.5 m/s + 0.5 rad/s        |
| `fwdright` | Forward-right diagonal  | 0.5 m/s - 0.5 rad/s        |
| `bckleft`  | Backward-left diagonal  | -0.5 m/s + 0.5 rad/s       |
| `bckright` | Backward-right diagonal | -0.5 m/s - 0.5 rad/s       |
| `stop`     | Stop all movement       | Zero velocity              |

### Camera Servo Commands

| Command      | Action              | Details                 |
| ------------ | ------------------- | ----------------------- |
| `cleft`      | Pan left            | S1 -5°, S2 0°           |
| `cright`     | Pan right           | S1 +5°, S2 0°           |
| `up`         | Tilt up             | S1 0°, S2 +5°           |
| `down`       | Tilt down           | S1 0°, S2 -5°           |
| `upcleft`    | Up + left           | S1 -5°, S2 +5°          |
| `upcright`   | Up + right          | S1 +5°, S2 +5°          |
| `downcleft`  | Down + left         | S1 -5°, S2 -5°          |
| `downcright` | Down + right        | S1 +5°, S2 -5°          |
| `crst`       | Reset to center     | S1→0°, S2→-60°          |
| `cstop`      | Stop servo movement | Freeze current position |

### Safety & Mode Commands

| Command          | Action                  | Effect                                                         |
| ---------------- | ----------------------- | -------------------------------------------------------------- |
| `auto_on`        | Enable autonomous mode  | Robot uses LiDAR for obstacle avoidance                        |
| `auto_off`       | Disable autonomous mode | Returns to manual control                                      |
| `estop_on`       | Activate emergency stop | Hard lock; blocks all movement commands                        |
| `estop_off`      | Clear emergency stop    | Unlocks movement; 30s grace period before LiDAR can re-trigger |
| `auto_soft_stop` | Soft stop in auto mode  | Halts robot without locking e-stop; allows recovery            |

### VIT Embedding Commands

| Command  | Size   | Dimensions | Use Case                     |
| -------- | ------ | ---------- | ---------------------------- |
| `embds1` | 512 B  | 128        | Low latency, fast processing |
| `embds2` | 1024 B | 256        | Balanced speed/quality       |
| `embds3` | 2048 B | 512        | High quality, slower         |

---

## Yahboom Car Startup & Testing

### Basic Run Order

```bash
# Terminal 1: Robot movement and ROS bridge
python3 mqtt_ros_node.py

# Terminal 2: Safety monitoring
python3 lidar_safety_node.py

# Terminal 3: WebRTC video + VIT embeddings (recommended)
python3 webrtc_server.py

```

### Testing with MQTT

```bash
# Move forward
mosquitto_pub -h localhost -t yahboom/cmd -m "fwd"

# Stop
mosquitto_pub -h localhost -t yahboom/cmd -m "stop"

# Rotate left
mosquitto_pub -h localhost -t yahboom/cmd -m "left"

# Enable auto mode
mosquitto_pub -h localhost -t yahboom/cmd -m "auto_on"

# Disable auto mode
mosquitto_pub -h localhost -t yahboom/cmd -m "auto_off"

# Activate e-stop
mosquitto_pub -h localhost -t yahboom/cmd -m "estop_on"

# Clear e-stop
mosquitto_pub -h localhost -t yahboom/cmd -m "estop_off"

# Move camera left
mosquitto_pub -h localhost -t yahboom/cmd -m "cleft"

# Reset camera
mosquitto_pub -h localhost -t yahboom/cmd -m "crst"

# Change embedding size to 512B/128D
mosquitto_pub -h localhost -t yahboom/vit/command -m "embds1"
```

### Accessing WebRTC Video

Open browser and navigate to:

```
http://<robot-ip>:8080
```

The page displays live camera feed and connection status. The system auto-connects to WebRTC.

---

## How the System Works Together

### Manual Control Flow

1. Dashboard sends command → `yahboom/cmd`
2. `mqtt_ros_node.py` receives command
3. Converts to ROS 2 `/cmd_vel` message
4. Robot moves via ROS control
5. `lidar_safety_node.py` monitors front LiDAR
6. If too close: publishes `estop_on` → movement blocked
7. Dashboard receives safety status via `yahboom/safety/status`

### Autonomous Mode Flow

1. Dashboard sends `auto_on` → `yahboom/cmd`
2. Both `mqtt_ros_node.py` and `lidar_safety_node.py` switch to auto mode
3. Robot moves forward if path is clear (LiDAR check)
4. If obstacle detected: `lidar_safety_node.py` publishes `auto_soft_stop`
5. `mqtt_ros_node.py` stops robot but keeps e-stop unlocked
6. Pi auto logic selects the next move (turn, reverse, or halt)

### Video & Embedding Flow

1. `webrtc_server.py` opens camera
2. `camera_worker()` thread reads frames continuously
3. `vit_worker()` thread processes frames every 5 frames
4. MobileCLIP-S1 creates embeddings
5. Embeddings published → `yahboom/vit/embedding`
6. Status updates → `yahboom/vit/status`
7. Browser connects to port 8080 for live WebRTC video
8. Embedding size can be changed via `yahboom/vit/command`

---

## Yahboom Dashboard Backend

| Feature                          | Expected behaviour                                                                                                                                            | Responsible functions / files                                                                                                                                                                                                                                 |
| :------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| MQTT drive status relay          | Subscribe to `yahboom/drive/status`, parse Pi `timestamp` as `robotTimestamp`, expose via REST for widgets and stop-time experiments.                         | [backend/app/services/mqtt_service.py](Yahboom%20Dashboard/backend/app/services/mqtt_service.py): `_parse_drive_message()`, `get_drive_status()`; [backend/app/routes/bot_routes.py](Yahboom%20Dashboard/backend/app/routes/bot_routes.py): `GET /api/drive_status` |
| Stop-Time Test Bench widget      | Pi-clock stop timing, cache vs edge-aware stop modes, VIT stop-label e-stop (armed after START), CSV export. Uses drive/grid/VIT APIs and `toggleRosAuto()`. | [src/app/components/Widgets.tsx](Yahboom%20Dashboard/src/app/components/Widgets.tsx): `StopTestBenchWidget`; [src/lib/edgeAwareStopLabelEstop.ts](Yahboom%20Dashboard/src/lib/edgeAwareStopLabelEstop.ts); [backend/app/routes/test_bench_routes.py](Yahboom%20Dashboard/backend/app/routes/test_bench_routes.py) |
| Backend server startup           | Create and start Flask app, load config, manage background services (SLAM, VIT relay). Provides REST API endpoints.                                           | [backend/main.py](Yahboom%20Dashboard/backend/main.py): `create_app()` and top-level run block — see [client_backend_README.md](client_backend_README.md)                                                                                                     |
| Configuration                    | Centralised settings for MQTT topics, Flask host/port, VIT, SLAM and Raspberry Pi SSH/video settings. Configured via `.env` file.                             | [backend/config.py](Yahboom%20Dashboard/backend/config.py) — referenced in [client_backend_README.md](client_backend_README.md)                                                                                                                               |
| SLAM mapping service             | Subscribe to scan/grid/cmd topics, feed data to SLAM core, write `slam_map.json`, provide API endpoints returning map/status. Real-time 2D occupancy mapping. | [backend/slam_service.py](Yahboom%20Dashboard/backend/slam_service.py): `SlamService` methods `connect()`, `_on_message()`, `_writer_loop()`, `get_map()`, `get_status()`; CartographerSLAM class methods like `process_raw_scan()`, `_update()`, `to_dict()` |
| API routes                       | Expose map and SLAM status to frontend, handle VIT streaming endpoints and other dashboard routes. REST API for frontend.                                     | [backend/app/routes/stream_routes.py](Yahboom%20Dashboard/backend/app/routes/stream_routes.py) (and other routes in `routes/`)                                                                                                                                |
| VIT service & embedding handling | Receive VIT embeddings, store/parse them for search/labels, expose embeddings endpoints for AI scene understanding.                                           | [backend/app/services/vit/vit_service.py](Yahboom%20Dashboard/backend/app/services/vit/vit_service.py) and [backend/app/services/vit/vit_server_ssh.py](Yahboom%20Dashboard/backend/app/services/vit/vit_server_ssh.py)                                       |

---

## Movement & Motor Control

### Manual Movement Commands

The robot responds to the following manual movement commands published to `yahboom/cmd`:

| Command    | Action                  | Linear Speed | Angular Speed |
| ---------- | ----------------------- | ------------ | ------------- |
| `fwd`      | Move forward            | 0.5 m/s      | 0 rad/s       |
| `bck`      | Move backward           | -0.5 m/s     | 0 rad/s       |
| `left`     | Rotate left in place    | 0 m/s        | 1.0 rad/s     |
| `right`    | Rotate right in place   | 0 m/s        | -1.0 rad/s    |
| `fwdleft`  | Forward-left diagonal   | 0.5 m/s      | 0.5 rad/s     |
| `fwdright` | Forward-right diagonal  | 0.5 m/s      | -0.5 rad/s    |
| `bckleft`  | Backward-left diagonal  | -0.5 m/s     | 0.5 rad/s     |
| `bckright` | Backward-right diagonal | -0.5 m/s     | -0.5 rad/s    |
| `stop`     | Stop all movement       | 0 m/s        | 0 rad/s       |

### Movement Settings (Manual Mode)

These settings are configured in [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py) around lines 32-38:

- **`LINEAR_SPEED = 0.5`**: Maximum linear velocity (m/s) for manual forward/backward movement
- **`ANGULAR_SPEED = 1.0`**: Maximum angular velocity (rad/s) for manual rotations
- **`PUBLISH_RATE = 20.0`**: Frequency of `/cmd_vel` messages published (Hz) – 50ms per command
- **`LINEAR_STEP = 0.02`**: Linear velocity increment per control cycle (enables smooth acceleration)
- **`ANGULAR_STEP = 0.05`**: Angular velocity increment per control cycle (enables smooth rotation acceleration)

### Pi autonomous mode (dashboard)

The dashboard enables Pi autonomous navigation by publishing MQTT commands (EXPLORE button or `auto_on`):

| Command          | Action                           | Effect                                                                                                  |
| ---------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `auto_on`        | Enable autonomous mode           | Pi runs `auto_movement_logic()`; manual movement commands are blocked while `autoMode` is active       |
| `auto_off`       | Disable autonomous mode          | Robot stops; returns to manual control mode                                                             |
| `auto_soft_stop` | Soft emergency stop in auto mode | Halts robot immediately but does NOT latch e-stop; Pi or operator can resume                            |

**Note:** Client-side explore autopilot (browser LiDAR decision loop) was removed. All autonomous driving runs on the Pi via `mqtt_ros_node.py`.

**Important**: Camera commands (`up`, `down`, `cleft`, `cright`, etc.) work in both manual and auto modes and are NOT blocked by e-stop.

### Yahboom Onboard Auto Navigation

When the robot receives an `auto_on` command, it enters autonomous navigation mode:

- **Behavior**: Robot attempts to move forward while avoiding obstacles detected by LiDAR
- **Turn Logic**: If an obstacle is detected in front, the robot selects the best direction (left or right) based on available space
- **Safety**: Robot will trigger a soft stop if obstacles are too close (not blocked by hard e-stop)
- **Implementation**: Controlled by `auto_movement_logic()` and `find_best_direction()` in [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py)

### Yahboom Auto Navigation Settings

These settings control the onboard autonomous behavior in [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py) lines 41-52:

- **`AUTO_LINEAR_SPEED = 0.25`**: Forward velocity in auto mode (m/s) – slower than manual for safety
- **`AUTO_TURN_SPEED = 0.65`**: Rotation speed when turning to avoid obstacles (rad/s)
- **`AUTO_BLOCK_DISTANCE = 0.40`**: Minimum safe distance (m) before triggering a stop. Must be larger than lidar_safety_node estop distance
- **`AUTO_SIDE_DISTANCE = 0.30`**: Minimum safe side distance (m) for left/right obstacle detection
- **`AUTO_90_TURN_TIME = 2.4`**: Approximate duration (s) for a 90-degree turn (tune after testing)
- **`BEST_DIRECTION_WINDOW = 10.0`**: Scan window (degrees) for finding best turning direction
- **`ROBOT_WIDTH_M = 0.17`**: Robot physical width (m)
- **`GAP_SAFETY_MARGIN = 1.3`**: Safety multiplier for minimum gap width
- **`MIN_GAP_WIDTH_M`**: Effective minimum gap width (calculated as `ROBOT_WIDTH_M * GAP_SAFETY_MARGIN` ≈ 0.22 m)

### Movement Publishing Rate

- **`/cmd_vel` topic**: Published at 20 Hz (50 ms intervals) to ROS
- **Drive status**: Published to `yahboom/drive/status` MQTT topic with current distances and state
- **Grid updates**: Occupancy grid published to `yahboom/grid` at 5 Hz (see SLAM Mapping Settings)

---

## Camera & Servo Control

### Servo Configuration

The robot has two servos controlling camera pan and tilt:

- **Servo S1 (Pan)**: Left/right camera rotation
    - Range: **-90° to +90°** (0° center)
    - Maximum step: 10°
- **Servo S2 (Tilt)**: Up/down camera rotation
    - Range: **-90° to +20°** (default -60°)
    - Maximum step: 5°

These limits are configured in [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py) around lines 66-70:

```python
S1_MIN, S1_MAX = -90, 90      # Pan servo limits (degrees)
S2_MIN, S2_MAX = -90, 20      # Tilt servo limits (degrees)
CAM_STEP = 10                 # Standard camera step size (degrees)
S_CAM_STEP = 5                # Servo step size (degrees) - smaller for finer control
```

### Camera Control Commands

Camera commands are published to `yahboom/cmd` and are **NOT blocked by e-stop**:

| Command      | Action                | S1 Change  | S2 Change    |
| ------------ | --------------------- | ---------- | ------------ |
| `up`         | Tilt up               | 0°         | +5°          |
| `down`       | Tilt down             | 0°         | -5°          |
| `cleft`      | Pan left              | -5°        | 0°           |
| `cright`     | Pan right             | +5°        | 0°           |
| `upcleft`    | Tilt up + pan left    | -5°        | +5°          |
| `upcright`   | Tilt up + pan right   | +5°        | +5°          |
| `downcleft`  | Tilt down + pan left  | -5°        | -5°          |
| `downcright` | Tilt down + pan right | +5°        | -5°          |
| `crst`       | Reset to center       | -S1° to 0° | -S2° to -60° |
| `cstop`      | Stop servo movement   | 0°         | 0°           |

### Servo Control Implementation

The servo control logic is handled in [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py):

- **Update frequency**: `servo_tick()` called every 100 ms
- **Command handling**: `on_message()` captures camera commands and updates target servo angles
- **Publishing**: Servo positions published to ROS topics `/servo_s1` and `/servo_s2` as `Int32` messages
- **Ramping**: Servos move incrementally to avoid sudden jerks (smooth acceleration)

### Camera Feed

The camera is served via **WebRTC** (low-latency) and **MJPEG** (compatibility fallback):

- **WebRTC Server**: Runs on port 8080 (configurable via `VIDEO_SERVER_PORT`)
- **Video feed path**: `/video_feed` (default)
- **MJPEG relay**: Optional legacy MJPEG relay at `/api/video_feed`
- **Frame capture**: Handled by `webrtc_server.py`

---

## SLAM Mapping Settings

### SLAM Overview

The SLAM service implements a **Google Cartographer-inspired correlative scan matcher** for 2D occupancy-grid mapping:

1. **Correlative Scan Matcher (CSM)** – FFT cross-correlation to find best (dx, dy, dθ) displacement
2. **Gaussian-blurred probability map** – Creates gravitational wells around obstacles
3. **Log-odds Bresenham ray casting** – Marks free space and occupied cells
4. **Iterative Closest Point (ICP)** – LiDAR-only motion fallback for autonomous movement
5. **Turn calibration** – Online learning of actual angular velocity during rotations

### Map Dimensions & Resolution

Configured in [backend/config.py](Yahboom%20Dashboard/backend/config.py) and [backend/slam_service.py](Yahboom%20Dashboard/backend/slam_service.py):

- **`SLAM_MAP_SIZE_M = 20.0`**: Total map extent in metres (20m × 20m default)
- **`SLAM_RESOLUTION_M = 0.05`**: Grid resolution (0.05m = 5cm per cell)
- **Total grid cells**: 400 × 400 = 160,000 cells
- **Output file**: `slam_map.json` (JSON format, updated in real-time)

### LiDAR Range Settings

- **`SLAM_MAX_RANGE_M = 8.0`**: Maximum LiDAR range (m) – rays beyond this are ignored
- **`SLAM_MIN_RANGE_M = 0.05`**: Minimum LiDAR range (m) – rays closer than this are ignored (avoid reflections)
- **`SLAM_MAX_SCAN_PTS = 720`**: Maximum scan points processed per update (downsampled for performance)

### Correlative Scan Matcher (CSM) Settings

These control how well the scan matcher aligns new scans with the map:

- **`SLAM_SEARCH_RADIUS_M = 0.70`**: Search radius (m) around predicted pose
- **`SLAM_SEARCH_ANGLE = 0.50`**: Search angle (rad ≈ 29°) for rotation hypothesis
- **`SLAM_BLUR_SIGMA_M = 0.10`**: Gaussian blur sigma (m) on probability map – creates "sticky" regions
- **`SLAM_CSM_MAX_CORRECTION_M = 0.40`**: Maximum correction applied per scan (guards against large jumps)
- **`SLAM_CSM_REFINE_MAX_M = 0.25`**: Maximum refinement during fine scan matching
- **`SLAM_CSM_HIGH_CONF = 0.35`**: High-confidence threshold for scan match

### Log-Odds Settings

These control how obstacles and free space are accumulated in the map:

- **`SLAM_LOG_OCC = 0.60`**: Log-odds increase per occupied ray endpoint
- **`SLAM_LOG_FREE = 0.30`**: Log-odds decrease per free-space ray segment
- **`SLAM_LOG_MAX = 5.00`**: Maximum log-odds value (saturation point for certainty)
- **`SLAM_OCC_LOCK = 1.25`**: Threshold (log-odds) for marking a cell as locked/occupied

### Minimum Confidence Threshold

- **`SLAM_MIN_CONFIDENCE = 0.05`**: Minimum confidence to accept a scan match (5%)

### Motion Model & Dead-Reckoning

These parameters estimate robot motion between scans:

- **`SLAM_LINEAR_MPS = 0.25`**: Linear velocity estimate (m/s) for dead-reckoning
- **`SLAM_ANGULAR_RPS = 1.1`**: Angular velocity estimate (rad/s) for turn integration

### Iterative Closest Point (ICP) Settings

ICP provides LiDAR-only motion estimation (fallback when wheel odometry is unavailable):

- **`SLAM_ICP_ENABLED = true`**: Enable ICP-based motion estimation
- **`SLAM_ICP_MAX_PTS = 240`**: Maximum points used in ICP (downsampled)
- **`SLAM_ICP_ITERS = 8`**: ICP iterations per scan
- **`SLAM_ICP_MAX_PAIR_M = 0.35`**: Maximum distance (m) for point correspondence
- **`SLAM_ICP_MIN_PAIRS = 20`**: Minimum point pairs required for valid match
- **`SLAM_ICP_MIN_CONFIDENCE = 0.25`**: Minimum confidence when motion commands are active
- **`SLAM_ICP_MIN_CONF_NO_CMD = 0.12`**: Minimum confidence when robot is idle (autonomous mode fallback)
- **`SLAM_ICP_MAX_STEP_M = 0.80`**: Maximum linear translation per scan (0.80 m)
- **`SLAM_ICP_MAX_ROT_RAD = 0.70`**: Maximum rotation per scan (≈ 40°)

### Turn Calibration

Online learning of actual angular velocity during turns:

- **`SLAM_TURN_CALIB_ENABLED = true`**: Enable turn calibration
- **`SLAM_TURN_BEARING_BINS = 360`**: Bearing histogram bins for polar correlation
- **`SLAM_TURN_MAX_SEARCH_RAD = 1.20`**: Search radius for turn matching (rad)
- **`SLAM_TURN_RANGE_TOL_M = 0.30`**: Range tolerance (m) for bearing match
- **`SLAM_TURN_MIN_BINS = 25`**: Minimum histogram bins for valid turn calibration
- **`SLAM_TURN_MIN_CONFIDENCE = 0.22`**: Minimum confidence for turn correction
- **`SLAM_TURN_MIN_DELTA_RAD = 0.015`**: Minimum angle change to update calibration
- **`SLAM_TURN_HINT_MAX_ERR_RAD = 0.55`**: Maximum error (rad) allowed for turn hint
- **`SLAM_TURN_CALIB_ALPHA = 0.12`**: Learning rate for angular velocity calibration
- **`SLAM_TURN_SEARCH_ANGLE = 0.90`**: Search angle (rad) for turn localization
- **`SLAM_TURN_ICP_MAX_ROT_RAD = 1.20`**: Maximum rotation allowed during turn ICP (≈ 69°)

### Map Auto-Resizing

The map automatically expands as the robot explores:

- **`SLAM_AUTO_RESIZE = true`**: Enable dynamic map expansion
- **`SLAM_RESIZE_MARGIN_M = 1.5`**: Headroom (m) to keep before triggering resize
- **`SLAM_RESIZE_STEP_M = 5.0`**: Grow map by 5m chunks when space runs out

### Output & Persistence

- **`SLAM_OUTPUT_FILE`**: Path to `slam_map.json` (serialized map state)
- **`SLAM_WRITE_INTERVAL_S = 0.50`**: Interval (s) for writing map to disk
- **`SLAM_MAX_TRAJECTORY = 2000`**: Maximum trajectory waypoints stored in memory

### View Padding & Display

- **`SLAM_VIEW_PADDING_M = 1.5`**: Padding (m) around occupied cells for frontend viewport
- **`SLAM_VIEW_MIN_SIZE_M = 5.0`**: Minimum viewport size (m) to prevent over-zoom

### SLAM Map JSON Format

The `slam_map.json` file contains:

```json
{
  "resolution": 0.05,
  "origin_x": -10.0,
  "origin_y": -10.0,
  "width": 400,
  "height": 400,
  "occupancy": [0, 0, 1, 1, 0, ...],
  "pose": [x, y, theta],
  "confidence": 0.92,
  "timestamp": "2025-01-15T10:30:45.123Z",
  "trajectory": [
    {"x": 0, "y": 0, "theta": 0, "t": 0},
    {"x": 0.1, "y": 0, "theta": 0, "t": 0.05},
    ...
  ],
  "status": "active"
}
```

### Tuning SLAM for Your Environment

1. **Narrow corridors**: Increase `SLAM_SEARCH_RADIUS_M` and `SLAM_SEARCH_ANGLE` for more robust matching
2. **Large open spaces**: Decrease `SLAM_LOG_FREE` to reduce spurious free space
3. **Dynamic environments**: Lower `SLAM_MIN_CONFIDENCE` to accept lower-quality matches
4. **Fast movement**: Increase `SLAM_ICP_MAX_STEP_M` and reduce `SLAM_ICP_ITERS`
5. **Slow, precise mapping**: Decrease `SLAM_ICP_MAX_STEP_M` and increase `SLAM_ICP_ITERS`

---

## Backend Configuration & SLAM Service Details

This section documents the backend Flask server, configuration system, and SLAM mapping service implementation.

### Backend Files

The backend consists of three main files:

- **`main.py`** – Entry point for the Flask backend server
- **`config.py`** – Centralized configuration settings
- **`slam_service.py`** – SLAM mapping engine and MQTT integration

---

## Backend Startup (`main.py`)

`main.py` is the main entry point for the backend server and handles environment validation before starting Flask.

### Functions in `main.py`

#### `_venv_python()`

Checks whether a Python virtual environment exists inside the backend folder.

**Looks for:**

On Windows:

```
.venv/Scripts/python.exe
```

On Linux or Raspberry Pi:

```
.venv/bin/python
```

**Returns** the venv Python path if it exists, otherwise `None`.

**Why**: Helps detect if the user is accidentally running the project with the wrong Python version.

---

#### `_ensure_dependencies()`

Checks whether Flask is installed. If Flask is missing:

1. Checks if user is already inside a virtual environment
2. Checks if `.venv` Python exists
3. Checks if `requirements.txt` exists
4. Automatically installs missing packages using `python -m pip install -r requirements.txt`

If dependencies are installed in `.venv` but user is running system Python, it prints:

```bash
backend/.venv/bin/python main.py
```

or:

```bash
npm run dev:backend
```

**Why**: Makes the backend easier to run and prevents environment errors.

---

#### Main program block

```python
if __name__ == "__main__":
    app = create_app()
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
```

1. Calls `create_app()` to create the Flask application
2. Runs the app using settings from `config.py`
3. Uses `threaded=True` for concurrent request handling

---

## Backend Configuration (`config.py`)

`config.py` stores all backend configuration settings loaded from `.env` file with sensible defaults.

### MQTT Settings

#### `DEFAULT_BROKER_IP`

Default MQTT broker address (usually Raspberry Pi hostname)

**Default**: `raspberrypi.local`

**Env variable**: `MQTT_BROKER_IP`

---

#### `BROKER_PORT`

MQTT broker port

**Default**: `1883`

**Env variable**: `MQTT_BROKER_PORT`

---

#### `TOPIC`

Main robot command topic for movement, camera, auto, and e-stop commands

**Default**: `yahboom/cmd`

**Env variable**: `MQTT_TOPIC`

---

#### `SAFETY_TOPIC`

LiDAR safety status updates from robot

**Default**: `yahboom/safety/status`

**Env variable**: `MQTT_SAFETY_TOPIC`

---

#### `GRID_TOPIC`

Local occupancy grid from robot

**Default**: `yahboom/grid`

**Env variable**: `MQTT_GRID_TOPIC`

---

#### `DRIVE_STATUS_TOPIC`

Drive state and Pi timestamps from `publish_drive_status()` on the robot

**Default**: `yahboom/drive/status`

**Env variable**: `MQTT_DRIVE_STATUS_TOPIC`

**API**: `GET /api/drive_status` returns parsed JSON including `robotTimestamp` (Pi `time.time()` seconds)

---

#### `MQTT_TIMEOUT`

Timeout for MQTT operations (seconds)

**Default**: `60`

**Env variable**: `MQTT_TIMEOUT`

---

#### `PUBLISH_TIMEOUT`

Timeout for MQTT publish (0 = fire-and-forget, lowest latency)

**Default**: `0`

**Env variable**: `MQTT_PUBLISH_TIMEOUT`

---

### Flask Settings

#### `FLASK_HOST`

Flask server host (0.0.0.0 allows access from any network device)

**Default**: `0.0.0.0`

**Env variable**: `FLASK_HOST`

---

#### `FLASK_PORT`

Flask server port

**Default**: `3000`

**Env variable**: `FLASK_PORT`

---

#### `FLASK_DEBUG`

Enable Flask debug mode

**Default**: `false`

**Env variable**: `FLASK_DEBUG`

---

### Raspberry Pi SSH Settings

Used for remote script control on Raspberry Pi.

#### `PI_SSH_USER`

SSH username for Pi

**Default**: `pi`

**Env variable**: `PI_SSH_USER`

---

#### `PI_SSH_PASSWORD`

SSH password for Pi

**Default**: `raspberry`

**Env variable**: `PI_SSH_PASSWORD`

---

#### `PI_SSH_KEY_PATH`

Optional SSH private key path (overrides password login)

**Default**: `` (empty – use password)

**Env variable**: `PI_SSH_KEY_PATH`

---

### Video Server Settings

Control WebRTC video server on Raspberry Pi.

#### `PI_VIDEO_VENV`

Virtual environment path for video server

**Default**: `~/vit_env/bin/activate`

**Env variable**: `PI_VIDEO_VENV`

---

#### `PI_VIDEO_SERVER_PATH`

Path to WebRTC server script on Pi

**Default**: `webrtc_server.py`

**Env variable**: `PI_VIDEO_SERVER_PATH`

---

#### `PI_VIDEO_SERVER_LOG`

Log file for video server output

**Default**: `/tmp/yahboom_video_server.log`

**Env variable**: `PI_VIDEO_SERVER_LOG`

---

#### `VIDEO_SERVER_PORT`

Video server port

**Default**: `8080`

**Env variable**: `VIDEO_SERVER_PORT`

---

#### `VIDEO_USE_MJPEG_RELAY`

Enable legacy MJPEG relay mode (false = use WebRTC)

**Default**: `false`

**Env variable**: `VIDEO_USE_MJPEG_RELAY`

---

#### `VIDEO_LINK_WAIT_SEC`

Time to wait for WebRTC dashboard link

**Default**: `15`

**Env variable**: `VIDEO_LINK_WAIT_SEC`

---

#### `PROBE_CACHE_TTL_SEC`

Cache TTL for video probe status

**Default**: `4`

**Env variable**: `PROBE_CACHE_TTL_SEC`

---

#### `VIDEO_PROBE_INTERVAL_SEC`

Interval for video server health checks

**Default**: `12`

**Env variable**: `VIDEO_PROBE_INTERVAL_SEC`

---

### VIT Settings

Configure MobileCLIP/VIT encoder on Raspberry Pi.

#### `PI_VIT_VENV`

Virtual environment for VIT server

**Default**: `~/vit_env/bin/activate`

**Env variable**: `PI_VIT_VENV`

---

#### `PI_VIT_SERVER_PATH`

Path to VIT server script on Pi

**Default**: `webrtc_server.py`

**Env variable**: `PI_VIT_SERVER_PATH`

---

#### `PI_VIT_SERVER_LOG`

Log file for VIT server

**Default**: `/tmp/yahboom_vit_server.log`

**Env variable**: `PI_VIT_SERVER_LOG`

---

#### `VIT_PROBE_INTERVAL_SEC`

Interval for VIT encoder health checks

**Default**: `2`

**Env variable**: `VIT_PROBE_INTERVAL_SEC`

---

#### `VIT_PROBE_CACHE_TTL_SEC`

Cache TTL for VIT probe status

**Default**: `2`

**Env variable**: `VIT_PROBE_CACHE_TTL_SEC`

---

#### `VIT_EMBEDDING_TOPIC`

Topic for receiving VIT embeddings

**Default**: `yahboom/vit/embedding`

**Env variable**: `MQTT_VIT_EMBEDDING_TOPIC`

---

#### `VIT_CLIP_EMBEDDING_TOPIC`

Topic for CLIP-style embeddings

**Default**: `yahboom/clip_embedding`

**Env variable**: `MQTT_VIT_CLIP_EMBEDDING_TOPIC`

---

#### `VIT_STATUS_TOPIC`

Topic for VIT status updates

**Default**: `yahboom/vit/status`

**Env variable**: `MQTT_VIT_STATUS_TOPIC`

---

#### `VIT_ROBOT_STATUS_TOPIC`

Topic for robot status updates

**Default**: `yahboom/status`

**Env variable**: `MQTT_VIT_ROBOT_STATUS_TOPIC`

---

#### `VIT_RESULT_TOPIC`

Topic for VIT detection results

**Default**: `yahboom/vit/result`

**Env variable**: `MQTT_VIT_RESULT_TOPIC`

---

#### `VIT_CONFIG_TOPIC`

Topic for VIT configuration

**Default**: `yahboom/vit/config`

**Env variable**: `MQTT_VIT_CONFIG_TOPIC`

---

#### `VIT_COMMAND_TOPIC`

Topic for sending VIT commands

**Default**: `yahboom/vit/command`

**Env variable**: `MQTT_VIT_COMMAND_TOPIC`

---

#### `VIT_CONFIDENCE_THRESHOLD`

Confidence threshold for VIT detections (0-100)

**Default**: `60.0`

**Env variable**: `VIT_CONFIDENCE_THRESHOLD`

---

### Command Groups

#### `CAMERA_COMMANDS`

Commands exempt from e-stop movement lock:

```
up, down, cright, cleft, upcright, upcleft, downcright, downcleft, crst, cstop
```

---

#### `AUTO_COMMANDS`

Autonomous mode control:

```
auto_on, auto_off
```

---

#### `ESTOP_COMMANDS`

Emergency stop control:

```
estop_on, estop_off
```

---

#### `ALLOWED_COMMANDS`

Full list of allowed robot commands (prevents unsafe/unknown commands)

---

## SLAM Mapping Service (`slam_service.py`)

`slam_service.py` implements the core SLAM (Simultaneous Localisation and Mapping) algorithm with Google Cartographer-inspired techniques.

### Main Classes

#### `CartographerSLAM`

Core SLAM engine implementing:

- Correlative scan matching via FFT cross-correlation
- Gaussian-blurred probability maps for obstacle detection
- Log-odds Bresenham ray casting for occupancy grid updates
- Iterative Closest Point (ICP) for LiDAR-only motion estimation
- Online turn calibration for angular velocity learning

**Important variables**:

- `self.pose` – Robot pose [x, y, theta] in metres/radians
- `self._log` – Occupancy grid in log-odds format (positive = occupied, negative = free, zero = unknown)
- `self._prob_blur` – Blurred probability map for scan matching
- `self.trajectory` – List of historical poses for path visualization

**Key methods**:

- `process_raw_scan(msg)` – Processes LiDAR scan JSON
- `process_grid_scan(msg)` – Processes occupancy grid JSON
- `_update(local_pts)` – Main SLAM update loop
- `apply_command(cmd)` – Receives movement command for dead-reckoning
- `_integrate_motion(now)` – Dead-reckoning motion model
- `_estimate_scan_motion(local_pts)` – ICP-based motion estimation
- `_scan_match(local_pts)` – Correlative scan matcher
- `_ray_cast_update(world_pts)` – Updates occupancy grid via ray casting
- `reset()` – Clears SLAM state
- `to_dict(status, uptime_s, crop)` – Exports map as JSON

---

#### `SlamService`

Wrapper that connects CartographerSLAM to MQTT and Flask.

**Responsibilities**:

- Creates and manages CartographerSLAM instance
- MQTT broker connection and subscription
- Background threads for map writing and monitoring
- API endpoints for map/status retrieval
- Map reset functionality

**Key methods**:

- `start_background()` – Starts writer and monitor threads
- `connect(broker_ip)` – Connects to MQTT broker
- `_on_message(client, userdata, message)` – MQTT message handler
- `_writer_loop()` – Writes slam_map.json at configured interval
- `_monitor_loop()` – Watches main MQTT service for broker changes
- `get_map(crop=False)` – Returns current map
- `get_status()` – Returns SLAM service status
- `request_reset()` – Requests map reset

---

### SLAM Output Format

The `slam_map.json` file contains:

```json
{
  "resolution": 0.05,
  "origin_x": -10.0,
  "origin_y": -10.0,
  "width": 400,
  "height": 400,
  "occupancy": [0, 0, 1, 1, 0, ...],
  "pose": [x, y, theta],
  "confidence": 0.92,
  "timestamp": "2025-01-15T10:30:45.123Z",
  "trajectory": [
    {"x": 0, "y": 0, "theta": 0, "t": 0},
    {"x": 0.1, "y": 0, "theta": 0, "t": 0.05},
    ...
  ],
  "status": "active"
}
```

---

### Running SLAM Standalone

`slam_service.py` can run as a separate process:

```bash
python slam_service.py --broker raspberrypi.local
```

With optional flags:

```bash
python slam_service.py --broker raspberrypi.local --reset
python slam_service.py --broker raspberrypi.local --output slam_map.json
```

---

### Backend Integration Flow

#### Startup

1. `main.py` starts Flask app
2. App initializes background services (SLAM, VIT relay)
3. Dashboard connects to robot MQTT broker
4. `SlamService` mirrors that connection
5. SLAM receives scan/grid/command data

#### Mapping

1. Robot publishes LiDAR scan/grid to MQTT
2. `SlamService` receives message
3. Data sent to `CartographerSLAM`
4. SLAM updates pose and occupancy grid
5. `SlamService` writes to `slam_map.json`
6. Frontend reads and displays map

#### Command Processing

1. User sends movement command from dashboard
2. Backend publishes to `yahboom/cmd`
3. `SlamService` listens to same topic
4. SLAM uses command for dead-reckoning
5. On next scan, SLAM combines command motion with scan matching

---

## Common Backend Commands

Run backend:

```bash
python main.py
```

Run with virtual environment:

```bash
.venv/bin/python main.py
```

Run via npm script:

```bash
npm run dev:backend
```

Run SLAM standalone:

```bash
python slam_service.py --broker raspberrypi.local
```

Run SLAM with reset:

```bash
python slam_service.py --broker raspberrypi.local --reset
```

---

## MQTT Topics Reference

| Topic                 | Purpose / expected payload                                                                                                                                                                                                                                                                                                                | Producers                                                                                                 | Consumers                                                                                                                                                                                                                          |
| :-------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| yahboom/cmd           | Robot command strings: movement (`fwd`, `bck`, `left`, `right`, `fwdleft`, `fwdright`, `bckleft`, `bckright`, `stop`), camera (`up`, `down`, `cleft`, `cright`, `upcleft`, `upcright`, `downcleft`, `downcright`, `crst`, `cstop`), autonomous (`auto_on`, `auto_off`), emergency (`estop_on`, `estop_off`), soft stop (`auto_soft_stop`) | Dashboard backend, other controllers                                                                      | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py), [Yahboom Car/Used/lidar_safety_node.py](Yahboom%20Car/Used/lidar_safety_node.py), [backend/slam_service.py](Yahboom%20Dashboard/backend/slam_service.py) |
| yahboom/grid          | Local occupancy grid JSON: `{w, h, grid[], resolution, robot_x, robot_y, front_dist, left_dist, right_dist, timestamp}`                                                                                                                                                                                                                   | [Yahboom Car/Used/mqtt_ros_node.py](Yahboom%20Car/Used/mqtt_ros_node.py) (5 Hz via `publish_grid_mqtt()`) | [backend/slam_service.py](Yahboom%20Dashboard/backend/slam_service.py), dashboard viewer                                                                                                                                           |
| yahboom/safety/status | Safety state: `clear`, `warning`, `blocked`, `manual_estop_triggered`, or CSV sensor data                                                                                                                                                                                                                                                 | [Yahboom Car/Used/lidar_safety_node.py](Yahboom%20Car/Used/lidar_safety_node.py)                          | Dashboard display, backend services, logging                                                                                                                                                                                       |
| yahboom/drive/status  | Drive state JSON: `{status, auto_mode, estop_active, front, left, right, …, timestamp}` (Pi `time.time()` seconds)                                                                                                                                                                                                                        | [Yahboom Car/mqtt_ros_node.py](Yahboom%20Car/mqtt_ros_node.py) (via `publish_drive_status()`)   | Dashboard (`GET /api/drive_status`), Stop-Time Test Bench, drive status widget                                                                                                                                                     |
| yahboom/scan          | Raw LiDAR LaserScan JSON: `{angle_min, angle_max, angle_increment, ranges[], timestamp}`                                                                                                                                                                                                                                                  | Robot LiDAR nodes (ROS bridge)                                                                            | [backend/slam_service.py](Yahboom%20Dashboard/backend/slam_service.py) (via `process_raw_scan()`)                                                                                                                                  |
| yahboom/vit/embedding | Base64-encoded embedding JSON: `{raw_bytes, embedding_dim, data, dtype, frame, timestamp}`                                                                                                                                                                                                                                                | [Yahboom Car/Used/webrtc_server.py](Yahboom%20Car/Used/webrtc_server.py)                                  | [backend/app/services/vit/vit_service.py](Yahboom%20Dashboard/backend/app/services/vit/vit_service.py)                                                                                                                             |
| yahboom/vit/status    | VIT encoder status: `vit_encoder_started`, `running`, `error`, frame count                                                                                                                                                                                                                                                                | VIT encoder ( webrtc_server.py)                                                                           | Dashboard/monitoring services                                                                                                                                                                                                      |
| yahboom/vit/command   | VIT control commands: `embds1`, `embds2`, `embds3` (embedding dimension), or other encoder settings                                                                                                                                                                                                                                       | Dashboard or remote controller                                                                            | [Yahboom Car/Used/webrtc_server.py](Yahboom%20Car/Used/webrtc_server.py)                                                                                                                                                           |

---

---

## Quick Start - Running the Dashboard

To run the Yahboom Dashboard on any device:

1. **Install dependencies** (one-time setup):

    ```bash
    install-dependencies.bat
    ```

    This checks for Node.js and Python, installs frontend/backend packages, and sets up the virtual environment.

2. **Run the dashboard**:
    ```bash
    run-dashboard.bat
    ```
    This launches the Flask backend (port 3000) and Vite frontend (port 5173) in **two separate terminal windows**.

Access the dashboard at: **http://localhost:5173**

Both batch files are located in the project root and automatically handle directory navigation. Make sure to run `install-dependencies.bat` before running the dashboard for the first time.

---
