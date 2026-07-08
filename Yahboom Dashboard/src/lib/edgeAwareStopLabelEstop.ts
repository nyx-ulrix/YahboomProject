import { sendDashboardBottleStop } from './Controls';
import { useMetricsStore } from '../app/store';
import { benchModeHasDashboardBottleStop } from './testBenchSession';
import { notifyTestBenchStopLabelStop } from './testBenchSession';

/** VIT label from labels.json — edge-aware stop triggers on this class only. */
export const EDGE_AWARE_STOP_LABEL = 'bottle';

/** Minimum bottle confidence (%) before edge-aware dashboard stop fires. */
const EDGE_AWARE_MIN_CONFIDENCE = 75;
const EDGE_AWARE_COOLDOWN_MS = 5_000;

export type VitStatusForStopLabel = {
  latest?: {
    top_label?: string;
    top_confidence?: number;
    results?: { label: string; confidence: number }[];
    timestamp?: string;
  } | null;
  activity?: { last_decode_at?: string | null };
};

let edgeAwareEnabled = false;
let stopLabelEstopArmed = false;
let lastHandledKey: string | null = null;
let lastTriggerAt = 0;

export function isStopLabel(label: string): boolean {
  return label.trim().toLowerCase() === EDGE_AWARE_STOP_LABEL.toLowerCase();
}

/** Called when the test bench stop-mode toggle changes (or loads from backend). */
export function setEdgeAwareStopEnabled(enabled: boolean) {
  const was = edgeAwareEnabled;
  edgeAwareEnabled = enabled;
  if (enabled && !was) {
    lastHandledKey = null;
  }
}

export function isEdgeAwareStopEnabled(): boolean {
  return edgeAwareEnabled;
}

/** Dedupe key for a VIT decode (timestamp from latest or last_decode_at). */
export function vitDecodeEventKey(vit: VitStatusForStopLabel): string | null {
  const latest = vit.latest;
  if (!latest) return null;
  return latest.timestamp || vit.activity?.last_decode_at || null;
}

/**
 * Arm stop-label stop only while a test-bench session is active (after START).
 * Pass ignoreCurrentDecodeKey (from vitDecodeEventKey) to skip the decode already
 * visible when START was pressed — only new decodes after START can trigger.
 */
export function setStopLabelEstopArmed(armed: boolean, ignoreCurrentDecodeKey?: string | null) {
  const was = stopLabelEstopArmed;
  stopLabelEstopArmed = armed;
  if (armed && !was) {
    lastHandledKey = ignoreCurrentDecodeKey ?? null;
    lastTriggerAt = 0;
  }
}

/** All label/confidence rows from the latest decode (ranking ignored). */
function labelRows(latest: NonNullable<VitStatusForStopLabel['latest']>) {
  const rows = [...(latest.results ?? [])];
  const topLabel = latest.top_label?.trim();
  if (topLabel && !rows.some((r) => r.label === topLabel)) {
    rows.push({ label: topLabel, confidence: latest.top_confidence ?? 0 });
  }
  return rows;
}

/** Highest bottle confidence on the latest decode, if any. */
export function bestStopLabelConfidence(vit: VitStatusForStopLabel): number | null {
  const latest = vit.latest;
  if (!latest) return null;
  const bottleRows = labelRows(latest).filter((row) => isStopLabel(row.label));
  if (!bottleRows.length) return null;
  return Math.max(...bottleRows.map((row) => row.confidence));
}

/** True when any stop-label row meets the confidence threshold (any rank). */
export function hasQualifyingStopLabel(vit: VitStatusForStopLabel): boolean {
  const confidence = bestStopLabelConfidence(vit);
  return confidence != null && confidence >= EDGE_AWARE_MIN_CONFIDENCE;
}

/**
 * When edge-aware mode is on, session is armed, and stop label meets threshold, send
 * stop (same as manual stop). Returns true if triggered.
 */
export function processVitStatusForStopLabelEstop(vit: VitStatusForStopLabel): boolean {
  if (!edgeAwareEnabled || !stopLabelEstopArmed || !benchModeHasDashboardBottleStop()) return false;

  const latest = vit.latest;
  const key = vitDecodeEventKey(vit);
  if (!latest || !key || key === lastHandledKey) return false;

  const confidence = bestStopLabelConfidence(vit);
  if (confidence == null || confidence < EDGE_AWARE_MIN_CONFIDENCE) return false;

  return triggerStopLabelStop(key, confidence);
}

function triggerStopLabelStop(key: string, confidence: number): boolean {
  const now = Date.now();
  if (now - lastTriggerAt < EDGE_AWARE_COOLDOWN_MS) return false;
  if (useMetricsStore.getState().estopActive) return false;

  lastHandledKey = key;
  lastTriggerAt = now;
  notifyTestBenchStopLabelStop(confidence);
  sendDashboardBottleStop();
  useMetricsStore.getState().pushEvent(
    'warning',
    `Edge Stop — bottle ${confidence.toFixed(2)} percent (minimum ${EDGE_AWARE_MIN_CONFIDENCE} percent), mission ended`,
    'yahboom/vit/status',
  );
  return true;
}
