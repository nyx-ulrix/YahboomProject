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

## Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Vite development server |
| `npm run dev:backend` | Flask backend |
| `npm run build` | Production frontend bundle |
| `npm run setup` | Install frontend + backend deps |
| `npm run setup:backend` | Backend setup only |
