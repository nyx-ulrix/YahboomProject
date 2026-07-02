/** Lets the stop-time test bench react when the dashboard sends a manual stop. */

import type { StopBenchMode, StopSource } from './testBenchStorage';
import { benchHasDashboardBottleStop, benchNeedsPiScript } from './testBenchStorage';

let onManualStopDuringSession: (() => void) | null = null;
let pendingStopReason: string | undefined;
let pendingStopIsStopLabel = false;
let pendingStopConfidence: number | null = null;
let edgeAwareStopLabelActive = false;
let benchSessionActive = false;
let benchStopMode: StopBenchMode = 'edge_aware';

export function setTestBenchSessionActive(active: boolean) {
  benchSessionActive = active;
}

export function setTestBenchStopMode(mode: StopBenchMode) {
  benchStopMode = mode;
}

/** Pure cache mode: Pi stop only — do not send auto_off from dashboard. */
export function skipAutoOffOnBenchStop(): boolean {
  return benchSessionActive && benchStopMode === 'cache_aware_offloading';
}

/** Skip dashboard auto_off after run when Pi cache script ended the run (cache or hybrid). */
export function skipAutoOffAfterBenchRun(stopSource: StopSource | null): boolean {
  if (benchStopMode === 'cache_aware_offloading') return true;
  if (benchStopMode === 'hybrid' && stopSource === 'cache_pi') return true;
  return false;
}

export function benchModeNeedsPiScript(): boolean {
  return benchNeedsPiScript(benchStopMode);
}

export function benchModeHasDashboardBottleStop(): boolean {
  return benchHasDashboardBottleStop(benchStopMode);
}

export function setTestBenchManualStopHook(fn: (() => void) | null) {
  onManualStopDuringSession = fn;
}

export function notifyTestBenchManualStop(reason?: string) {
  pendingStopReason = reason;
  pendingStopIsStopLabel = false;
  onManualStopDuringSession?.();
}

/** Bottle / edge-aware stop-label — freezes the live timer immediately. */
export function notifyTestBenchStopLabelStop(confidence?: number) {
  pendingStopReason = 'Stop-time test — stop label detected';
  pendingStopIsStopLabel = true;
  pendingStopConfidence = confidence ?? null;
  edgeAwareStopLabelActive = true;
  onManualStopDuringSession?.();
}

/** True after edge-aware bottle stop until the test-bench session resets. */
export function isEdgeAwareStopLabelBenchStop(): boolean {
  return edgeAwareStopLabelActive;
}

export function clearEdgeAwareStopLabelBenchStop() {
  edgeAwareStopLabelActive = false;
}

/** Reason passed to the most recent notify (consumed by the hook). */
export function takeTestBenchStopReason(): string | undefined {
  const reason = pendingStopReason;
  pendingStopReason = undefined;
  return reason;
}

export function takeTestBenchStopIsStopLabel(): boolean {
  const isStopLabel = pendingStopIsStopLabel;
  pendingStopIsStopLabel = false;
  return isStopLabel;
}

/** Confidence (%) latched for the most recent bottle stop, if any. */
export function takeTestBenchStopConfidence(): number | null {
  const value = pendingStopConfidence;
  pendingStopConfidence = null;
  return value;
}
