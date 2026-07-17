# Yahboom Project

Monorepo for a Yahboom robot stack:

- **`Yahboom Dashboard/`** — Vite + React web dashboard and Flask backend (MQTT, video relay, SLAM, VIT embedding relay)
- **`Yahboom Car/Code/`** — Raspberry Pi scripts (WebRTC, MobileCLIP/VIT, ROS 2 bridge, LiDAR safety)

## Prerequisites

- Node.js 18+
- Python 3.11+

## Dashboard setup

All npm commands run from `Yahboom Dashboard/`:

```bash
cd "Yahboom Dashboard"
npm run setup
```

This installs frontend dependencies and sets up the Python backend (`Yahboom Dashboard/backend/.venv`).

## Run locally

From `Yahboom Dashboard/`:

Start the Flask backend (port 3000 by default):

```bash
npm run dev:backend
```

In a second terminal:

```bash
npm run dev
```

The frontend proxies `/api/*` to the backend. Open the URL shown in the Vite terminal.

**Important:** Run only one backend instance. If port 3000 is already in use, `main.py` exits with an error — stop the other terminal first.

## Navigation and robot connection

The top bar has **Dashboard** and **Controller** view tabs. Robot MQTT connection is inline on the same row:

- **IP / hostname** — hidden by default; click the **eye** icon to show or hide the field
- **Connect** — calls `POST /api/connect` with the entered broker address (saved in browser `localStorage`)
- **Status dot** — green when connected (hover for the active broker host)

The backend also auto-connects to `MQTT_BROKER_IP` on startup. The settings modal has been removed; connection controls live only in the top bar.

## Default layout

The default dashboard layout is **Stop Test** (VIT decoder, video feed, Stop-Time Test Bench, stop button, event log). Switch layouts from the template menu in the top bar (**VIT View**, **LiDAR View**, **Stop Test**).

## Video and VIT on the Pi

The dashboard does **not** SSH-start Pi scripts. Copy `Yahboom Car/Code/` to the robot and start services manually (SSH, VNC, or HDMI).

**Terminal 1 — video + camera frames:**

```bash
source ~/vit_env/bin/activate
cd ~/YahboomProject/Yahboom\ Car/Code   # or your deploy path
python3 webrtc_server.py
```

**Terminal 2 — MobileCLIP embeddings + cache-aware mode:**

```bash
source ~/vit_env/bin/activate
cd ~/YahboomProject/Yahboom\ Car/Code
python3 VIT.py
```

`webrtc_server.py` streams WebRTC on port `8080` and publishes JPEG frames to `yahboom/camera/frame`. `VIT.py` consumes those frames, runs MobileCLIP-S1, and publishes embeddings on `yahboom/vit/embedding`. Cache-aware offloading is built into `VIT.py` — the dashboard toggles it with `Cao_ON` / `Cao_OFF` over MQTT (no separate `cache_aware_offloading.py` script).

The backend detects a running stream with an **HTTP probe** to port `8080` (`VIDEO_SERVER_PORT`) and exposes WebRTC via `/api/webrtc/offer`.

**Removed API routes:** `POST /api/start_stream`, `POST /api/stop_stream`, `POST /api/vit/start_server`, `POST /api/vit/stop_server`.

## Yahboom Car scripts (`Yahboom Car/Code/`)

| Script | Role |
|--------|------|
| `webrtc_server.py` | Camera capture, WebRTC server (`:8080`), MQTT frame relay to VIT |
| `VIT.py` | MobileCLIP-S1 encoding, Pi cache comparison, embedding publish, `Cao_ON`/`Cao_OFF` handling |
| `mqtt_ros_node.py` | MQTT ↔ ROS 2 bridge, autonomous explore (`auto_on`/`auto_off`), drive status |
| `lidar_safety_node.py` | LiDAR-based obstacle safety |
| `capture_bottle_cache_multi.py` | Build `/home/pi/cache_embeddings.json` from live embeddings (multi-angle capture) |
| `capture_reference_snapshot.py` | Save individual reference snapshots while VIT is running |

