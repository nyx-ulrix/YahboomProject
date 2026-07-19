// Custom hooks — backend polling and keyboard input.

import { useEffect, useRef, useState } from 'react';
import { DEFAULT_BROKER_HOST, useMetricsStore, usePickerStore, useSettingsStore } from './store';
import { sendCommand, sendCameraCommand, setEstopState, vecToCameraCommand, type BotCommand } from '../lib/Controls';
import { connectBroker } from '../lib/Connections';
import {
  processVitStatusForStopLabelEstop,
  setCloudAwareStopEnabled,
} from '../lib/yoloStopLabelEstop';
import { useCosineSimilarityCheck } from '../lib/useCosineSimilarityCheck';
import { processYoloStatusForBottleStop } from '../lib/yoloBottleStop';
import { syncStopModeToBackend } from '../lib/testBenchStorage';
import type { LiveGridData, MetricsState } from './types';

// Polls /api/status every 3 s and writes the backend's
// connection state into the shared stores. Because all devices talk to the
// same Flask process, every browser stays in sync automatically: when one
// device clicks Connect the rest will reflect it within ~3 seconds.
type SafetyPayload = {
  raw?: string;
  status?: string;
  distance?: string | number | null;
  estop?: boolean;
  updatedAt?: number | null;
};

function textForStatus(data: SafetyPayload) {
  return `${data.status ?? ''} ${data.raw ?? ''}`.toLowerCase();
}

function hasToken(data: SafetyPayload, token: string) {
  return textForStatus(data).includes(token.toLowerCase());
}

function isHardEstopStatus(data: SafetyPayload) {
  if (hasToken(data, 'estop_grace')) return false;
  return hasToken(data, 'estop=true') || hasToken(data, 'manual_estop_triggered');
}

function isGraceStatus(data: SafetyPayload) {
  return hasToken(data, 'estop_grace');
}

