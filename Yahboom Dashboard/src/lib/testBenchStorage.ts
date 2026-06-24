/** Browser-persisted Stop-Time Test Bench session data. */

const STORAGE_KEY = 'yahboom_test_bench_v1';

export type StopBenchMode = 'cache_aware_offloading' | 'edge_aware';

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