**Also on the Pi (typical):** run `mqtt_ros_node.py` (and optionally `lidar_safety_node.py`) in separate terminals so movement commands and drive status reach ROS.

**Repo layout notes:**

- Active robot code lives in `Yahboom Car/Code/` (formerly `Used/`).
- `Unused/cache_aware_offloading.py` and `Old_WebRTC_Server` were removed — cache-aware logic is in `VIT.py`.
- `Yahboom Car/Embedding Snashots/` holds example embedding text dumps for reference only (not loaded at runtime).

**Pi cache file:** `/home/pi/cache_embeddings.json` — create with `capture_bottle_cache_multi.py` while `VIT.py` is running, or copy from the dashboard reference library when testing cache-aware mode locally on the Pi.

## MobileCLIP detection (client i2i from Pi embeddings)

Object detection is **image-to-image**. The **client never encodes images** — it receives **image embeddings generated on the Pi** (relayed by the backend) and matches them in the browser against the dashboard reference library. Optional backend text-label decode is off by default (`VIT_ENABLE_MODEL=false`). The Stop Test Bench has a mutually exclusive **Detection Mode** toggle:

| Mode | What the Pi sends | Matching | Pi `Cao_*` | Who stops |
|------|-------------------|----------|-----------|-----------|
| **Cloud Only** | Every Pi embedding | Browser i2i vs full reference library; stop on **Stop Target** category | `Cao_OFF` | Dashboard on `stop_hit` |
| **Cache Aware Offloading** | Cache-miss embeddings only | Browser i2i vs full reference library on miss | `Cao_ON` | Pi on cache **hit**; dashboard on cache **miss** + `stop_hit` |

Flow:

- **Pi** encodes camera frames and publishes embeddings on MQTT (`yahboom/vit/embedding`).
- **Backend** relays each embedding to `GET /api/vit/client/latest_embedding` (no live matching).
- **Browser** (`useClientReferenceDetection`) polls embeddings every 180 ms, scans the **full reference library** (`GET /api/vit/reference/library`), runs i2i match (`Yahboom Dashboard/src/lib/clientVit/`), and POSTs the result to `/api/vit/client/match_result`.
- **Stop:** `useCloudAwareStopLabelEstop()` polls `/api/vit/status` every 500 ms and fires when `reference_match.stop_hit` is true and similarity ≥ 70% (`Yahboom Dashboard/src/lib/cloudAwareStopLabelEstop.ts`). Only the **Stop Target** category (default `target_bottle`) qualifies — other library categories are shown in the VIT decoder but do not trigger stop.

Live detection never encodes images on the dashboard host. The optional **Upload Embeddings** widget can encode static images on the backend when `torch` + `open_clip_torch` are installed.

## Environment

