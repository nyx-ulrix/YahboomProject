# Yahboom Dashboard

Web dashboard for controlling and monitoring a Yahboom robot. The frontend is a Vite + React app; the backend is a Flask server that handles MQTT, video relay, SLAM, and VIT decoding.

## Prerequisites

- Node.js 18+
- Python 3.11+

## Setup

```bash
npm run setup
```

This installs frontend dependencies and sets up the Python backend (`backend/.venv`).

## Run locally

Start the Flask backend (port 3000 by default):

```bash
npm run dev:backend
```

In a second terminal, start the Vite dev server:

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

The dashboard does **not** SSH-start `webrtc_server.py` or a separate VIT encoder. Start video on the Pi manually (SSH, VNC, or HDMI), for example:

```bash
source ~/vit_env/bin/activate
python3 webrtc_server.py
```

The backend detects a running stream with an **HTTP probe** to port `8080` (`VIDEO_SERVER_PORT`) and exposes WebRTC via `/api/webrtc/offer`.

**Removed API routes:** `POST /api/start_stream`, `POST /api/stop_stream`, `POST /api/vit/start_server`, `POST /api/vit/stop_server`.

## MobileCLIP detection (backend encoding)

Object detection is **image-to-image** and all MobileCLIP encoding + matching runs on the **backend** — the backend no longer text-decodes for the UI (`VIT_ENABLE_MODEL` defaults to `false`). The Stop Test Bench has a mutually exclusive **Detection Mode** toggle:

| Mode | Embedding source | Matching | Pi `Cae_*` | Who stops |
|------|------------------|----------|-----------|-----------|
| **Edge Only** | Browser forwards WebRTC frames to the backend, which encodes them with MobileCLIP-S1 (`open_clip`) | Backend i2i | `Cae_OFF` | Dashboard on i2i hit |
| **Cache Aware Offloading** | Pi MQTT embedding on cache **miss** | Backend i2i | `Cae_ON` | Pi on cache **hit**; dashboard on cache **miss** + i2i hit |

Flow:

- **Edge Only:** `src/lib/useEdgeFrameEncoder.ts` samples the WebRTC `<video>` at `VITE_CLIENT_EDGE_FPS`, uploads a JPEG to `POST /api/vit/edge/encode`; `vit_service.encode_frame_and_match()` encodes + matches and records the result on `/api/vit/status`.
- **Cache Aware:** the Pi publishes cache-miss embeddings on MQTT; `vit_service._handle_embedding()` matches them (Edge Only ignores MQTT embeddings).
- **Stop:** `useEdgeAwareStopLabelEstop()` polls `/api/vit/status` and fires on `reference_match.hit` ≥ 75% (`src/lib/edgeAwareStopLabelEstop.ts`).

**Backend encoding needs `torch` + `open_clip_torch` + `Pillow`** on the dashboard host (already in `requirements.txt`). No model ships to the browser. Full guide: [docs/EDGE_REFERENCE_MATCHING.md](docs/EDGE_REFERENCE_MATCHING.md).

## Environment

A `.env` file in the project root configures both frontend and backend. Common variables:

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Backend URL for the Vite proxy (default `http://localhost:3000`) |
| `MQTT_BROKER_IP` | Default Raspberry Pi MQTT broker (also used for startup auto-connect) |
| `FLASK_PORT` | Flask listen port (default `3000`) |
| `VIDEO_SERVER_PORT` | Pi WebRTC server port for HTTP health probe (default `8080`) |
| `VIDEO_PROBE_INTERVAL_SEC` | How often the backend probes Pi video (default `12`) |
| `PI_SSH_USER` / `PI_SSH_PASSWORD` / `PI_SSH_KEY_PATH` | SSH to Pi for **cache-aware test bench** only |
| `PI_VIT_VENV` | Pi venv path for `cache_aware_offloading.py` (default `~/vit_env/bin/activate`) |
| `PI_CACHE_AWARE_SCRIPT_PATH` | Path on Pi to `cache_aware_offloading.py` |
| `PI_CACHE_AWARE_LOG` | Pi log file for cache-aware script (default `/tmp/yahboom_cache_aware.log`) |
| `CACHE_SCRIPT_EMBEDDING_READY_SNIPPET` | Log substring that unlocks START in Cache Aware mode |
| `MQTT_CACHE_AWARE_READY_TOPIC` | Retained MQTT topic for embedding-ready (default `yahboom/cache_aware/ready`) |
| `VIT_REFERENCE_EMBEDDINGS_FILE` | Path to edge reference embeddings JSON (default `backend/app/services/vit/reference_embeddings.json`) |
| `VIT_REFERENCE_LABEL` | Label filter inside the reference file (default `bottle`) |
| `VIT_REFERENCE_MATCH_ENABLED` | Enable image-to-image matching (default `true`) |
| `EDGE_AWARE_REFERENCE_THRESHOLD` | Minimum cosine similarity for a reference hit (default `0.75`) |
| `VIT_ENABLE_MODEL` | Backend CLIP text-label decode — off; detection is i2i (default `false`) |
| `VIT_ENABLE_EDGE_ENCODER` | Backend MobileCLIP image encoder for Edge Only frames (default `true`) |
| `VIT_CLIENT_DETECTION_MODE` | Default mode mirrored to `vit_service` (`edge_aware` \| `cache_aware_offloading`) |
| `VIT_CLIENT_EDGE_FPS` / `VITE_CLIENT_EDGE_FPS` | Browser frame-forward rate for Edge Only (default `5`) |

