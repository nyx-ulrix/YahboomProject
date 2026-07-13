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

The backend detects a running stream with an **HTTP probe** to port `8080` (`VIDEO_SERVER_PORT`) and exposes WebRTC via `/api/webrtc/offer`. VIT scene decoding uses MQTT embeddings from the running server (`vit_service.py`).

**Removed API routes:** `POST /api/start_stream`, `POST /api/stop_stream`, `POST /api/vit/start_server`, `POST /api/vit/stop_server`.

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
| `CACHE_SCRIPT_EMBEDDING_READY_SNIPPET` | Log substring that unlocks START in cache/hybrid mode |
| `MQTT_CACHE_AWARE_READY_TOPIC` | Retained MQTT topic for embedding-ready (default `yahboom/cache_aware/ready`) |
| `VIT_REFERENCE_EMBEDDINGS_FILE` | Path to edge reference embeddings JSON (default `backend/app/services/vit/reference_embeddings.json`) |
| `VIT_REFERENCE_LABEL` | Label filter inside the reference file (default `bottle`) |
| `VIT_REFERENCE_MATCH_ENABLED` | Enable image-to-image matching on the dashboard (default `true`) |
| `EDGE_AWARE_REFERENCE_THRESHOLD` | Minimum cosine similarity for a reference hit (default `0.75`) |

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

Located in `src/app/components/Widgets.tsx` (`StopTestBenchWidget`). Stop-mode logic: `backend/app/routes/test_bench_routes.py`, `backend/app/services/vit/edge_aware_estop.py`, `backend/app/services/test_bench/cache_aware_ssh.py`. Edge-aware bottle stop on the client: `src/lib/edgeAwareStopLabelEstop.ts` via `useEdgeAwareStopLabelEstop()` in `src/app/hooks.ts`.

**Purpose:** Run repeated stop-time experiments, compare stop modes, and export results as CSV.

#### Stop modes (3-position slider)

Default: **Edge** (right). Preference is saved in browser `localStorage` (`yahboom_stop_bench_mode`).

| Mode | API value | Behaviour |
|------|-----------|-----------|
| **Cache** (left) | `cache_aware_offloading` | Pi runs `cache_aware_offloading.py` in lxterminal. Bottle stop is detected on the Pi (CLIP embeddings via MQTT). Dashboard does **not** send `auto_off` on stop. |
| **Hybrid** (center) | `hybrid` | Pi script **and** dashboard VIT bottle stop are both armed. **First trigger wins**; run records which path stopped (`Pi script · bottle` vs `Dashboard VIT · bottle`). |
| **Edge** (right, default) | `edge_aware` | Dashboard compares live embeddings to **reference embeddings** (image-to-image). Sends `auto_off` + `stop` when similarity ≥ 75% after START. Pi cache script is **stopped** when this mode is selected. |

Changing to Cache or Hybrid starts the Pi script once (one lxterminal per slider change). Switching between Cache and Hybrid does **not** restart the script. Switching to Edge kills the Pi script and closes its terminal.

#### START button gating

START is disabled when:

- A test session is already active
- E-stop is latched
- Stop mode is switching (`SCRIPT…` pill)
- **Cache or Hybrid:** Pi script is not running, or bottle embedding is not ready

For Cache/Hybrid, START unlocks when the backend reports both:

- `cache_script_running: true` — `cache_aware_offloading.py` process on the Pi
- `cache_script_detection_ready: true` — `[DETECT] Text embedding ready: 'a water bottle'…` in the Pi log **or** retained MQTT on `yahboom/cache_aware/ready`

VIT encoder and video must be running on the Pi (`webrtc_server.py`) so the Pi script receives embeddings. Status pill shows **WARMUP** while the script is up but embedding is not ready yet.

#### How a run works

1. Select **network type** and **stop mode**, then press **START** — sends `auto_on` (same as **EXPLORE**).
2. **Command time** — Pi clock when START is pressed.
3. **Movement start** — when the Pi publishes a movement drive-status on `yahboom/drive/status`.
4. **Stop** — Pi drive-status halt, e-stop, or bottle detection (Pi script and/or dashboard VIT per mode).
5. Enter **stopping distance** (m) per row after each run.

#### Edge-aware reference matching (dashboard VIT)

Edge stop uses **image-to-image** matching, not CLIP text labels. The dashboard loads reference embeddings from `reference_embeddings.json` (same format as Pi `cache_embeddings.json`) and compares each live MQTT embedding via cosine similarity.

**Full guide:** [docs/EDGE_REFERENCE_MATCHING.md](docs/EDGE_REFERENCE_MATCHING.md)

**Setup (copy from Pi):**

1. On the Pi, run `capture_bottle_cache_multi.py` while `VIT.py` publishes `yahboom/vit/embedding`.
2. Copy `/home/pi/cache_embeddings.json` to `Yahboom Dashboard/backend/app/services/vit/reference_embeddings.json`.
3. Restart the Flask backend.
4. Keep **`Cae_OFF`** on the Pi so cache hits do not suppress embedding publish to the edge.

**Stop rule:**

- Polls `GET /api/vit/status` every 500 ms.
- On a **new** decode after START with `reference_match.hit === true` and `similarity_percent` ≥ 75%, sends `auto_off` + `stop`.
- Armed only after START (pre-START detections are ignored).
- Implemented in `edgeAwareStopLabelEstop.ts`; matching in `vit_service.py` (`ReferenceEmbeddingStore`).

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
| `GET` | `/api/test_bench/stop_mode` | Current mode and Pi script status. Use `?force=1` to bypass probe cache. |
| `POST` | `/api/test_bench/stop_mode` | Body `{ "mode": "cache_aware_offloading" \| "hybrid" \| "edge_aware" }` — set mode and start/stop Pi script. |

**GET response fields (cache/hybrid):**

- `mode`, `edge_aware_enabled`, `needs_pi_cache_script`
- `cache_script_running`, `cache_script_detection_ready`
- `cache_script_log`, `cache_script_launch_mode` (when applicable)

Implemented in `backend/app/routes/test_bench_routes.py`, `backend/app/services/vit/edge_aware_estop.py`, `backend/app/services/test_bench/cache_aware_ssh.py`.

### Video and VIT API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stream_status` | Pi video state (`?force=1` for live HTTP probe) |
| `POST` | `/api/webrtc/offer` | WebRTC SDP proxy to Pi `webrtc_server.py` |
| `GET` | `/api/video_feed` | MJPEG relay (when `VIDEO_USE_MJPEG_RELAY=true`) |
| `GET` | `/api/vit/status` | VIT status: reference match, encoder activity, `reference_ready`, `reference_count` |
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