function parseDistance(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return null;
  const match = value.match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseRawJson(raw: unknown): Record<string, unknown> {
  if (typeof raw !== 'string' || raw.trim() === '') return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function toInt(value: unknown): number | null {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

function normalizeGridCells(grid: unknown): number[] | null {
  if (!Array.isArray(grid)) return null;
  const flat = Array.isArray(grid[0])
    ? (grid as unknown[][]).flat()
    : grid;
  return flat.map((cell) => {
    const value = toInt(cell);
    if (value == null) return -1;
    return value < 0 ? -1 : value > 0 ? 1 : 0;
  });
}

function normalizeGridStatus(data: Record<string, unknown>): LiveGridData {
  const rawJson = parseRawJson(data.raw);
  const merged = { ...rawJson, ...data };
  const cells = normalizeGridCells(merged.grid ?? merged.cells ?? merged.data ?? merged.occupancy);
  const size = merged.size as LiveGridData['size'];
  const tupleSize = Array.isArray(size) ? size : null;
  const objectSize = size && typeof size === 'object' && !Array.isArray(size) ? size : null;
  const w =
    toInt(merged.w) ??
    toInt(merged.width) ??
    toInt(objectSize && 'cols' in objectSize ? objectSize.cols : null) ??
    toInt(objectSize && 'width' in objectSize ? objectSize.width : null) ??
    toInt(tupleSize?.[1]) ??
    120;
  const h =
    toInt(merged.h) ??
    toInt(merged.height) ??
    toInt(objectSize && 'rows' in objectSize ? objectSize.rows : null) ??
    toInt(objectSize && 'height' in objectSize ? objectSize.height : null) ??
    toInt(tupleSize?.[0]) ??
    toInt(typeof size === 'number' ? size : null) ??
    120;
  const expected = w * h;
  const grid = cells && expected > 0 && cells.length >= expected ? cells.slice(0, expected) : cells;

  return {
    raw: typeof merged.raw === 'string' ? merged.raw : undefined,
    status: typeof merged.status === 'string' ? merged.status : undefined,
    resolution: parseDistance(merged.resolution),
    size,
    w,
    h,
    grid,
    robot_row: toInt(merged.robot_row),
    robot_col: toInt(merged.robot_col),
    front: parseDistance(merged.front ?? merged.distance),
    left: parseDistance(merged.left),
    right: parseDistance(merged.right),
    auto_mode: typeof merged.auto_mode === 'boolean' ? merged.auto_mode : undefined,
    estop_active: typeof merged.estop_active === 'boolean'
      ? merged.estop_active
      : typeof merged.estop === 'boolean'
        ? merged.estop
        : undefined,
    timestamp: merged.timestamp as number | string | null | undefined,
    updatedAt: toInt(merged.updatedAt),
  };
}

function stopAutoMode() {
  useMetricsStore.setState({
    mode: 'manual',
    autoMode: false,
    autoRunning: false,
    movementVec: null,
  });
}

export function useConnectionSync() {
  // Track the last-seen backend event ID in a ref so it stays independent of
  // local events (which use Date.now() as their ID and would otherwise corrupt
  // the dedup comparison against the backend's small sequential integers).
  const lastBackendEventIdRef = useRef(0);
  const lastVideoUrlRef = useRef<string | null>(null);
  // Throttle auto-connect attempts so a disconnected backend (e.g. after a
  // restart) is retried automatically without hammering the broker every poll.
  const lastAutoConnectAtRef = useRef(0);

  useEffect(() => {
    const sync = async () => {
      try {
        const [statusRes, eventsRes] = await Promise.all([
          fetch('/api/status'),
          fetch('/api/events'),
        ]);

        const status: {
          connected: boolean;
          broker_ip: string;
          estop_active: boolean;
          video_url: string | null;
          stream_running: boolean;
        } = await statusRes.json();

        // Auto-connect (and auto-reconnect) whenever the backend reports it is
        // not connected. Throttled to at most once every 8s so a genuinely
        // down broker isn't hammered, while still recovering automatically
        // after a backend restart with the tab left open.
        if (!status.connected) {
          const now = Date.now();
          if (now - lastAutoConnectAtRef.current >= 8000) {
            lastAutoConnectAtRef.current = now;
            const ip = useSettingsStore.getState().brokerIp || DEFAULT_BROKER_HOST;
            connectBroker(ip);
          }
        }

        useSettingsStore.getState().setBrokerIp(status.broker_ip || useSettingsStore.getState().brokerIp);
        useSettingsStore.getState().setConnected(status.connected);
        const videoUrl = status.video_url ?? '';
        useSettingsStore.getState().setVideoStreamUrl(videoUrl);
        if (videoUrl && videoUrl !== lastVideoUrlRef.current) {
          lastVideoUrlRef.current = videoUrl;
          console.info('[VideoFeed] using stream URL:', videoUrl);
        } else if (!videoUrl && lastVideoUrlRef.current) {
          lastVideoUrlRef.current = null;
          console.info('[VideoFeed] stream URL cleared');
        }
        if ((status.estop_active ?? false) && useMetricsStore.getState().autoRunning) {
          sendCommand('auto_off');
        }

        const ignoreLatchUntil = useMetricsStore.getState().estopIgnoreLatchUntil;
        let estopActive = status.estop_active ?? false;
        if (estopActive && Date.now() < ignoreLatchUntil) {
          estopActive = false;
        }

        useMetricsStore.setState({
          connectionStatus: status.connected ? 'CONNECTED' : 'DISCONNECTED',
          mqttLinkStatus:   status.connected ? 'CONNECTED' : 'DISCONNECTED',
          estopActive,
          streamRunning:    status.stream_running ?? false,
          ...(estopActive
            ? {
                currentCommand: 'STOP' as const,
                missionStatus: 'E-STOP' as const,
                mode: 'manual' as const,
                autoMode: false,
                autoRunning: false,
                movementVec: null,
              }
            : {}),
        });

        const incoming: { id: number; timestamp: string; level: 'info' | 'warning' | 'error'; message: string }[] =
          await eventsRes.json();
        if (incoming.length > 0) {
          // Append-only: only add events with IDs higher than any we already hold.
          // Uses a ref (not the store max) so locally-pushed events with
          // Date.now()-based IDs never inflate lastId and block backend events.
          const lastId = lastBackendEventIdRef.current;
          const fresh  = incoming.filter((e) => e.id > lastId);
          if (fresh.length > 0) {
            lastBackendEventIdRef.current = incoming.reduce((m, e) => (e.id > m ? e.id : m), lastId);
            useMetricsStore.setState((s) => ({
              events: [...s.events, ...fresh],
            }));
          }
        }
      } catch {
        // Backend unreachable — leave existing state untouched.
      }
    };

    sync();                              // immediate fetch on mount
    const id = setInterval(sync, 3000); // then every 3 s
    return () => clearInterval(id);
  }, []);
}

// LiDAR e-stop feedback from backend MQTT subscription.
export function useSafetyStatusPoll() {
  const lastEstopRef = useRef(false);
  const lastRawRef = useRef<string | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        let res = await fetch('/api/safety_topic_status');
        if (!res.ok) res = await fetch('/api/safety_status');
        if (!res.ok) return;
        const data: SafetyPayload = await res.json();

        const raw = data.raw ?? '';
        const statusText = raw || data.status || 'unknown';
        const hardEstop = isHardEstopStatus(data);
        const grace = isGraceStatus(data);

        useMetricsStore.setState({
          safetyStatus: statusText,
          safetyGraceStatus: grace ? statusText : null,
        });

        if (hardEstop) {
          if (useMetricsStore.getState().autoRunning) {
            sendCommand('auto_off');
          }
          stopAutoMode();

          if (!lastEstopRef.current || raw !== lastRawRef.current) {
            useMetricsStore.getState().pushEvent('warning', `LiDAR E-stop triggered — ${raw}`, 'yahboom/safety/status');
          }
        }

        lastEstopRef.current = hardEstop;
        lastRawRef.current = raw;
      } catch {
        // Backend unreachable — leave current safety latch untouched.
      }
    };

    poll();
    const id = setInterval(poll, 500);
    return () => clearInterval(id);
  }, []);
}

