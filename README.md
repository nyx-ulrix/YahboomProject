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

The default dashboard layout is **Stop Test CAO** (VIT decoder, video feed, Stop-Time Test Bench, stop button, event log). Switch layouts from the template menu in the top bar:

| Template | Use when |
|----------|----------|
| **Stop Test CAO** | Cache Aware Offloading — cosine / VIT decoder widget |
| **Stop Test YOLO** | YOLO mode — YOLO Model widget |
| **VIT View** | General VIT monitoring |
| **LiDAR View** | LiDAR grid and SLAM |

Pressing **Cache Aware** or **YOLO** on the Stop-Time Test Bench automatically switches to **Stop Test CAO** or **Stop Test YOLO** respectively (`applyStopBenchLayoutForMode()` in `testBenchStorage.ts`). On dashboard load, `syncStopModeToBackend()` restores the saved mode and matching layout.

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

`webrtc_server.py` streams WebRTC on port `8080`, serves **`GET /frame.jpg`** (latest camera JPEG for dashboard YOLO when WebRTC is the display path), and publishes JPEG frames to `yahboom/camera/frame`. `webrtc_video_only.py` is the same camera/WebRTC path **without** MQTT frame publish or `/frame.jpg` (display-only). `VIT.py` consumes those frames, runs MobileCLIP-S1, and publishes embeddings on `yahboom/vit/embedding`. Cache-aware offloading is built into `VIT.py` — the dashboard toggles it with **`Cao_ON` / `Cao_OFF`** over MQTT (no separate `cache_aware_offloading.py` script).

The backend detects a running stream with an **HTTP probe** to port `8080` (`VIDEO_SERVER_PORT`) and exposes WebRTC via `/api/webrtc/offer`.

**Removed API routes:** `POST /api/start_stream`, `POST /api/stop_stream`, `POST /api/vit/start_server`, `POST /api/vit/stop_server`.

## Yahboom Car scripts (`Yahboom Car/Code/`)

| Script | Role |
|--------|------|
| `webrtc_server.py` | Camera capture, WebRTC server (`:8080`), MQTT frame relay to VIT, `/frame.jpg` |
| `webrtc_video_only.py` | Same WebRTC display stream only — no MQTT frames, no `/frame.jpg` |
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

## Stop bench detection modes

The Stop Test Bench has a mutually exclusive **Detection Mode** toggle (**YOLO** vs **Cache Aware Offloading**). Preference is saved in browser `localStorage` (`yahboom_stop_bench_mode`).

| Mode | API value | What the Pi sends | Matching | Pi `Cao_*` | Who stops |
|------|-----------|-------------------|----------|-----------|-----------|
| **YOLO** (default) | `cloud_aware` | Every Pi embedding (for Edge path) + live video frames to backend | Backend **YOLOv8** on video; optional **Edge Stop** via browser i2i cosine match | `Cao_OFF` | **YOLO Stop** when bottle class ≥ Stop Similarity (%); **Edge Stop** when i2i match ≥ threshold |
| **Cache Aware Offloading (CAO)** | `cache_aware_offloading` | Cache-miss embeddings only | Browser i2i vs full reference library on miss | `Cao_ON` | Pi on cache **hit** (**Cache Stop**); dashboard on cache **miss** + match (**Edge Stop**) |

**CAO (Cache Aware Offloading)** — MQTT commands use the **`Cao_*`** prefix (`Cao_ON`, `Cao_OFF`, `Cao_Ready`). The former `Cae_*` names are retired; dashboard and `VIT.py` must be deployed together.

### YOLO mode

- Backend **`yolo_service.py`** runs Ultralytics YOLOv8 (`yolov8n.pt` by default) on frames from MQTT `yahboom/camera/frame` and HTTP poll fallback to `http://<pi>:8080/frame.jpg`.
- YOLO inference runs only when test-bench mode is `cloud_aware`; it pauses in CAO mode.
- When **hops** backhaul simulation is enabled, YOLO frame ingest gets hop delay (live WebRTC video is not delayed).
- **`useYoloBottleStop()`** polls `/api/yolo/status` and fires **YOLO Stop** when the stop-target class (default bottle) meets the Stop Similarity threshold.
- Widget: **YOLO Model** — latched readings, paused state when CAO is active.

### CAO / Edge (image-to-image) mode

Object matching is **image-to-image**. The **client never encodes live camera images** — it receives **embeddings from the Pi** (relayed by the backend) and matches them in the browser against the dashboard reference library. Optional backend text-label decode is off by default (`VIT_ENABLE_MODEL=false`).

Flow:

