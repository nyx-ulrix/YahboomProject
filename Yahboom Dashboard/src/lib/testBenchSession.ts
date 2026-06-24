/** Lets the stop-time test bench react when the dashboard sends a manual stop. */

let onManualStopDuringSession: (() => void) | null = null;
let pendingStopReason: string | undefined;
let pendingStopIsStopLabel = false;

export function setTestBenchManualStopHook(fn: (() => void) | null) {
  onManualStopDuringSession = fn;
}

export function notifyTestBenchManualStop(reason?: string) {
  pendingStopReason = reason;
  pendingStopIsStopLabel = false;
  onManualStopDuringSession?.();
}

/** Bottle / edge-aware stop-label — freezes the live timer immediately. */
export function notifyTestBenchStopLabelStop() {
  pendingStopReason = 'Stop-time test — stop label detected';
  pendingStopIsStopLabel = true;
  onManualStopDuringSession?.();
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
