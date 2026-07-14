// Client-side reference library for image-to-image matching.
//
// Loads the active reference embeddings from the backend (GET /api/vit/reference/active),
// decodes the base64 float32 blobs into normalized Float32Array vectors, and groups
// them by embedding dimension. The browser matches Pi embeddings against these
// vectors (see referenceMatch.ts). The backend no longer runs the live match.

export type ReferenceVector = {
  sampleId: number | null;
  label: string;
  dim: number;
  threshold: number;
  vec: Float32Array; // L2-normalized
};

type ReferenceActiveResponse = {
  status?: string;
  active_category?: string | null;
  label?: string;
  default_threshold?: number;
  stop_threshold?: number;
  count?: number;
  objects?: Array<{
    sample_id?: number | null;
    label?: string;
    embedding_dim?: number | null;
    threshold?: number;
    data?: string;
    embedding?: number[];
  }>;
};

let vectorsByDim = new Map<number, ReferenceVector[]>();
let activeCategory: string | null = null;
let stopThreshold = 0.75;
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

/** Fetch + parse the active reference library. Returns true when vectors loaded. */
export async function loadReferenceLibrary(force = false): Promise<boolean> {
  if (inFlight && !force) return inFlight;
  const run = (async () => {
    try {
      const res = await fetch('/api/vit/reference/active', { cache: 'no-store' });
      if (!res.ok) {
        loadError = `reference fetch failed (${res.status})`;
        return false;
      }
      const data = (await res.json()) as ReferenceActiveResponse;
      const next = new Map<number, ReferenceVector[]>();
      for (const obj of data.objects ?? []) {
        let raw: Float32Array | null = null;
        if (typeof obj.data === 'string') raw = base64ToFloat32(obj.data);
        else if (Array.isArray(obj.embedding)) raw = Float32Array.from(obj.embedding);
        if (!raw || raw.length === 0) continue;
        const dim = obj.embedding_dim ?? raw.length;
        const vec = l2normalize(raw);
        const entry: ReferenceVector = {
          sampleId: obj.sample_id ?? null,
          label: obj.label ?? data.label ?? 'bottle',
          dim,
          threshold: obj.threshold ?? data.default_threshold ?? 0.7,
          vec,
        };
        const bucket = next.get(dim);
        if (bucket) bucket.push(entry);
        else next.set(dim, [entry]);
      }
      vectorsByDim = next;
      activeCategory = data.active_category ?? null;
      stopThreshold = data.stop_threshold ?? stopThreshold;
      defaultThreshold = data.default_threshold ?? defaultThreshold;
      loaded = true;
      loadError = next.size === 0 ? 'no reference vectors' : null;
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

export function getActiveCategory(): string | null {
  return activeCategory;
}

export function isReferenceLoaded(): boolean {
  return loaded;
}

export function getReferenceError(): string | null {
  return loadError;
}
