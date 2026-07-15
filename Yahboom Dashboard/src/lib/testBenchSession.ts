/** Lets the stop-time test bench react when the dashboard sends a manual stop. */

import type { StopBenchMode, StopSource } from './testBenchStorage';
import { benchHasDashboardBottleStop, benchNeedsPiScript } from './testBenchStorage';

let onManualStopDuringSession: (() => void) | null = null;
let pendingStopReason: string | undefined;
let pendingStopIsStopLabel = false;
let pendingStopIsAutoOffPending = false;
let pendingStopConfidence: number | null = null;
let cloudAwareStopLabelActive = false;
let benchSessionActive = false;
let benchStopMode: StopBenchMode = 'cloud_aware';

export function setTestBenchSessionActive(active: boolean) {
  benchSessionActive = active;
}

export function setTestBenchStopMode(mode: StopBenchMode) {
  benchStopMode = mode;
}

/**
 * Cloud-aware bottle stop is always armed, so a manual stop always sends auto_off
 * from the dashboard to disengage explore (the Pi handles its own cache-aware stop).
 */
export function skipAutoOffOnBenchStop(): boolean {
  return false;
}

/** Skip dashboard auto_off after a run only when the Pi cache script ended it. */
export function skipAutoOffAfterBenchRun(stopSource: StopSource | null): boolean {
  return benchStopMode === 'cache_aware_offloading' && stopSource === 'cache_pi';
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

/** Explore disengaged via auto_off — wait for Pi drive-status auto_disabled to end the run. */
export function notifyTestBenchAutoOffPending(reason?: string) {
  if (!benchSessionActive || cloudAwareStopLabelActive) return;
  pendingStopReason = reason ?? pendingStopReason ?? 'Mission test — explore disengaged';
  pendingStopIsStopLabel = false;
  pendingStopIsAutoOffPending = true;
  onManualStopDuringSession?.();
}

/** Bottle / cloud-aware stop-label — freezes the live timer immediately. */
export function notifyTestBenchStopLabelStop(confidence?: number) {
  pendingStopReason = 'Mission test — bottle detected';
  pendingStopIsStopLabel = true;
  pendingStopConfidence = confidence ?? null;
  cloudAwareStopLabelActive = true;
  onManualStopDuringSession?.();
}

/** True after cloud-aware bottle stop until the test-bench session resets. */
export function isCloudAwareStopLabelBenchStop(): boolean {
  return cloudAwareStopLabelActive;
}

export function clearCloudAwareStopLabelBenchStop() {
  cloudAwareStopLabelActive = false;
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

export function takeTestBenchStopIsAutoOffPending(): boolean {
  const isAutoOffPending = pendingStopIsAutoOffPending;
  pendingStopIsAutoOffPending = false;
  return isAutoOffPending;
}

/** Confidence (%) latched for the most recent bottle stop, if any. */
export function takeTestBenchStopConfidence(): number | null {
  const value = pendingStopConfidence;
  pendingStopConfidence = null;
  return value;
}
