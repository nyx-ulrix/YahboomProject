/** Browser-persisted Stop-Time Test Bench session data. */

import { useLayoutStore } from '../app/store';

const STORAGE_KEY = 'yahboom_test_bench_v1';

export type StopBenchMode = 'cache_aware_offloading' | 'cloud_aware';

export type StopSource = 'cache_pi' | 'edge_dashboard' | 'yolo_dashboard' | 'manual';

export type PersistedStopTestRun = {
  id: number;
  run: number;
  commandSentAt: number;
  startedAt: number;
  stoppedAt: number;
  durationMs: number;
  commandToMoveMs: number;
  stoppingDistance: string;
  networkType: string;
  stopMode: StopBenchMode;
  stopSource?: StopSource;
  stopConfidencePercent?: number | null;
};

export type TestBenchCache = {
  runs: PersistedStopTestRun[];
  networkType: string | null;
  userPickedNetwork: boolean;
};

const EMPTY_CACHE: TestBenchCache = {
  runs: [],
  networkType: null,
  userPickedNetwork: false,
};

function isStopBenchMode(value: unknown): value is StopBenchMode {
  return value === 'cache_aware_offloading' || value === 'cloud_aware';
}

/** Legacy persisted values from the edge_* naming era. */
function normalizeStopBenchMode(value: unknown): StopBenchMode | undefined {
  if (value === 'edge_aware') return 'cloud_aware';
  return isStopBenchMode(value) ? value : undefined;
}

/** Legacy 'hybrid' runs are recorded as cache_aware_offloading (cloud stays armed). */
function normalizeStopMode(value: unknown): StopBenchMode | undefined {
  if (value === 'hybrid') return 'cache_aware_offloading';
  return normalizeStopBenchMode(value);
}

function normalizeStopSource(value: unknown): StopSource | undefined {
  if (value === 'cloud_dashboard') return 'edge_dashboard';
  if (
    value === 'cache_pi'
    || value === 'edge_dashboard'
    || value === 'yolo_dashboard'
    || value === 'manual'
  ) return value;
  return undefined;
}

function isStopSource(value: unknown): value is StopSource {
  return normalizeStopSource(value) !== undefined;
}

function isPersistedRun(value: unknown): value is PersistedStopTestRun {
  if (!value || typeof value !== 'object') return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.id === 'number'
    && typeof r.run === 'number'
    && typeof r.commandSentAt === 'number'
    && typeof r.startedAt === 'number'
    && typeof r.stoppedAt === 'number'
    && typeof r.durationMs === 'number'
    && typeof r.commandToMoveMs === 'number'
    && typeof r.stoppingDistance === 'string'
    && typeof r.networkType === 'string'
    && isStopBenchMode(r.stopMode)
    && (r.stopSource === undefined || isStopSource(r.stopSource))
    && (r.stopConfidencePercent === undefined || r.stopConfidencePercent === null || typeof r.stopConfidencePercent === 'number')
  );
}

function parseCache(raw: string | null): TestBenchCache {
  if (!raw) return { ...EMPTY_CACHE };
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const runs = Array.isArray(data.runs)
      ? data.runs
          .map((run) => {
            if (run && typeof run === 'object') {
              const record = run as Record<string, unknown>;
              const mode = normalizeStopMode(record.stopMode);
              const source = normalizeStopSource(record.stopSource);
              if (mode) {
                return {
                  ...record,
                  stopMode: mode,
                  ...(source ? { stopSource: source } : {}),
                };
              }
            }
            return run;
          })
          .filter(isPersistedRun)
      : [];
    return {
      runs,
      networkType: typeof data.networkType === 'string' ? data.networkType : null,
      userPickedNetwork: data.userPickedNetwork === true,
    };
  } catch {
    return { ...EMPTY_CACHE };
  }
}

export function loadTestBenchCache(): TestBenchCache {
  try {
    return parseCache(localStorage.getItem(STORAGE_KEY));
  } catch {
    return { ...EMPTY_CACHE };
  }
}

