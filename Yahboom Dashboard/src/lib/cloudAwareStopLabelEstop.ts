import { sendDashboardBottleStop } from './Controls';
import { useMetricsStore } from '../app/store';
import { getStopCategory, getStopThreshold } from './clientVit/referenceStore';
import { benchModeHasDashboardBottleStop } from './testBenchSession';
import { notifyTestBenchStopLabelStop } from './testBenchSession';

/** Minimum reference similarity (%) before cloud-aware dashboard stop fires. */
export const CLOUD_AWARE_MIN_CONFIDENCE = 70;

function minStopSimilarityPercent(): number {
  return getStopThreshold() * 100;
}
const CLOUD_AWARE_COOLDOWN_MS = 5_000;

export type VitReferenceMatch = {
  label: string;
  category?: string;
  sample_id?: number | null;
  similarity: number;
  similarity_percent: number;
  threshold: number;
  hit: boolean;
  stop_hit?: boolean;
};

export type VitStatusForStopLabel = {
  reference_ready?: boolean;
  latest?: {
    top_label?: string;
    top_confidence?: number;
    match_mode?: string;
    reference_match?: VitReferenceMatch;
    results?: { label: string; confidence: number }[];
    timestamp?: string;
  } | null;
  activity?: { last_decode_at?: string | null };
};

let cloudAwareEnabled = false;
let stopLabelEstopArmed = false;
let lastHandledKey: string | null = null;
let lastTriggerAt = 0;

/** Called when the test bench stop-mode toggle changes (or loads from backend). */
export function setCloudAwareStopEnabled(enabled: boolean) {
  const was = cloudAwareEnabled;
  cloudAwareEnabled = enabled;
  if (enabled && !was) {
    lastHandledKey = null;
  }
}

export function isCloudAwareStopEnabled(): boolean {
  return cloudAwareEnabled;
}

/** Dedupe key for a VIT decode (timestamp from latest or last_decode_at). */
export function vitDecodeEventKey(vit: VitStatusForStopLabel): string | null {
  const latest = vit.latest;
  if (!latest) return null;
  return latest.timestamp || vit.activity?.last_decode_at || null;
}

/**
 * Arm reference-match stop only while a test-bench session is active (after START).
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

/** Reference similarity (%) on the latest decode when a match exists. */
export function bestReferenceMatchConfidence(vit: VitStatusForStopLabel): number | null {
  const ref = vit.latest?.reference_match;
  if (!ref) return null;
  return ref.similarity_percent;
}

/** True when the latest match qualifies for cloud stop (stop category only). */
export function hasQualifyingReferenceMatch(vit: VitStatusForStopLabel): boolean {
  const ref = vit.latest?.reference_match;
  if (!ref) return false;
  const stopHit = ref.stop_hit ?? (ref.category === getStopCategory() && ref.hit);
  if (!stopHit) return false;
  return ref.similarity_percent >= minStopSimilarityPercent();
}

/**
 * When cloud-aware mode is on, session is armed, and reference match hits threshold,
 * send stop (same as manual stop). Returns true if triggered.
 */
export function processVitStatusForStopLabelEstop(vit: VitStatusForStopLabel): boolean {
  if (!cloudAwareEnabled || !stopLabelEstopArmed || !benchModeHasDashboardBottleStop()) return false;

  const latest = vit.latest;
  const key = vitDecodeEventKey(vit);
  if (!latest || !key || key === lastHandledKey) return false;

  const ref = latest.reference_match;
  const stopHit = ref?.stop_hit ?? (ref?.category === getStopCategory() && ref?.hit);
  if (!ref || !stopHit || ref.similarity_percent < minStopSimilarityPercent()) return false;

  return triggerStopLabelStop(key, ref.similarity_percent, ref.label);
}

function triggerStopLabelStop(key: string, confidence: number, label: string): boolean {
  const now = Date.now();
  if (now - lastTriggerAt < CLOUD_AWARE_COOLDOWN_MS) return false;
  if (useMetricsStore.getState().estopActive) return false;

  lastHandledKey = key;
  lastTriggerAt = now;
  notifyTestBenchStopLabelStop(confidence);
  sendDashboardBottleStop();
  useMetricsStore.getState().pushEvent(
    'warning',
    `Cloud Stop — ${label} reference match ${confidence.toFixed(2)} percent (minimum ${minStopSimilarityPercent().toFixed(0)} percent), mission ended`,
    'yahboom/vit/status',
  );
  return true;
}