## Build

```bash
npm run build
```

## Dashboard widgets

Widgets are added from the picker (**P**). Key control widgets:

| Widget | Purpose |
|--------|---------|
| **ROS Auto Button** | Sends `auto_on` / `auto_off` to the Pi (`toggleRosAuto()`). Label: **EXPLORE** / **STOP EXPLORING**. |
| **Stop-Time Test Bench** | Measures stop time after explore. Three stop modes; Pi timestamps; CSV export. |
| **Emergency Stop**, **Movement Joystick**, **Camera Joystick**, **System Status**, **Local LiDAR Grid**, **Persistent SLAM Map**, **VIT Scene Decoder**, **Event Log**, **Video Feed** | Standard monitoring and control. |

### Stop-Time Test Bench

Located in `src/app/components/Widgets.tsx` (`StopTestBenchWidget`). Stop-mode logic: `backend/app/routes/test_bench_routes.py`, `backend/app/services/vit/edge_aware_estop.py`, `backend/app/services/test_bench/cache_aware_ssh.py`. Backend image-to-image detection: `vit_service.py`; Edge Only frame forwarding: `src/lib/useEdgeFrameEncoder.ts`; bottle stop: `src/lib/edgeAwareStopLabelEstop.ts` via `useEdgeAwareStopLabelEstop()` in `src/app/hooks.ts`.

**Purpose:** Run repeated stop-time experiments, compare stop modes, and export results as CSV.

#### Detection modes (mutually exclusive)

Default: **Edge Only**. Preference is saved in browser `localStorage` (`yahboom_stop_bench_mode`).

| Mode | API value | Behaviour |
|------|-----------|-----------|
| **Edge Only** (default) | `edge_aware` | Browser forwards WebRTC frames to the backend, which encodes them with MobileCLIP and compares to the active reference library (image-to-image). Sends `auto_off` + `stop` when similarity ≥ 75% after START. Publishes `Cae_OFF`. |
| **Cache Aware Offloading** | `cache_aware_offloading` | Pi checks its own cache and stops on a **hit**. On a **cache miss** the Pi publishes the embedding over MQTT; the **backend** runs the i2i match and the dashboard stops. Publishes `Cae_ON`; START waits for `Cae_Ready`. |

Selecting a mode publishes the matching `Cae_ON` / `Cae_OFF` command and mirrors the mode into `vit_service` (`POST /api/test_bench/cache_aware`).

#### START button gating

START is disabled when:

- A test session is already active
- E-stop is latched
- Detection mode is switching (`SENDING COMMAND` pill)
- **Cache Aware Offloading:** the Pi has not confirmed cache-aware ready yet

In Cache Aware Offloading, START unlocks when the Pi confirms readiness (`Cae_Ready`, surfaced via retained MQTT on `yahboom/cache_aware/ready`). VIT encoder and video must be running on the Pi (`webrtc_server.py`) so the Pi produces embeddings.

#### How a run works

1. Select **network type** and **stop mode**, then press **START** — sends `auto_on` (same as **EXPLORE**).
2. **Command time** — Pi clock when START is pressed.
3. **Movement start** — when the Pi publishes a movement drive-status on `yahboom/drive/status`.
4. **Stop** — Pi drive-status halt, e-stop, or bottle detection (Pi cache hit and/or client i2i per mode).
5. Enter **stopping distance** (m) per row after each run.

#### Image-to-image reference matching (backend)

