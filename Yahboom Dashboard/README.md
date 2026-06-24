# Yahboom Dashboard

Web dashboard for controlling and monitoring a Yahboom robot. The frontend is a Vite + React app; the backend is a Flask server that handles MQTT, video relay, SLAM, and VIT decoding.

## Prerequisites

- Node.js 18+
- Python 3.11+

## Setup

```bash
npm run setup
```

This installs frontend dependencies and sets up the Python backend.

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

## Environment

A `.env` file in the project root configures both frontend and backend. Common variables:

- `VITE_API_URL` — backend URL for the Vite proxy (default `http://localhost:3000`)
- `MQTT_BROKER_IP` / `FLASK_PORT` — broker and Flask listen port

## Build

```bash
npm run build
```

## Dashboard widgets

Widgets are added from the picker (**P**). Key control widgets:

| Widget | Purpose |
|--------|---------|
| **ROS Auto Button** | Sends `auto_on` / `auto_off` to the Pi (`toggleRosAuto()`). Label: **EXPLORE** / **STOP EXPLORING**. |
| **Stop-Time Test Bench** | Measures stop time after explore. Cache-aware or edge-aware (VIT stop label) modes; Pi timestamps; CSV export. |
| **Emergency Stop**, **Movement Joystick**, **Camera Joystick**, **System Status**, **Local LiDAR Grid**, **Persistent SLAM Map**, **VIT Scene Decoder**, **Event Log**, **Video Feed** | Standard monitoring and control. |

### Stop-Time Test Bench

Located in `src/app/components/Widgets.tsx` (`StopTestBenchWidget`). Stop-mode and edge-aware e-stop logic live in `src/lib/edgeAwareStopLabelEstop.ts`; VIT polling runs via `useEdgeAwareStopLabelEstop()` in `src/app/hooks.ts`.

**Purpose:** Run repeated stop-time experiments, compare stop modes, and export results as CSV.

**Stop modes** (toggle on the widget; persisted on the backend via `GET`/`POST /api/test_bench/stop_mode`):

| Mode | Label in UI | Behaviour |
|------|-------------|-----------|
| `cache_aware_offloading` | Cache aware stop | Default. Stop is detected from Pi drive-status only (same as before edge-aware was added). |
| `edge_aware` | Edge aware stop | VIT scene decoder sends `auto_soft_stop` when the configured **stop label** is detected (see below). Pi drive-status still ends the run and records stop time. |

**How a run works:**

1. User selects **network type** and **stop mode**, then presses **START** — same action as **EXPLORE** (`toggleRosAuto()` → `auto_on` on the Pi). START is disabled while a session is active or while e-stop is latched.
2. **Command time** — latest Raspberry Pi clock (`time.time()` from MQTT) is captured when START is pressed.
3. **Movement start** — official run start is when the Pi publishes a movement drive-status (e.g. `auto_creep_forward`, `moving_forward`, `auto_forward_clear`) on `yahboom/drive/status`.
4. **Stop** — run ends automatically when the Pi reports a halt (`stopped`, `auto_soft_stop`, `auto_disabled`, `estop_active`, etc.), or when e-stop is engaged (manual, backend, or grid). Edge-aware VIT sends `auto_soft_stop` (not e-stop). There is no manual STOP button on the widget.
5. User enters **stopping distance** (m) per row after each run.
6. **Network type** and **stop mode** are stored per run and included in CSV export.

**Edge-aware stop label (VIT):**

- Trigger class is `bottle` (`EDGE_AWARE_STOP_LABEL` in `edgeAwareStopLabelEstop.ts`, matching `backend/app/services/vit/labels.json`).
- The dashboard polls `GET /api/vit/status` every 500 ms (`useEdgeAwareStopLabelEstop`). When any decoder row for that label is **≥ 40%** confidence on a **new decode after START**, it sends `auto_soft_stop` via MQTT (halts the robot without latching e-stop).
- Stop-label soft-stop **only runs after START** (`setStopLabelEstopArmed(true, …)` seeds the current decode so pre-START detections are ignored). Toggling edge-aware mode on without pressing START does **not** send stop commands.
- Requires VIT encoder and video pipeline running (see VIT Scene Decoder widget / top bar).

**Timing:**

- **Recorded runs** — all saved timestamps (`commandSentAt`, `startedAt`, `stoppedAt`, durations) use the **Pi clock** from MQTT (`robotTimestamp` / `timestamp` on drive-status and grid payloads), sampled at the moment each event is detected. The browser clock is not used for stored results.
- **Live timer** — one session poll loop (~100 ms) handles movement detection, stop detection, and the on-screen counter. Between Pi MQTT messages the display extrapolates from the last Pi sample (`lastPiTimestamp + wall-clock delta`) so the counter keeps ticking; extrapolation is display-only and is not written to CSV.

**Session poll APIs:** `GET /api/drive_status`, `GET /api/grid_status`, `GET /api/status` (backend e-stop), during an active run.

**CSV columns:** Run, Command Time (Pi), Movement Start (Pi), Stop Time (Pi), Command-to-Move (ms), Stop Duration (ms), Stop Time (s), Stopping Distance (m), Network Type, **Stop Mode**.

**Backend:** `backend/app/routes/test_bench_routes.py` — stop-mode selection; `backend/app/services/vit/edge_aware_estop.py` — server-side mode flag (bottle e-stop is handled on the dashboard client).

### Autonomous mode (Pi only)

The dashboard no longer runs client-side explore autopilot. Previously, a **CLIENT AUTO** widget and `useClientAutoPilot()` sent movement commands from the browser based on LiDAR grid data. That has been **removed**.

Autonomous driving is **only** via the Pi: press **EXPLORE** (ROS Auto Button) or send `auto_on` to `yahboom/cmd`. The Pi’s `auto_movement_logic()` in `mqtt_ros_node.py` handles obstacle avoidance.

**Removed code (for reference):**

- Widget: `auto_movement_button_widget` (CLIENT AUTO)
- Hook: `useClientAutoPilot()` in `src/app/hooks.ts`
- Function: `toggleClientExplore()` in `src/lib/Controls.tsx`
- Store field: `exploreActive`

### Backend: drive status API

The Flask backend subscribes to `yahboom/drive/status` and exposes:

- **`GET /api/drive_status`** — latest JSON from the Pi: `status`, `robotTimestamp` (Pi `time.time()` seconds), `auto_mode`, `estop`, etc.

Configured via `MQTT_DRIVE_STATUS_TOPIC` in `backend/config.py` (default `yahboom/drive/status`). Implemented in `backend/app/services/mqtt_service.py` (`_parse_drive_message`, `get_drive_status`).

Grid MQTT payloads also pass through `robotTimestamp` and `auto_mode` on **`GET /api/grid_status`**.

### Backend: test bench API

Stop-mode selection for the Stop-Time Test Bench:

- **`GET /api/test_bench/stop_mode`** — returns `{ mode, edge_aware_enabled, min_confidence, cooldown_sec }`
- **`POST /api/test_bench/stop_mode`** — body `{ "mode": "cache_aware_offloading" | "edge_aware" }`

Implemented in `backend/app/routes/test_bench_routes.py` and `backend/app/services/vit/edge_aware_estop.py`. Edge-aware stop-label detection and `auto_soft_stop` are executed on the **dashboard client** (`edgeAwareStopLabelEstop.ts`); the backend stores which mode is active.

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Vite development server |
| `npm run dev:backend` | Flask backend |
| `npm run build` | Production frontend bundle |
| `npm run setup` | Install frontend + backend deps |
| `npm run setup:backend` | Backend setup only |
