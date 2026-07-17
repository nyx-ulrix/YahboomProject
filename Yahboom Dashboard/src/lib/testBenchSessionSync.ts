/** Cross-browser sync for the Mission Test Bench Start button / active session. */

import type { StopBenchMode } from './testBenchStorage';
import { getStopModeSyncOrigin } from './testBenchStorage';

export type TestBenchSessionApi = {
  active: boolean;
  origin: string | null;
  command_sent_at_ms: number | null;
  active_start_ms: number | null;
  frozen_elapsed_ms: number | null;
  session_start_wall_ms: number | null;
  stop_mode: StopBenchMode | null;
  completed_run?: CompletedRunApi | null;
  completed_at?: number | null;
  recorded?: boolean;
};

export type CompletedRunApi = {
  run: number;
  commandSentAt: number;
  startedAt: number;
  stoppedAt: number;
  durationMs: number;
  commandToMoveMs: number;
  stoppingDistance: string;
  networkType: string;
  stopMode: StopBenchMode;
  stopSource?: string;
  stopConfidencePercent?: number | null;
  completed_by?: string;
};

export const TEST_BENCH_SESSION_SYNC_EVENT = 'yahboom-test-bench-session-sync';

let cachedSession: TestBenchSessionApi | null = null;
let sessionSyncLockUntil = 0;
const sessionBroadcast = typeof BroadcastChannel !== 'undefined'
  ? new BroadcastChannel('yahboom-test-bench-session')
  : null;

export function getSessionSyncOrigin(): string {
  return getStopModeSyncOrigin();
}

export function lockSessionSync(ms = 5000): void {
  sessionSyncLockUntil = Date.now() + ms;
}

export function isSessionSyncLocked(): boolean {
  return Date.now() < sessionSyncLockUntil;
}

export function getCachedTestBenchSession(): TestBenchSessionApi | null {
  return cachedSession;
}

function sessionKey(data: TestBenchSessionApi): string {
  if (!data.active) return 'inactive';
  return [
    data.origin ?? '',
    data.command_sent_at_ms ?? '',
    data.active_start_ms ?? '',
    data.frozen_elapsed_ms ?? '',
    data.stop_mode ?? '',
  ].join('|');
}

export async function fetchTestBenchSession(): Promise<TestBenchSessionApi | null> {
  try {
    const res = await fetch('/api/test_bench/session', { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json() as TestBenchSessionApi;
  } catch {
    return null;
  }
}

export async function startTestBenchSession(payload: {
  origin: string;
  command_sent_at_ms: number;
  stop_mode: StopBenchMode;
  session_start_wall_ms: number;
}): Promise<{ ok: boolean; status: number; data: TestBenchSessionApi | null }> {
  try {
    const res = await fetch('/api/test_bench/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = res.ok || res.status === 409
      ? await res.json() as TestBenchSessionApi
      : null;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

export async function patchTestBenchSession(
  partial: { active_start_ms?: number; frozen_elapsed_ms?: number },
): Promise<void> {
  try {
    await fetch('/api/test_bench/session', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(partial),
    });
  } catch {
    /* backend unreachable */
  }
}

export async function clearTestBenchSession(): Promise<void> {
  try {
    await fetch('/api/test_bench/session', { method: 'DELETE' });
  } catch {
    /* backend unreachable */
  }
}

export async function completeTestBenchSession(
  run: CompletedRunApi,
  origin: string,
): Promise<{ recorded: boolean; completed_run?: CompletedRunApi | null } | null> {
  try {
    const res = await fetch('/api/test_bench/session/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ origin, run }),
    });
    if (!res.ok) return null;
    return await res.json() as { recorded: boolean; completed_run?: CompletedRunApi | null };
  } catch {
    return null;
  }
}

export type TestBenchSessionSyncDetail = {
  data: TestBenchSessionApi;
  origin: string;
};

export function broadcastTestBenchSessionSync(detail: TestBenchSessionSyncDetail): void {
  sessionBroadcast?.postMessage(detail);
  window.dispatchEvent(new CustomEvent(TEST_BENCH_SESSION_SYNC_EVENT, { detail }));
}

export function subscribeTestBenchSessionSync(
  handler: (detail: TestBenchSessionSyncDetail) => void,
): () => void {
  const onWindow = (event: Event) => {
    const detail = (event as CustomEvent<TestBenchSessionSyncDetail>).detail;
    if (detail?.data) handler(detail);
  };
  const onChannel = (event: MessageEvent<TestBenchSessionSyncDetail>) => {
    const detail = event.data;
    if (detail?.data && detail.origin !== getSessionSyncOrigin()) handler(detail);
  };
  window.addEventListener(TEST_BENCH_SESSION_SYNC_EVENT, onWindow);
  sessionBroadcast?.addEventListener('message', onChannel);
  return () => {
    window.removeEventListener(TEST_BENCH_SESSION_SYNC_EVENT, onWindow);
    sessionBroadcast?.removeEventListener('message', onChannel);
  };
}

/** Pull backend session; broadcast when another dashboard started/updated/ended a run. */
export async function pullAndReconcileTestBenchSession(): Promise<TestBenchSessionApi | null> {
  if (isSessionSyncLocked()) return cachedSession;
  const data = await fetchTestBenchSession();
  if (!data) return null;

  const prevKey = cachedSession ? sessionKey(cachedSession) : null;
  const nextKey = sessionKey(data);
  cachedSession = data;

  if (prevKey !== nextKey || data.completed_run) {
    broadcastTestBenchSessionSync({ data, origin: getSessionSyncOrigin() });
  }
  return data;
}