A `.env` file in **`Yahboom Dashboard/`** configures both frontend and backend. Common variables:

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Backend URL for the Vite proxy (default `http://localhost:3000`) |
| `MQTT_BROKER_IP` | Default Raspberry Pi MQTT broker (also used for startup auto-connect) |
| `FLASK_PORT` | Flask listen port (default `3000`) |
| `VIDEO_SERVER_PORT` | Pi WebRTC server port for HTTP health probe (default `8080`) |
| `VIDEO_PROBE_INTERVAL_SEC` | How often the backend probes Pi video (default `12`) |
| `PI_SSH_USER` / `PI_SSH_PASSWORD` / `PI_SSH_KEY_PATH` | _(legacy)_ unused; old SSH cache-script helpers remain in backend but are not called |
| `PI_VIT_VENV` | Pi venv path (default `~/vit_env/bin/activate`) |
| `PI_CACHE_AWARE_SCRIPT_PATH` | _(legacy)_ default `VIT.py`; cache-aware runs inside `VIT.py` |
| `PI_CACHE_AWARE_LOG` | _(legacy)_ Pi log path from old SSH cache-script flow |
| `CACHE_SCRIPT_EMBEDDING_READY_SNIPPET` | _(legacy)_ log substring from removed `cache_aware_offloading.py` |
| `MQTT_CACHE_AWARE_READY_TOPIC` | Retained MQTT topic for VIT cache-aware ready (default `yahboom/cache_aware/ready`) |
| `VIT_REFERENCE_EMBEDDINGS_FILE` | Legacy active reference copy used by activate/sync |
| `VIT_REFERENCE_LIBRARY_DIR` | Category folders for captured/uploaded references |
| `VIT_REFERENCE_LABEL` | Default label for new captures (default `target bottle`) |
| `VIT_STOP_REFERENCE_CATEGORY` | Category slug that qualifies for cloud stop (default `target_bottle`) |
| `VIT_REFERENCE_MATCH_ENABLED` | Reference store enabled for status/activate (default `true`) |
| `CLOUD_AWARE_REFERENCE_THRESHOLD` | Minimum cosine similarity floor for a stop hit (default `0.70`) |
| `VIT_ENABLE_MODEL` | Optional backend CLIP text-label decode — off; live detection is client i2i (default `false`) |
| `VIT_CLIENT_DETECTION_MODE` | Default mode mirrored to `vit_service` (`cloud_aware` \| `cache_aware_offloading`) |

## Build

From `Yahboom Dashboard/`:

```bash
npm run build
```

## Dashboard widgets

Widgets are added from the picker (**P**). Key control widgets:

| Widget | Purpose |
|--------|---------|
| **ROS Auto Button** | Sends `auto_on` / `auto_off` to the Pi (`toggleRosAuto()`). Label: **EXPLORE** / **STOP EXPLORING**. |
| **Stop-Time Test Bench** | Measures stop time after explore. Three stop modes; Pi timestamps; CSV export. |
| **Live Reference Capture** | Save the latest Pi MQTT embedding into a named category in the reference library. |
| **Upload Embeddings** | Encode a static image on the dashboard backend and save it to the reference library (requires `torch` + `open_clip_torch`). |
| **Emergency Stop**, **Movement Joystick**, **Camera Joystick**, **System Status**, **Local LiDAR Grid**, **Persistent SLAM Map**, **VIT Scene Decoder**, **Event Log**, **Video Feed** | Standard monitoring and control. |

### Stop-Time Test Bench

Located in `Yahboom Dashboard/src/app/components/Widgets.tsx` (`StopTestBenchWidget`). Stop-mode logic: `Yahboom Dashboard/backend/app/routes/test_bench_routes.py`, `Yahboom Dashboard/backend/app/services/vit/cloud_aware_estop.py`. Cache-aware readiness comes from retained MQTT on `yahboom/cache_aware/ready` (no SSH log probe). Pi embedding relay: `vit_service.py`; client image-to-image: `src/lib/clientVit/` + `useClientReferenceDetection.ts`; bottle stop: `src/lib/cloudAwareStopLabelEstop.ts` via `useCloudAwareStopLabelEstop()` in `src/app/hooks.ts`.

**Purpose:** Run repeated stop-time experiments, compare stop modes, and export results as CSV.

#### Detection modes (mutually exclusive)

Default: **Cloud Only**. Preference is saved in browser `localStorage` (`yahboom_stop_bench_mode`).

| Mode | API value | Behaviour |
|------|-----------|-----------|
| **Cloud Only** (default) | `cloud_aware` | Pi sends every embedding (`Cao_OFF`); the **browser** matches each against the dashboard reference library (image-to-image). Sends `auto_off` + `stop` when similarity ≥ 70% after START. |
| **Cache Aware Offloading** | `cache_aware_offloading` | Pi checks its own cache and stops on a **hit**. On a **cache miss** the Pi publishes the embedding; the **browser** runs i2i match and the dashboard stops. Publishes `Cao_ON`; START waits for `Cao_Ready`. |