export function useGridStatusPoll() {
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/grid_status');
        if (!res.ok) return;
        const data = normalizeGridStatus(await res.json() as Record<string, unknown>);
        if (!alive) return;

        useMetricsStore.setState({
          latestGrid: data,
          frontDistance: data.front,
          leftDistance: data.left,
          rightDistance: data.right,
        });

      } catch {
        // Backend unreachable - keep last grid for display and decision state.
      }
    };

    poll();
    const id = setInterval(poll, 500);
    return () => { alive = false; clearInterval(id); };
  }, []);
}

export function useDriveStatusPoll() {
  const routeMissingRef = useRef(false);

  useEffect(() => {
    const poll = async () => {
      if (routeMissingRef.current) return;
      try {
        const res = await fetch('/api/drive_status');
        if (res.status === 404) {
          routeMissingRef.current = true;
          useMetricsStore.setState({ driveStatus: 'unavailable' });
          return;
        }
        if (!res.ok) return;
        const data = await res.json() as {
          raw?: string;
          status?: string;
          state?: string;
          robotTimestamp?: number | null;
          auto_mode?: boolean | null;
        };
        const patch: Partial<Pick<MetricsState, 'driveStatus' | 'autoMode' | 'autoRunning'>> = {
          driveStatus: data.status || data.state || data.raw || 'unknown',
        };
        // Pi reported auto off — clear client latch so manual drive works again.
        if (data.auto_mode === false) {
          patch.autoMode = false;
          patch.autoRunning = false;
        }
        useMetricsStore.setState(patch);
      } catch {
        useMetricsStore.setState({ driveStatus: 'unknown' });
      }
    };

    poll();
    const id = setInterval(poll, 1000);
    return () => clearInterval(id);
  }, []);
}

const MOVEMENT_KEYS = new Set(['w', 's', 'a', 'd']);

function computeMovementCommand(held: Set<string>): BotCommand | null {
  const fwd = held.has('w') && !held.has('s');
  const bck = held.has('s') && !held.has('w');
  const lft = held.has('a') && !held.has('d');
  const rgt = held.has('d') && !held.has('a');

  if (fwd && lft) return 'fwdleft';
  if (fwd && rgt) return 'fwdright';
  if (bck && lft) return 'bckleft';
  if (bck && rgt) return 'bckright';
  if (fwd)        return 'fwd';
  if (bck)        return 'bck';
  if (lft)        return 'left';
  if (rgt)        return 'right';
  return null;
}

export function useKeyboardMovement() {
  const heldKeys = useRef(new Set<string>());
  const lastCmd  = useRef<BotCommand | null>(null);

  useEffect(() => {
    const dispatch = () => {
      const cmd = computeMovementCommand(heldKeys.current);
      if (cmd === null) {
        if (lastCmd.current !== null) {
          lastCmd.current = null;
          sendCommand('stop', 'release');
        }
      } else if (cmd !== lastCmd.current) {
        if (useMetricsStore.getState().estopActive) return;
        lastCmd.current = cmd;
        sendCommand(cmd);
      }
    };

    const onDown = (e: KeyboardEvent) => {
      if (isInputFocused()) return;

      if (e.code === 'Space') {
        e.preventDefault();
        heldKeys.current.clear();
        lastCmd.current = null;
        sendCommand('stop', 'manual');
        return;
      }

      const key = e.key.toLowerCase();
      if (!MOVEMENT_KEYS.has(key)) return;
      if (heldKeys.current.has(key)) return; // ignore OS key-repeat
      heldKeys.current.add(key);
      dispatch();
    };

    const onUp = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      if (!MOVEMENT_KEYS.has(key)) return;
      heldKeys.current.delete(key);
      dispatch();
    };

    window.addEventListener('keydown', onDown);
    window.addEventListener('keyup', onUp);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('keyup', onUp);
    };
  }, []);
}

