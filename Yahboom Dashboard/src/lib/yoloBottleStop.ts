import { sendDashboardBottleStop } from './Controls';
import { useMetricsStore } from '../app/store';
import {
  benchHasYoloBottleStop,
  loadStopSimilarityThresholdPct,
} from './testBenchStorage';
import {
  getTestBenchStopMode,
  notifyTestBenchStopLabelStop,
} from './testBenchSession';

export type YoloStatusForBottleStop = {
  readings_fresh?: boolean;
  latest?: {
    timestamp?: string;
    detections?: Array<{
      label: string;
      confidence_percent: number;
    }>;
    top_detection?: {
      label: string;
      confidence_percent: number;
    } | null;
  } | null;
};

const BOTTLE_LABEL = 'bottle';
const YOLO_STOP_COOLDOWN_MS = 5_000;

let yoloStopArmed = false;
let lastHandledKey: string | null = null;
let lastTriggerAt = 0;

function minStopSimilarityPercent(): number {
  return loadStopSimilarityThresholdPct();
}

function isBottleLabel(label: string): boolean {
  return label.trim().toLowerCase() === BOTTLE_LABEL;
}

export function yoloStopEventKey(status: YoloStatusForBottleStop): string | null {
  const latest = status.latest;
  if (!latest?.timestamp) return null;
  const bottle = bestQualifyingBottle(status);
  if (!bottle) return null;
  return `${latest.timestamp}:${bottle.label}:${bottle.confidence_percent.toFixed(1)}`;
}

function bestQualifyingBottle(
  status: YoloStatusForBottleStop,
): { label: string; confidence_percent: number } | null {
  const threshold = minStopSimilarityPercent();
  const detections = status.latest?.detections ?? [];
  let best: { label: string; confidence_percent: number } | null = null;

  for (const det of detections) {
    if (!isBottleLabel(det.label)) continue;
    if (det.confidence_percent < threshold) continue;
    if (!best || det.confidence_percent > best.confidence_percent) {
      best = { label: det.label, confidence_percent: det.confidence_percent };
    }
  }

  return best;
}

/** Arm YOLO bottle stop while a test-bench session is active (after START). */
export function setYoloStopArmed(armed: boolean, ignoreCurrentEventKey?: string | null) {
  const was = yoloStopArmed;
  yoloStopArmed = armed;
  if (armed && !was) {
    lastHandledKey = ignoreCurrentEventKey ?? null;
    lastTriggerAt = 0;
  }
}

/**
 * When YOLO mode is on, session is armed, and a bottle meets Stop Similarity (%),
 * send auto_off + stop (same as cache-aware dashboard stop).
 */
export function processYoloStatusForBottleStop(status: YoloStatusForBottleStop): boolean {
  if (!yoloStopArmed || !benchHasYoloBottleStop(getTestBenchStopMode())) return false;
  if (!status.readings_fresh) return false;

  const key = yoloStopEventKey(status);
  if (!key || key === lastHandledKey) return false;

  const bottle = bestQualifyingBottle(status);
  if (!bottle) return false;

  return triggerYoloBottleStop(key, bottle.confidence_percent, bottle.label);
}

function triggerYoloBottleStop(key: string, confidence: number, label: string): boolean {
  const now = Date.now();
  if (now - lastTriggerAt < YOLO_STOP_COOLDOWN_MS) return false;
  if (useMetricsStore.getState().estopActive) return false;

  lastHandledKey = key;
  lastTriggerAt = now;
  notifyTestBenchStopLabelStop(confidence);
  sendDashboardBottleStop();
  useMetricsStore.getState().pushEvent(
    'warning',
    `YOLO Stop — ${label} detected ${confidence.toFixed(1)}% confidence (minimum ${minStopSimilarityPercent()}%), mission ended`,
    'yolo',
  );
  return true;
}
