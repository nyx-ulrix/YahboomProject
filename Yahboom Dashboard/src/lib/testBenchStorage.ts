/** Browser-persisted Stop-Time Test Bench session data. */

const STORAGE_KEY = 'yahboom_test_bench_v1';

export type StopBenchMode = 'cache_aware_offloading' | 'cloud_aware';

export type StopSource = 'cache_pi' | 'cloud_dashboard' | 'manual';

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
  if (value === 'edge_dashboard') return 'cloud_dashboard';
  if (value === 'cache_pi' || value === 'cloud_dashboard' || value === 'manual') return value;
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
  cache_pi: 'Cache stop',
  cloud_dashboard: 'Cloud Stop',
  manual: 'Manual stop',
};

export const STOP_MODE_LABELS: Record<StopBenchMode, string> = {
  cache_aware_offloading: 'Cache Aware',
  cloud_aware: 'Cloud Stop',
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
  // Cache Aware Offloading always starts OFF (never restored from a prior session);
  // it must be re-enabled explicitly so the car receives a fresh Cae_ON + Cae_Ready.
  // cloudOn is always true — cloud-aware bottle stop has no toggle.
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

// Cloud-aware dashboard bottle stop is always armed, in every mode.
export function benchHasDashboardBottleStop(_mode: StopBenchMode): boolean {
  return true;
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
 * Map cache/cloud toggles to backend stop mode. Cloud-aware bottle stop is always
 * armed, so cache-on -> cache_aware_offloading (Pi script + cloud), else cloud_aware.
 */
export function togglesToStopMode(cacheOn: boolean, _cloudOn: boolean): StopBenchMode {
  return cacheOn ? 'cache_aware_offloading' : 'cloud_aware';
}
