/** Browser-persisted Stop-Time Test Bench session data. */

const STORAGE_KEY = 'yahboom_test_bench_v1';

export type StopBenchMode = 'cache_aware_offloading' | 'hybrid' | 'edge_aware';

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
  return value === 'cache_aware_offloading' || value === 'hybrid' || value === 'edge_aware';
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
  );
}

function parseCache(raw: string | null): TestBenchCache {
  if (!raw) return { ...EMPTY_CACHE };
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const runs = Array.isArray(data.runs)
      ? data.runs.filter(isPersistedRun)
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
  cache_pi: 'Pi script · bottle',
  edge_dashboard: 'Dashboard VIT · bottle',
  manual: 'Manual stop',
};

export const STOP_MODE_LABELS: Record<StopBenchMode, string> = {
  cache_aware_offloading: 'Cache aware',
  hybrid: 'Hybrid',
  edge_aware: 'Edge aware',
};

export const STOP_BENCH_MODES: StopBenchMode[] = [
  'cache_aware_offloading',
  'hybrid',
  'edge_aware',
];

export const DEFAULT_STOP_BENCH_MODE: StopBenchMode = 'edge_aware';

const STOP_MODE_PREF_KEY = 'yahboom_stop_bench_mode';

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
  return mode === 'cache_aware_offloading' || mode === 'hybrid';
}

export function benchHasDashboardBottleStop(mode: StopBenchMode): boolean {
  return mode === 'edge_aware' || mode === 'hybrid';
}
