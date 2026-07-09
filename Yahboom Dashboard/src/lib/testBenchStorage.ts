/** Browser-persisted Stop-Time Test Bench session data. */

const STORAGE_KEY = 'yahboom_test_bench_v1';

export type StopBenchMode = 'cache_aware_offloading' | 'edge_aware';

export type StopSource = 'cache_pi' | 'edge_dashboard' | 'manual';

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
  return value === 'cache_aware_offloading' || value === 'edge_aware';
}

/** Legacy 'hybrid' runs are recorded as cache_aware_offloading (edge stays armed). */
function normalizeStopMode(value: unknown): StopBenchMode | undefined {
  if (value === 'hybrid') return 'cache_aware_offloading';
  return isStopBenchMode(value) ? value : undefined;
}

function isStopSource(value: unknown): value is StopSource {
  return value === 'cache_pi' || value === 'edge_dashboard' || value === 'manual';
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
              const mode = normalizeStopMode((run as Record<string, unknown>).stopMode);
              if (mode) return { ...(run as Record<string, unknown>), stopMode: mode };
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
  edge_dashboard: 'Edge Stop',
  manual: 'Manual stop',
};

export const STOP_MODE_LABELS: Record<StopBenchMode, string> = {
  cache_aware_offloading: 'Cache Aware',
  edge_aware: 'Edge Stop',
};

export const STOP_BENCH_MODES: StopBenchMode[] = [
  'cache_aware_offloading',
  'edge_aware',
];

export const DEFAULT_STOP_BENCH_MODE: StopBenchMode = 'edge_aware';

// Edge-aware bottle stop is always on (no toggle); only cache-aware is user-controlled.
export const DEFAULT_STOP_TOGGLES: StopModeToggles = { cacheOn: false, edgeOn: true };

const STOP_MODE_PREF_KEY = 'yahboom_stop_bench_mode';
const STOP_TOGGLES_KEY = 'yahboom_stop_toggles';

export function loadStopToggles(): StopModeToggles {
  // Cache Aware Offloading always starts OFF (never restored from a prior session);
  // it must be re-enabled explicitly so the car receives a fresh Cae_ON + Cae_Ready.
  // edgeOn is always true — edge-aware bottle stop has no toggle.
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
    return isStopBenchMode(raw) ? raw : DEFAULT_STOP_BENCH_MODE;
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

export function benchNeedsPiScript(mode: StopBenchMode): boolean {
  return mode === 'cache_aware_offloading';
}

// Edge-aware dashboard bottle stop is always armed, in every mode.
export function benchHasDashboardBottleStop(_mode: StopBenchMode): boolean {
  return true;
}

export type StopModeToggles = { cacheOn: boolean; edgeOn: boolean };

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
    edgeOn: benchHasDashboardBottleStop(mode),
  };
}

/**
 * Map cache/edge toggles to backend stop mode. Edge-aware bottle stop is always
 * armed, so cache-on -> cache_aware_offloading (Pi script + edge), else edge_aware.
 */
export function togglesToStopMode(cacheOn: boolean, _edgeOn: boolean): StopBenchMode {
  return cacheOn ? 'cache_aware_offloading' : 'edge_aware';
}