- **Pi** encodes camera frames and publishes embeddings on MQTT (`yahboom/vit/embedding`).
- **Backend** relays each embedding to `GET /api/vit/client/latest_embedding` (no live matching).
- **Browser** (`useClientReferenceDetection`) polls embeddings every 180 ms, scans the **full reference library** (`GET /api/vit/reference/library`), runs i2i match (`Yahboom Dashboard/src/lib/clientVit/`), and POSTs the result to `/api/vit/client/match_result`.
- **Edge Stop:** `useCloudAwareStopLabelEstop()` polls `/api/vit/status` every 500 ms and fires when `reference_match.stop_hit` is true and similarity ≥ Stop Similarity (%). Only the **Stop Target** category (default `target_bottle`) qualifies.

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
| `YOLO_ENABLED` | Enable YOLO inference on live frames (default `true`) |
| `YOLO_HF_REPO` | Hugging Face repo for weights (default `Ultralytics/YOLOv8`) |
| `YOLO_MODEL` | Weights filename (default `yolov8n.pt`) |
| `YOLO_CONFIDENCE` | Detection confidence floor (default `0.25`) |
| `YOLO_IMGSZ` | Inference input size (default `640`) |
| `YOLO_INFERENCE_INTERVAL_SEC` | Min seconds between YOLO runs (default `0.4`) |
| `YOLO_READINGS_STALE_MS` | Widget stale threshold (default `5000`) |

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
| **Stop-Time Test Bench** | Measures stop time after explore. YOLO vs CAO modes; Pi timestamps; CSV export. Auto-switches layout on mode buttons. |
| **YOLO Model** | Live YOLOv8 detections from `/api/yolo/status` (latched display; paused in CAO mode). |
| **Live Reference Capture** | Save the latest Pi MQTT embedding into a named category in the reference library. |
| **Upload Embeddings** | Encode a static image on the dashboard backend and save it to the reference library (requires `torch` + `open_clip_torch`). |
| **Emergency Stop**, **Movement Joystick**, **Camera Joystick**, **System Status**, **Local LiDAR Grid**, **Persistent SLAM Map**, **VIT Scene Decoder**, **Event Log**, **Video Feed** | Standard monitoring and control. |

### Stop-Time Test Bench

Located in `Yahboom Dashboard/src/app/components/Widgets.tsx` (`StopTestBenchWidget`). Stop-mode logic: `Yahboom Dashboard/backend/app/routes/test_bench_routes.py`, `Yahboom Dashboard/backend/app/services/vit/yolo_aware_estop.py` (stop-mode registry; formerly `cloud_aware_estop.py`), `Yahboom Dashboard/backend/app/services/yolo_service.py`. Cache-aware readiness comes from retained MQTT on `yahboom/cache_aware/ready` (no SSH log probe). Pi embedding relay: `vit_service.py`; client image-to-image: `src/lib/clientVit/` + `useClientReferenceDetection.ts`; **Edge Stop:** `src/lib/yoloStopLabelEstop.ts` (formerly `cloudAwareStopLabelEstop.ts`) via `useCloudAwareStopLabelEstop()`; **YOLO Stop:** `src/lib/yoloBottleStop.ts` via `useYoloBottleStop()` in `src/app/hooks.ts`. Layout sync: `applyStopBenchLayoutForMode()` / `syncStopModeToBackend()` in `testBenchStorage.ts`.

**Purpose:** Run repeated stop-time experiments, compare stop modes, and export results as CSV.

#### Detection modes (mutually exclusive)

Default: **YOLO**. Preference is saved in browser `localStorage` (`yahboom_stop_bench_mode`).

| Mode | API value | Behaviour |
|------|-----------|-----------|
| **YOLO** (default) | `cloud_aware` | Pi sends every embedding (`Cao_OFF`). Backend runs YOLOv8 on live video frames. **YOLO Stop** when bottle detection ≥ Stop Similarity (%). **Edge Stop** still available via browser i2i match on Pi embeddings. Layout: **Stop Test YOLO**. |
| **Cache Aware Offloading (CAO)** | `cache_aware_offloading` | Pi checks its own cache and stops on a **hit** (**Cache Stop**). On a **cache miss** the Pi publishes the embedding; the **browser** runs i2i match and the dashboard stops (**Edge Stop**). Publishes `Cao_ON`; START waits for `Cao_Ready`. YOLO inference is paused. Layout: **Stop Test CAO**. |

Selecting a mode publishes the matching `Cao_ON` / `Cao_OFF` command, mirrors the mode into `vit_service` and `yolo_service` (`POST /api/test_bench/cache_aware`), and switches the dashboard layout template.

#### Stop sources (event log / CSV “Stopped by”)

| Source key | Label | When |
|------------|-------|------|
| `cache_pi` | **Cache Stop** | Pi cache hit in CAO mode |
| `edge_dashboard` | **Edge Stop** | Browser i2i cosine match ≥ threshold |
| `yolo_dashboard` | **YOLO Stop** | YOLO bottle detection ≥ Stop Similarity (%) |
| `manual` | **Manual stop** | Operator e-stop or manual halt |

