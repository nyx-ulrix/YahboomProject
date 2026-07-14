// Client image-to-image matching (cosine similarity via dot product).
//
// Scans every reference vector in the library and reports the best match for
// scene-decoder display. Edge stop uses stopHit, which is true only when the
// best match belongs to the stop category (default target_bottle).

import {
  getReferenceVectorsForDim,
  getStopCategory,
  getStopThreshold,
  hasReferenceVectors,
} from './referenceStore';

export type ClientReferenceMatch = {
  label: string;
  category: string;
  sampleId: number | null;
  similarity: number;
  similarityPercent: number;
  threshold: number; // effective threshold used for the hit test
  hit: boolean; // display hit — any category above threshold
  stopHit: boolean; // edge stop — stop category only
  embeddingDim: number;
};

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

/** Best library match for a live Pi embedding, or null when none apply. */
export function matchEmbedding(live: Float32Array): ClientReferenceMatch | null {
  if (!hasReferenceVectors() || live.length === 0) return null;

  const references = getReferenceVectorsForDim(live.length);
  if (references.length === 0) return null;

  const normalized = l2normalize(live);
  const stopThreshold = getStopThreshold();
  const stopCategory = getStopCategory();

  let best: { similarity: number; index: number } | null = null;
  for (let i = 0; i < references.length; i++) {
    const similarity = dot(normalized, references[i].vec);
    if (!best || similarity > best.similarity) best = { similarity, index: i };
  }
  if (!best) return null;

  const ref = references[best.index];
  const effectiveThreshold = Math.max(ref.threshold, stopThreshold);
  const similarity = best.similarity;
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
    embeddingDim: live.length,
  };
}