export function useKeyboardCamera(onChange?: (v: { pan: number; tilt: number }) => void) {
  const keysRef      = useRef<Set<string>>(new Set());
  const lastCmdRef   = useRef<ReturnType<typeof vecToCameraCommand>>(null);
  const onChangeRef  = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    const compute = () => {
      const k = keysRef.current;
      let pan = 0;
      let tilt = 0;
      if (k.has('ArrowUp'))    tilt += 1;
      if (k.has('ArrowDown'))  tilt -= 1;
      if (k.has('ArrowLeft'))  pan  -= 1;
      if (k.has('ArrowRight')) pan  += 1;
      useMetricsStore.setState({ cameraKeyboardVec: { x: pan, y: tilt } });
      onChangeRef.current?.({ pan, tilt });

      const cmd = vecToCameraCommand(pan, tilt);
      if (cmd !== null && cmd !== lastCmdRef.current) {
        lastCmdRef.current = cmd;
        sendCameraCommand(cmd);
      } else if (cmd === null) {
        if (lastCmdRef.current !== null) sendCameraCommand('cstop');
        lastCmdRef.current = null;
      }
    };
    const onDown = (e: KeyboardEvent) => {
      if (isInputFocused()) return;
      if (!e.key.startsWith('Arrow')) return;
      e.preventDefault();
      if (keysRef.current.has(e.key)) return;
      keysRef.current.add(e.key);
      compute();
    };
    const onUp = (e: KeyboardEvent) => {
      if (!e.key.startsWith('Arrow')) return;
      keysRef.current.delete(e.key);
      compute();
    };
    window.addEventListener('keydown', onDown);
    window.addEventListener('keyup', onUp);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('keyup', onUp);
    };
  }, []);
}

/**
 * Cache Aware cosine-similarity bottle stop. Matching runs in the browser only when
 * Cache Aware is selected (not YOLO). Posts match results to /api/vit/status, which
 * this hook polls to fire the stop while armed.
 */
export function useCloudAwareStopLabelEstop() {
  useCosineSimilarityCheck();

  useEffect(() => {
    let alive = true;

    const poll = async () => {
      try {
        const vitRes = await fetch('/api/vit/status', { cache: 'no-store' });
        if (!vitRes.ok || !alive) return;
        const vit = await vitRes.json();
        processVitStatusForStopLabelEstop(vit);
      } catch {
        /* backend unreachable */
      }
    };

    poll();
    const id = setInterval(poll, 500);
    return () => { alive = false; clearInterval(id); };
  }, []);
}

/** Keep backend YOLO/cache mode aligned with local toggles after refresh. */
export function useStopModeBackendSync() {
  useEffect(() => {
    void syncStopModeToBackend();
  }, []);
}

/** YOLO bottle stop while cloud_aware (YOLO) test-bench mode is active. */
export function useYoloBottleStop() {
  useEffect(() => {
    let alive = true;
    let nextPoll: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      let failed = false;
      try {
        const res = await fetch('/api/yolo/status', { cache: 'no-store' });
        if (!res.ok) {
          failed = true;
          return;
        }
        if (!alive) return;
        const status = await res.json();
        processYoloStatusForBottleStop(status);
      } catch {
        /* backend unreachable */
        failed = true;
      } finally {
        if (alive) {
          // Poll again as soon as the previous request completes. This gives
          // minimum stop latency without creating overlapping requests.
          nextPoll = setTimeout(poll, failed ? 250 : 0);
        }
      }
    };

    void poll();
    return () => {
      alive = false;
      if (nextPoll != null) clearTimeout(nextPoll);
    };
  }, []);
}

export function useGlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isInputFocused()) return;
      if (e.key === 'x' || e.key === 'X') {
        e.preventDefault();
        void setEstopState(true);
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        sendCameraCommand('crst');
      } else if (e.key === 'p' || e.key === 'P') {
        e.preventDefault();
        usePickerStore.getState().toggle();
      } else if (e.key === 'Escape') {
        usePickerStore.getState().setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}

function isInputFocused() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || (el as HTMLElement).isContentEditable;
}