export function saveTestBenchCache(patch: Partial<TestBenchCache>): void {
  try {
    const current = loadTestBenchCache();
    const next: TestBenchCache = {
      runs: patch.runs ?? current.runs,
      networkType: patch.networkType !== undefined ? patch.networkType : current.networkType,
      userPickedNetwork: patch.userPickedNetwork ?? current.userPickedNetwork,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode / quota — ignore */
  }
}

export function clearTestBenchCache(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export const STOP_SOURCE_LABELS: Record<StopSource, string> = {
  cache_pi: 'Cache Stop',
  edge_dashboard: 'Edge Stop',
  yolo_dashboard: 'YOLO Stop',
  manual: 'Manual stop',
};

export const STOP_MODE_LABELS: Record<StopBenchMode, string> = {
  cache_aware_offloading: 'Cache Aware',
  cloud_aware: 'YOLO',
};

export const STOP_BENCH_MODES: StopBenchMode[] = [
  'cache_aware_offloading',
  'cloud_aware',
];

export const DEFAULT_STOP_BENCH_MODE: StopBenchMode = 'cloud_aware';

export const DEFAULT_STOP_TARGET_CATEGORY = 'target_bottle';

export const DEFAULT_STOP_SIMILARITY_THRESHOLD_PCT = 70;

const STOP_TARGET_CATEGORY_KEY = 'yahboom_stop_target_category';
const STOP_SIMILARITY_THRESHOLD_KEY = 'yahboom_stop_similarity_threshold_pct';

// Cloud-aware bottle stop is always on (no toggle); only cache-aware is user-controlled.
export const DEFAULT_STOP_TOGGLES: StopModeToggles = { cacheOn: false, cloudOn: true };

const STOP_MODE_PREF_KEY = 'yahboom_stop_bench_mode';
const STOP_TOGGLES_KEY = 'yahboom_stop_toggles';

export function loadStopToggles(): StopModeToggles {
  try {
    const raw = localStorage.getItem(STOP_TOGGLES_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<StopModeToggles>;
      if (typeof parsed.cacheOn === 'boolean') {
        return { cacheOn: parsed.cacheOn, cloudOn: true };
      }
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULT_STOP_TOGGLES };
}

export function saveStopToggles(toggles: StopModeToggles): void {
  try {
    localStorage.setItem(STOP_TOGGLES_KEY, JSON.stringify(toggles));
  } catch {
    /* ignore */
  }
}

export function loadStopModePreference(): StopBenchMode {
  try {
    const raw = localStorage.getItem(STOP_MODE_PREF_KEY);
    return normalizeStopBenchMode(raw) ?? DEFAULT_STOP_BENCH_MODE;
  } catch {
    return DEFAULT_STOP_BENCH_MODE;
  }
}

export function saveStopModePreference(mode: StopBenchMode): void {
  try {
    localStorage.setItem(STOP_MODE_PREF_KEY, mode);
  } catch {
    /* private mode / quota — ignore */
  }
}

const STOP_TARGET_RE = /^[a-z0-9_-]{1,48}$/;

export function loadStopTargetCategory(): string {
  try {
    const raw = localStorage.getItem(STOP_TARGET_CATEGORY_KEY);
    if (raw && STOP_TARGET_RE.test(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_STOP_TARGET_CATEGORY;
}

export function saveStopTargetCategory(category: string): void {
  try {
    localStorage.setItem(STOP_TARGET_CATEGORY_KEY, category);
  } catch {
    /* ignore */
  }
}

export function loadStopSimilarityThresholdPct(): number {
  try {
    const raw = localStorage.getItem(STOP_SIMILARITY_THRESHOLD_KEY);
    const n = Number(raw);
    if (Number.isFinite(n) && n >= 1 && n <= 100) return Math.round(n);
  } catch {
    /* ignore */
  }
  return DEFAULT_STOP_SIMILARITY_THRESHOLD_PCT;
}

export function saveStopSimilarityThresholdPct(pct: number): void {
  try {
    localStorage.setItem(STOP_SIMILARITY_THRESHOLD_KEY, String(Math.round(pct)));
  } catch {
    /* ignore */
  }
}

export function benchNeedsPiScript(mode: StopBenchMode): boolean {
  return mode === 'cache_aware_offloading';
}

// Dashboard bottle stop: Cache Aware (cosine) or YOLO (object detection).
export function benchHasDashboardBottleStop(mode: StopBenchMode): boolean {
  return mode === 'cache_aware_offloading' || mode === 'cloud_aware';
}

/** YOLO bottle stop runs in cloud_aware (YOLO) test-bench mode. */
export function benchHasYoloBottleStop(mode: StopBenchMode): boolean {
  return mode === 'cloud_aware';
}

/** Client-side cosine similarity matching (Cache Aware cache-miss path only). */
export function benchUsesCosineSimilarity(mode: StopBenchMode): boolean {
  return mode === 'cache_aware_offloading';
}

export type StopModeToggles = { cacheOn: boolean; cloudOn: boolean };

/** True when the Pi reports cache-aware is running and ready (MQTT or SSH probe). */
export function piReportsCacheAwareOn(data: {
  cache_aware_mqtt_ready?: boolean;
  cache_script_running?: boolean;
  cache_script_detection_ready?: boolean;
}): boolean {
  if (data.cache_aware_mqtt_ready === true) return true;
  return data.cache_script_running === true && data.cache_script_detection_ready === true;
}

export function stopModeToToggles(mode: StopBenchMode): StopModeToggles {
  return {
    cacheOn: benchNeedsPiScript(mode),
    cloudOn: benchHasDashboardBottleStop(mode),
  };
}

/**
 * Map cache/cloud toggles to backend stop mode. Cache-on enables cosine similarity
 * on cache misses; otherwise YOLO (cloud_aware) — no client-side cosine matching.
 */
export function togglesToStopMode(cacheOn: boolean, _cloudOn: boolean): StopBenchMode {
  return cacheOn ? 'cache_aware_offloading' : 'cloud_aware';
}

/** Switch dashboard layout to Stop Test CAO or Stop Test YOLO for the active bench mode. */
export function applyStopBenchLayoutForMode(mode: StopBenchMode): void {
  const templateId = mode === 'cache_aware_offloading' ? 'stop_test_cao' : 'stop_test_yolo';
  useLayoutStore.getState().applyTemplate(templateId);
}

/** GET /api/test_bench/stop_mode payload — backend is the cross-browser source of truth. */
export type StopModeApiResponse = {
  mode?: StopBenchMode;
  cache_script_running?: boolean;
  cache_script_detection_ready?: boolean;
  cache_aware_mqtt_ready?: boolean;
  cloud_aware_enabled?: boolean;
  message?: string;
  status?: string;
};

export const STOP_MODE_SYNC_EVENT = 'yahboom-stop-mode-sync';

let cachedClientStopMode: StopBenchMode | null = null;
let stopModeSyncLockUntil = 0;
const stopModeSyncOriginId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
const stopModeBroadcast = typeof BroadcastChannel !== 'undefined'
  ? new BroadcastChannel('yahboom-stop-mode')
  : null;

/** Brief lock while this tab pushes a mode change so polling does not revert it. */
export function lockStopModeSync(ms = 5000): void {
  stopModeSyncLockUntil = Date.now() + ms;
}

export function isStopModeSyncLocked(): boolean {
  return Date.now() < stopModeSyncLockUntil;
}

export function getStopModeSyncOrigin(): string {
  return stopModeSyncOriginId;
}

export function getClientStopMode(): StopBenchMode {
  if (cachedClientStopMode) return cachedClientStopMode;
  const toggles = loadStopToggles();
  return togglesToStopMode(toggles.cacheOn, true);
}

/** Persist mode locally and return matching toggles (does not POST to backend). */
export function setClientStopMode(mode: StopBenchMode): StopModeToggles {
  cachedClientStopMode = mode;
  const toggles = stopModeToToggles(mode);
  saveStopToggles(toggles);
  saveStopModePreference(mode);
  return toggles;
}

export function stopModeFromApi(data: StopModeApiResponse | null | undefined): StopBenchMode | null {
  if (!data?.mode) return null;
  return normalizeStopBenchMode(data.mode);
}

export async function fetchBackendStopMode(): Promise<StopModeApiResponse | null> {
  try {
    const res = await fetch('/api/test_bench/stop_mode', { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json() as StopModeApiResponse;
  } catch {
    return null;
  }
}

export type StopModeSyncDetail = {
  mode: StopBenchMode;
  data: StopModeApiResponse;
  origin: string;
};

export function broadcastStopModeSync(detail: StopModeSyncDetail): void {
  stopModeBroadcast?.postMessage(detail);
  window.dispatchEvent(new CustomEvent(STOP_MODE_SYNC_EVENT, { detail }));
}

export function subscribeStopModeSync(
  handler: (detail: StopModeSyncDetail) => void,
): () => void {
  const onWindow = (event: Event) => {
    const detail = (event as CustomEvent<StopModeSyncDetail>).detail;
    if (detail?.mode) handler(detail);
  };
  const onChannel = (event: MessageEvent<StopModeSyncDetail>) => {
    const detail = event.data;
    if (detail?.mode && detail.origin !== stopModeSyncOriginId) handler(detail);
  };
  window.addEventListener(STOP_MODE_SYNC_EVENT, onWindow);
  stopModeBroadcast?.addEventListener('message', onChannel);
  return () => {
    window.removeEventListener(STOP_MODE_SYNC_EVENT, onWindow);
    stopModeBroadcast?.removeEventListener('message', onChannel);
  };
}

/** Pull backend mode; update local mirror + layout when another dashboard changed it. */
export async function pullAndReconcileStopMode(): Promise<StopModeApiResponse | null> {
  if (isStopModeSyncLocked()) return null;
  const data = await fetchBackendStopMode();
  const mode = stopModeFromApi(data);
  if (!mode || !data) return null;
  if (mode === getClientStopMode()) return data;
  setClientStopMode(mode);
  applyStopBenchLayoutForMode(mode);
  broadcastStopModeSync({ mode, data, origin: stopModeSyncOriginId });
  return data;
}

/** On dashboard load: adopt backend mode (do not push local defaults over other browsers). */
export async function syncStopModeToBackend(): Promise<void> {
  await pullAndReconcileStopMode();
}
