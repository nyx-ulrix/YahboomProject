// Cosine similarity check — client image-to-image matching via L2-normalized dot product.
//
// Scans every reference vector in the library and reports matches for the
// Cosine Similarity Decoder widget. Cache Aware cloud stop uses stopHit, which
// is true only when the best match belongs to the stop category (default target_bottle).

import {
  getReferenceVectorsForDim,
  getStopCategory,
  getStopThreshold,
  hasReferenceVectors,
} from './referenceStore';

export type CosineSimilarityCheckResult = {
  label: string;
  category: string;
  sampleId: number | null;
  similarity: number;
  similarityPercent: number;
  threshold: number; // effective threshold used for the hit test
  hit: boolean; // display hit — any category above threshold
  stopHit: boolean; // cloud stop — stop category only
  embeddingDim: number;
};

/** @deprecated Use CosineSimilarityCheckResult */
export type ClientReferenceMatch = CosineSimilarityCheckResult;

function l2normalize(v: Float32Array): Float32Array {
  let sum = 0;
  for (let i = 0; i < v.length; i++) sum += v[i] * v[i];
  const norm = Math.sqrt(sum);
  if (norm <= 1e-12) return v;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / norm;
  return out;
}

function dot(a: Float32Array, b: Float32Array): number {
  let sum = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) sum += a[i] * b[i];
  return sum;
}

function scoreReferenceForCosineSimilarityCheck(
  normalized: Float32Array,
  ref: ReturnType<typeof getReferenceVectorsForDim>[number],
  liveDim: number,
  stopThreshold: number,
  stopCategory: string,
): CosineSimilarityCheckResult {
  const similarity = dot(normalized, ref.vec);
  const effectiveThreshold = Math.max(ref.threshold, stopThreshold);
  const hit = similarity >= effectiveThreshold;
  const stopHit = hit && ref.category === stopCategory;
  return {
    label: ref.label,
    category: ref.category,
    sampleId: ref.sampleId,
    similarity,
    similarityPercent: Math.round(similarity * 100 * 100) / 100,
    threshold: effectiveThreshold,
    hit,
    stopHit,
    embeddingDim: liveDim,
  };
}

function matchNameKey(label: string): string {
  return label.trim().toLowerCase();
}

function matchSimilarityValue(similarity: number, similarityPercent?: number): number {
  if (Number.isFinite(similarity)) return similarity;
  if (similarityPercent != null) return similarityPercent / 100;
  return 0;
}

/** One row per reference name — keep the highest-similarity sample only. */
function dedupeCosineSimilarityCheckByName(
  matches: CosineSimilarityCheckResult[],
): CosineSimilarityCheckResult[] {
  const bestByName = new Map<string, CosineSimilarityCheckResult>();
  for (const match of matches) {
    const key = matchNameKey(match.label);
    const existing = bestByName.get(key);
    if (!existing || match.similarity > existing.similarity) {
      bestByName.set(key, match);
    }
  }
  return Array.from(bestByName.values()).sort((a, b) => b.similarity - a.similarity);
}

export type CosineSimilarityCheckMatchLike = {
  label: string;
  category?: string;
  sample_id?: number | null;
  similarity: number;
  similarity_percent?: number;
  threshold?: number;
  hit?: boolean;
  stop_hit?: boolean;
};

/** @deprecated Use CosineSimilarityCheckMatchLike */
export type ReferenceMatchLike = CosineSimilarityCheckMatchLike;

/** Dedupe API/status cosine similarity rows by label for display. */
export function dedupeCosineSimilarityCheckByLabel<T extends CosineSimilarityCheckMatchLike>(
  matches: T[],
): T[] {
  const bestByName = new Map<string, T>();
  for (const match of matches) {
    const key = matchNameKey(match.label);
    const score = matchSimilarityValue(match.similarity, match.similarity_percent);
    const existing = bestByName.get(key);
    const existingScore = existing
      ? matchSimilarityValue(existing.similarity, existing.similarity_percent)
      : -1;
    if (!existing || score > existingScore) {
      bestByName.set(key, match);
    }
  }
  return Array.from(bestByName.values()).sort(
    (a, b) => matchSimilarityValue(b.similarity, b.similarity_percent)
      - matchSimilarityValue(a.similarity, a.similarity_percent),
  );
}

/** @deprecated Use dedupeCosineSimilarityCheckByLabel */
export const dedupeReferenceMatchesByLabel = dedupeCosineSimilarityCheckByLabel;

/** Cosine similarity check — every library match for a live Pi embedding, highest first. */
export function cosineSimilarityCheck(live: Float32Array): CosineSimilarityCheckResult[] {
  if (!hasReferenceVectors() || live.length === 0) return [];

  const references = getReferenceVectorsForDim(live.length);
  if (references.length === 0) return [];

  const normalized = l2normalize(live);
  const stopThreshold = getStopThreshold();
  const stopCategory = getStopCategory();

  const scored = references
    .map((ref) => scoreReferenceForCosineSimilarityCheck(
      normalized, ref, live.length, stopThreshold, stopCategory,
    ))
    .sort((a, b) => b.similarity - a.similarity);

  return dedupeCosineSimilarityCheckByName(scored);
}

/** Best cosine similarity check result for a live Pi embedding, or null when none apply. */
export function cosineSimilarityCheckBest(live: Float32Array): CosineSimilarityCheckResult | null {
  const all = cosineSimilarityCheck(live);
  return all[0] ?? null;
}