#### START button gating

START is disabled when:

- A test session is already active
- E-stop is latched
- Detection mode is switching (`SENDING COMMAND` pill)
- **Cache Aware Offloading:** the Pi has not confirmed cache-aware ready yet

In Cache Aware Offloading, START unlocks when the Pi confirms readiness (`Cao_Ready`, surfaced via retained MQTT on `yahboom/cache_aware/ready`). The test bench reads this through `cache_aware_mqtt_ready` / `cache_script_running` in `GET /api/test_bench/stop_mode` — not via SSH log tailing. VIT encoder and video must be running on the Pi (`webrtc_server.py`) so the Pi produces embeddings. For display-only WebRTC without MQTT/`/frame.jpg` frames, use `webrtc_video_only.py` instead (YOLO/VIT will not receive frames).

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
- **Stop Target** dropdown on the Stop Test Bench — choose which category qualifies for Edge Stop (persisted in browser `localStorage`).
- Keep **`Cao_OFF`** for YOLO mode so every embedding is relayed for Edge matching.
- Keep **`Cao_ON`** for CAO mode so the Pi filters to cache misses only.

**Stop rule:**

- **YOLO:** YOLOv8 bottle class ≥ Stop Similarity (%) triggers **YOLO Stop**; browser i2i can still trigger **Edge Stop** on Pi embeddings.
- **CAO:** Pi stops on cache hit locally (**Cache Stop**); on cache miss the browser matches the forwarded embedding against the library (**Edge Stop**).
- Edge path POSTs match results to `/api/vit/client/match_result`; `/api/vit/status` is polled and, on `stop_hit === true` and similarity ≥ threshold after START, sends `auto_off` + `stop`.
- Armed only after START (pre-START detections are ignored).
- Implemented in `useClientReferenceDetection.ts` + `yoloStopLabelEstop.ts` (`processVitStatusForStopLabelEstop`) and `yoloBottleStop.ts` (`useYoloBottleStop`).

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

Implemented in `Yahboom Dashboard/backend/app/routes/test_bench_routes.py`, `Yahboom Dashboard/backend/app/services/vit/yolo_aware_estop.py`.

### Video and VIT API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stream_status` | Pi video state (`?force=1` for live HTTP probe) |
| `POST` | `/api/webrtc/offer` | WebRTC SDP proxy to Pi `webrtc_server.py` (or `webrtc_video_only.py`) |
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

### YOLO API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/yolo/status` | Latest YOLO detections, model state, paused flag (when CAO mode active) |
| `POST` | `/api/yolo/config` | Body `{ "enabled": true }` and/or `{ "confidence": 0.25 }` or `{ "confidence_percent": 25 }` |
| `POST` | `/api/yolo/clear` | Clear YOLO session history |

Implemented in `Yahboom Dashboard/backend/app/services/yolo_service.py`, `Yahboom Dashboard/backend/app/routes/yolo_routes.py`.

## Graphify knowledge graph

This repo maintains a local code knowledge graph under **`graphify-out/`** (gitignored). Cursor agents use it via `.cursor/rules/graphify.mdc` to navigate cross-file dependencies before reading source.

| Command | Purpose |
|---------|---------|
| `graphify update .` | Re-extract AST graph after code changes (no API cost) |
| `graphify query "<question>"` | Scoped subgraph for architecture questions |
| `graphify path "<A>" "<B>"` | Dependency path between two symbols |
| `graphify explain "<concept>"` | Nodes related to a concept |

Key symbols for recent stop-bench work:

- **`stop_test_cao`** / **`stop_test_yolo`** — layout templates in `store.ts`
- **`applyStopBenchLayoutForMode()`** — mode → layout switch in `testBenchStorage.ts`
- **`YoloService`** — backend YOLOv8 on live frames (`yolo_service.py`)
- **`Cao_ON` / `Cao_OFF` / `Cao_Ready`** — MQTT cache-aware commands in `VIT.py` and `mqtt_service.py`

Open `graphify-out/GRAPH_REPORT.md` for community hubs, god nodes, and import cycles. Run `graphify update .` after modifying code so the graph stays current.

## Scripts

Run from **`Yahboom Dashboard/`**:

| Script | Description |
|--------|-------------|
| `npm run dev` | Vite development server |
| `npm run dev:backend` | Flask backend (`backend/.venv`) via `scripts/run-backend.mjs` |
| `npm run build` | Production frontend bundle |
| `npm run setup` | Install frontend + backend deps |
| `npm run setup:backend` | Create `backend/.venv` and pip install via `scripts/setup-backend.mjs` |
