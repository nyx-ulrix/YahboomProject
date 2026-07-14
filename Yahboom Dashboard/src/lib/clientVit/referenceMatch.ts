// Client image-to-image matching (cosine similarity via dot product).
//
// Mirrors the backend ReferenceEmbeddingStore.match(): L2-normalize the live Pi
// embedding, compare it (same dimension only) against every reference vector, and
// report the best match. A "hit" requires the similarity to clear the effective
// threshold = max(sample threshold, stop threshold).

import {
  getReferenceVectorsForDim,
  getStopThreshold,
  hasReferenceVectors,
} from './referenceStore';

export type ClientReferenceMatch = {
  label: string;
  sampleId: number | null;
  similarity: number;
  similarityPercent: number;
  threshold: number; // effective threshold used for the hit test
  hit: boolean;
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

/** Best image-to-image match for a live Pi embedding, or null when none apply. */
export function matchEmbedding(live: Float32Array): ClientReferenceMatch | null {
  if (!hasReferenceVectors() || live.length === 0) return null;

  const references = getReferenceVectorsForDim(live.length);
  if (references.length === 0) return null;

  const normalized = l2normalize(live);
  const stopThreshold = getStopThreshold();

  let best: { similarity: number; index: number } | null = null;
  for (let i = 0; i < references.length; i++) {
    const similarity = dot(normalized, references[i].vec);
    if (!best || similarity > best.similarity) best = { similarity, index: i };
  }
  if (!best) return null;

  const ref = references[best.index];
  const effectiveThreshold = Math.max(ref.threshold, stopThreshold);
  const similarity = best.similarity;
  return {
    label: ref.label,
    sampleId: ref.sampleId,
    similarity,
    similarityPercent: Math.round(similarity * 100 * 100) / 100,
    threshold: effectiveThreshold,
    hit: similarity >= effectiveThreshold,
    embeddingDim: live.length,
  };
}