Selecting a mode publishes the matching `Cao_ON` / `Cao_OFF` command and mirrors the mode into `vit_service` (`POST /api/test_bench/cache_aware`).

#### START button gating

START is disabled when:

- A test session is already active
- E-stop is latched
- Detection mode is switching (`SENDING COMMAND` pill)
- **Cache Aware Offloading:** the Pi has not confirmed cache-aware ready yet

In Cache Aware Offloading, START unlocks when the Pi confirms readiness (`Cao_Ready`, surfaced via retained MQTT on `yahboom/cache_aware/ready`). The test bench reads this through `cache_aware_mqtt_ready` / `cache_script_running` in `GET /api/test_bench/stop_mode` — not via SSH log tailing. VIT encoder and video must be running on the Pi (`webrtc_server.py`) so the Pi produces embeddings.

#### How a run works

1. Select **network type** and **stop mode**, then press **START** — sends `auto_on` (same as **EXPLORE**).
2. **Command time** — Pi clock when START is pressed.
3. **Movement start** — when the Pi publishes a movement drive-status on `yahboom/drive/status`.
4. **Stop** — Pi drive-status halt, e-stop, or bottle detection (Pi cache hit and/or client i2i per mode).
5. Enter **stopping distance** (m) per row after each run.

#### Image-to-image reference matching (client)

Detection uses **image-to-image** matching in the **browser**, not CLIP text labels. The Pi generates live embeddings; the client never encodes live camera frames. Reference vectors live in `Yahboom Dashboard/backend/app/services/vit/reference_library/` (same JSON schema as Pi `/home/pi/cache_embeddings.json` from `Yahboom Car/Code/capture_bottle_cache_multi.py`). The Pi cache file is used only for on-Pi cache checks in Cache Aware mode.

**Setup:**

- **Live Reference Capture** widget — save relayed Pi embeddings into categorized folders (default category `target_bottle`).
- **Upload Embeddings** widget — encode a static image on the dashboard backend and save it to the library.
- **Stop Target** dropdown on the Stop Test Bench — choose which category qualifies for cloud stop (persisted in browser `localStorage`).
- Keep **`Cao_OFF`** for Cloud Only so every embedding is relayed.

**Stop rule:**

- **Cloud Only:** Pi sends every embedding; browser scans the full library but stops only when the best match is in the **Stop Target** category with `stop_hit === true`.
- **Cache Aware:** Pi stops on cache hit locally; on cache miss the browser matches the forwarded embedding against the library.
- Both POST match results to `/api/vit/client/match_result`; `/api/vit/status` is polled and, on `stop_hit === true` and `similarity_percent` ≥ 70% after START, sends `auto_off` + `stop`.
- Armed only after START (pre-START detections are ignored).
- Implemented in `useClientReferenceDetection.ts` + `cloudAwareStopLabelEstop.ts` (`processVitStatusForStopLabelEstop`).

#### Timing and CSV

- Stored timestamps use the **Pi clock** from MQTT, not the browser.
- Live timer extrapolates between Pi samples (display only).
- **CSV columns:** Run, Command Time (Pi), Movement Start (Pi), Stop Time (Pi), Command-to-Move (ms), Stop Duration (ms), Stop Time (s), Stopping Distance (m), Network Type, Stop Mode, Stopped by.

### Autonomous mode (Pi only)

Autonomous driving is **only** via the Pi: press **EXPLORE** or send `auto_on` to `yahboom/cmd`. The Pi’s `auto_movement_logic()` in `Yahboom Car/Code/mqtt_ros_node.py` handles obstacle avoidance.

Client-side explore autopilot (`CLIENT AUTO` widget, `useClientAutoPilot()`, `toggleClientExplore()`) has been removed.

### Backend: drive status API

