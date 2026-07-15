// Client-side reference library for image-to-image matching.
//
// Loads every category from the dashboard reference library (GET
// /api/vit/reference/library), decodes base64 float32 blobs into normalized
// vectors, and groups them by embedding dimension. The browser matches Pi
// embeddings against all vectors for scene-decoder display; only the stop
// category (default target_bottle) can trigger cloud stop.

export const STOP_REFERENCE_CATEGORY = 'target_bottle';

export type ReferenceVector = {
  category: string;
  sampleId: number | null;
  label: string;
  dim: number;
  threshold: number;
  vec: Float32Array; // L2-normalized
};

type ReferenceLibraryResponse = {
  status?: string;
  embedding_size_bytes?: number | null;
  stop_category?: string;
  default_threshold?: number;
  stop_threshold?: number;
  count?: number;
  categories?: Array<{ category: string; snapshot_count: number }>;
  objects?: Array<{
    category?: string;
    sample_id?: number | null;
    label?: string;
    embedding_dim?: number | null;
    threshold?: number;
    data?: string;
    embedding?: number[];
  }>;
};

let vectorsByDim = new Map<number, ReferenceVector[]>();
let libraryEmbeddingSizeBytes: number | null = null;
let stopCategory = STOP_REFERENCE_CATEGORY;
let libraryCategories: Array<{ category: string; snapshot_count: number }> = [];
let stopThreshold = 0.70;
let defaultThreshold = 0.7;
let loaded = false;
let loadError: string | null = null;
let inFlight: Promise<boolean> | null = null;

export function base64ToFloat32(b64: string): Float32Array {
  const binary = atob(b64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
  // The Pi/dashboard write little-endian float32; browsers are little-endian.
  return new Float32Array(bytes.buffer, bytes.byteOffset, Math.floor(len / 4));
}

function l2normalize(v: Float32Array): Float32Array {
  let sum = 0;
  for (let i = 0; i < v.length; i++) sum += v[i] * v[i];
  const norm = Math.sqrt(sum);
  if (norm <= 1e-12) return v;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / norm;
  return out;
}

/** Fetch + parse the full reference library. Returns true when vectors loaded. */
export async function loadReferenceLibrary(
  embeddingSizeBytes?: number | null,
  force = false,
): Promise<boolean> {
  if (inFlight && !force) return inFlight;
  const run = (async () => {
    try {
      const query = embeddingSizeBytes != null
        ? `?embedding_size_bytes=${embeddingSizeBytes}`
        : '';
      const res = await fetch(`/api/vit/reference/library${query}`, { cache: 'no-store' });
      if (!res.ok) {
        loadError = `reference library fetch failed (${res.status})`;
        return false;
      }
      const data = (await res.json()) as ReferenceLibraryResponse;
      const next = new Map<number, ReferenceVector[]>();
      for (const obj of data.objects ?? []) {
        let raw: Float32Array | null = null;
        if (typeof obj.data === 'string') raw = base64ToFloat32(obj.data);
        else if (Array.isArray(obj.embedding)) raw = Float32Array.from(obj.embedding);
        if (!raw || raw.length === 0) continue;
        const dim = obj.embedding_dim ?? raw.length;
        const vec = l2normalize(raw);
        const entry: ReferenceVector = {
          category: obj.category ?? 'unknown',
          sampleId: obj.sample_id ?? null,
          label: obj.label ?? 'target bottle',
          dim,
          threshold: obj.threshold ?? data.default_threshold ?? 0.7,
          vec,
        };
        const bucket = next.get(dim);
        if (bucket) bucket.push(entry);
        else next.set(dim, [entry]);
      }
      vectorsByDim = next;
      libraryEmbeddingSizeBytes = data.embedding_size_bytes ?? embeddingSizeBytes ?? null;
      stopCategory = data.stop_category ?? STOP_REFERENCE_CATEGORY;
      libraryCategories = data.categories ?? [];
      stopThreshold = data.stop_threshold ?? stopThreshold;
      defaultThreshold = data.default_threshold ?? defaultThreshold;
      loaded = true;
      loadError = next.size === 0 ? 'no reference vectors in library' : null;
      return next.size > 0;
    } catch (err) {
      loadError = err instanceof Error ? err.message : 'reference load error';
      return false;
    } finally {
      inFlight = null;
    }
  })();
  inFlight = run;
  return run;
}

export function getReferenceVectorsForDim(dim: number): ReferenceVector[] {
  return vectorsByDim.get(dim) ?? [];
}

export function hasReferenceVectors(): boolean {
  return vectorsByDim.size > 0;
}

export function getStopThreshold(): number {
  return stopThreshold;
}

export function getStopCategory(): string {
  return stopCategory;
}

export function setStopCategory(category: string): void {
  stopCategory = category;
}

/** Persist stop target on the backend and refresh the client library. */
export async function applyStopCategory(
  category: string,
  embeddingSizeBytes?: number | null,
): Promise<boolean> {
  try {
    const res = await fetch('/api/vit/reference/stop_category', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { stop_category?: string };
    stopCategory = data.stop_category ?? category;
    return loadReferenceLibrary(embeddingSizeBytes, true);
  } catch {
    return false;
  }
}

export function getLibraryEmbeddingSizeBytes(): number | null {
  return libraryEmbeddingSizeBytes;
}

export function getLibraryCategories(): Array<{ category: string; snapshot_count: number }> {
  return libraryCategories;
}

export function isReferenceLoaded(): boolean {
  return loaded;
}

export function getReferenceError(): string | null {
  return loadError;
}