Detection uses **image-to-image** matching, not CLIP text labels, and all encoding + matching runs on the **backend** (`ReferenceEmbeddingStore` in `vit_service.py`). Reference vectors are captured on the Pi and activated on the dashboard (same format as Pi `cache_embeddings.json`).

**Full guide:** [docs/EDGE_REFERENCE_MATCHING.md](docs/EDGE_REFERENCE_MATCHING.md)

**Setup:** Use the **Reference Capture** panel in the VIT Scene Decoder widget to SSH-capture snapshots into categorized folders, sync them to the dashboard, and **Activate** a category. Keep **`Cae_OFF`** for Edge Only so every frame is available; Cache Aware uses `Cae_ON` and only offloads cache misses.

**Stop rule:**

- **Edge Only:** the browser forwards WebRTC frames (~`VIT_CLIENT_EDGE_FPS`) to `POST /api/vit/edge/encode`; the backend encodes + matches vs the active library.
- **Cache Aware:** the backend matches Pi cache-miss embeddings from MQTT (`_handle_embedding`).
- Both surface `reference_match` on `/api/vit/status`; the dashboard polls it and, on `hit === true` and `similarity_percent` ≥ 75% after START, sends `auto_off` + `stop`.
- Armed only after START (pre-START detections are ignored).
- Implemented in `useEdgeFrameEncoder.ts` (frame forward) + `edgeAwareStopLabelEstop.ts` (`processVitStatusForStopLabelEstop`).

#### Timing and CSV

- Stored timestamps use the **Pi clock** from MQTT, not the browser.
- Live timer extrapolates between Pi samples (display only).
- **CSV columns:** Run, Command Time (Pi), Movement Start (Pi), Stop Time (Pi), Command-to-Move (ms), Stop Duration (ms), Stop Time (s), Stopping Distance (m), Network Type, Stop Mode, Stopped by.

### Autonomous mode (Pi only)

Autonomous driving is **only** via the Pi: press **EXPLORE** or send `auto_on` to `yahboom/cmd`. The Pi’s `auto_movement_logic()` in `mqtt_ros_node.py` handles obstacle avoidance.

Client-side explore autopilot (`CLIENT AUTO` widget, `useClientAutoPilot()`, `toggleClientExplore()`) has been removed.

### Backend: drive status API

- **`GET /api/drive_status`** — latest Pi drive JSON: `status`, `robotTimestamp`, `auto_mode`, `estop`, etc.
- Topic: `yahboom/drive/status` (`MQTT_DRIVE_STATUS_TOPIC` in `config.py`).

### Backend: test bench API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/test_bench/stop_mode` | Current mode and Pi cache-aware status. Use `?force=1` to bypass probe cache. |
| `POST` | `/api/test_bench/stop_mode` | Body `{ "mode": "cache_aware_offloading" \| "edge_aware" }` — set mode; mirrors into `vit_service`. |
| `POST` | `/api/test_bench/cache_aware` | Body `{ "on": true \| false }` — publish `Cae_ON` / `Cae_OFF` and set the mode. |

**GET response fields:**

- `mode`, `edge_aware_enabled`, `needs_pi_cache_script`
- `cache_script_running`, `cache_script_detection_ready` (cache-aware readiness)

Implemented in `backend/app/routes/test_bench_routes.py`, `backend/app/services/vit/edge_aware_estop.py`, `backend/app/services/test_bench/cache_aware_ssh.py`.

### Video and VIT API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stream_status` | Pi video state (`?force=1` for live HTTP probe) |
| `POST` | `/api/webrtc/offer` | WebRTC SDP proxy to Pi `webrtc_server.py` |
| `GET` | `/api/video_feed` | MJPEG relay (when `VIDEO_USE_MJPEG_RELAY=true`) |
| `GET` | `/api/vit/status` | VIT status: `detection_mode`, `edge_encoder_ready`, `reference_ready`, `reference_active_category`, `reference_stop_threshold` |
| `POST` | `/api/vit/edge/encode` | Edge Only: upload a WebRTC frame (JPEG) for backend encode + match |
| `POST` | `/api/vit/config` | Set embedding size (512 / 1024 / 2048 B) |
| `POST` | `/api/vit/clear` | Clear VIT session history |
| `GET` | `/api/vit/export` | Download session CSV |

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Vite development server |
| `npm run dev:backend` | Flask backend (`backend/.venv`) |
| `npm run build` | Production frontend bundle |
| `npm run setup` | Install frontend + backend deps |
| `npm run setup:backend` | Backend setup only |