- **`GET /api/drive_status`** — latest Pi drive JSON: `status`, `robotTimestamp`, `auto_mode`, `estop`, etc.
- Topic: `yahboom/drive/status` (`MQTT_DRIVE_STATUS_TOPIC` in `config.py`).

### Backend: test bench API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/test_bench/stop_mode` | Current mode and Pi cache-aware status. Use `?force=1` to bypass probe cache. |
| `POST` | `/api/test_bench/stop_mode` | Body `{ "mode": "cache_aware_offloading" \| "cloud_aware" }` — set mode; mirrors into `vit_service`. |
| `POST` | `/api/test_bench/cache_aware` | Body `{ "on": true \| false }` — publish `Cao_ON` / `Cao_OFF` to `VIT.py` and set the mode. |
| `GET` / `DELETE` | `/api/test_bench/latest_detection` | Latest Pi cache-aware detection from MQTT `yahboom/detect/status` (`DELETE` clears it). |

**GET `/api/test_bench/stop_mode` response fields:**

- `mode`, `cloud_aware_enabled`, `needs_pi_cache_script`
- `cache_script_running`, `cache_script_detection_ready`, `cache_aware_mqtt_ready` (all mirror Pi MQTT readiness on `yahboom/cache_aware/ready`)

Implemented in `Yahboom Dashboard/backend/app/routes/test_bench_routes.py`, `Yahboom Dashboard/backend/app/services/vit/cloud_aware_estop.py`.

### Video and VIT API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stream_status` | Pi video state (`?force=1` for live HTTP probe) |
| `POST` | `/api/webrtc/offer` | WebRTC SDP proxy to Pi `webrtc_server.py` |
| `GET` | `/api/video_feed` | MJPEG relay (when `VIDEO_USE_MJPEG_RELAY=true`) |
| `GET` | `/api/vit/status` | VIT status: `detection_mode`, `reference_ready`, `reference_stop_ready`, `reference_stop_category`, `reference_library_categories`, `reference_stop_threshold` |
| `GET` | `/api/vit/client/latest_embedding` | Latest Pi embedding relayed to browser (dedupe on `seq`) |
| `GET` | `/api/vit/reference/library` | Full reference library for browser i2i scan (all categories at an embedding size) |
| `GET` / `POST` | `/api/vit/reference/stop_category` | Get or set the category that qualifies for cloud stop |
| `GET` | `/api/vit/reference/active` | Legacy active reference copy (`reference_embeddings.json`) |
| `POST` | `/api/vit/reference/capture` | Save latest relayed Pi embedding to a category |
| `POST` | `/api/vit/reference/activate` | Copy a category to `reference_embeddings.json` (used by embedding-size slider sync) |
| `POST` | `/api/vit/reference/upload` | Encode a static image and save to the library |
| `GET` | `/api/vit/reference/categories` | List library categories |
| `GET` | `/api/vit/reference/samples` | List all library samples (for upload/move UI) |
| `POST` | `/api/vit/reference/move` | Move a sample between categories |
| `GET` | `/api/vit/reference/sample-image/<category>/<sample_id>` | Preview image for an uploaded sample |
| `POST` | `/api/vit/client/match_result` | Record match the browser computed |
| `POST` | `/api/vit/config` | Set embedding size (512 / 1024 / 2048 B) |
| `POST` | `/api/vit/clear` | Clear VIT session history |
| `GET` | `/api/vit/export` | Download session CSV |

## Scripts

Run from **`Yahboom Dashboard/`**:

| Script | Description |
|--------|-------------|
| `npm run dev` | Vite development server |
| `npm run dev:backend` | Flask backend (`backend/.venv`) via `scripts/run-backend.mjs` |
| `npm run build` | Production frontend bundle |
| `npm run setup` | Install frontend + backend deps |
| `npm run setup:backend` | Create `backend/.venv` and pip install via `scripts/setup-backend.mjs` |
