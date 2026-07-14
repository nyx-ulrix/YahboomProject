// Client image-to-image detection loop.
//
// The browser is fed ONLY by image embeddings the Pi generates (relayed by the
// backend). Each new Pi embedding is matched against the full dashboard
// reference library in the browser (image-to-image), and the result is posted
// back so /api/vit/status, the widget, and the CSV stay populated. Edge stop
// fires only when stop_hit is true (default: best match is target_bottle).

import { useEffect } from 'react';
import {
  base64ToFloat32,
  getLibraryEmbeddingSizeBytes,
  isReferenceLoaded,
  loadReferenceLibrary,
} from './clientVit/referenceStore';
import { matchEmbedding } from './clientVit/referenceMatch';

const EMBEDDING_POLL_MS = 180;

type LatestEmbedding = {
  seq?: number;
  data?: string | null;
  embedding_dim?: number | null;
  embedding_size?: number | null;
  image_file_size?: number | null;
};

export function useClientReferenceDetection() {
  useEffect(() => {
    let alive = true;
    let lastSeq = 0;
    let posting = false;

    void loadReferenceLibrary();

    const poll = async () => {
      if (!alive || posting) return;
      try {
        const res = await fetch('/api/vit/client/latest_embedding', { cache: 'no-store' });
        if (!res.ok || !alive) return;
        const data = (await res.json()) as LatestEmbedding;
        const seq = data.seq ?? 0;
        if (!data.data || seq === 0 || seq === lastSeq) return;
        lastSeq = seq;

        const embedSize = data.embedding_size ?? (
          data.embedding_dim != null ? data.embedding_dim * 4 : null
        );
        const loadedSize = getLibraryEmbeddingSizeBytes();
        if (
          embedSize != null
          && (!isReferenceLoaded() || loadedSize !== embedSize)
        ) {
          const ok = await loadReferenceLibrary(embedSize, true);
          if (!ok || !alive) return;
        } else if (!isReferenceLoaded()) {
          const ok = await loadReferenceLibrary(embedSize ?? undefined, true);
          if (!ok || !alive) return;
        }

        const live = base64ToFloat32(data.data);
        const match = matchEmbedding(live);
        if (!match || !alive) return;

        posting = true;
        try {
          await fetch('/api/vit/client/match_result', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              label: match.label,
              category: match.category,
              sample_id: match.sampleId,
              similarity: match.similarity,
              threshold: match.threshold,
              hit: match.hit,
              stop_hit: match.stopHit,
              embedding_dim: match.embeddingDim,
              embedding_size: data.embedding_size ?? live.length * 4,
              image_file_size: data.image_file_size ?? null,
            }),
          });
        } finally {
          posting = false;
        }
      } catch {
        /* backend unreachable — next tick retries */
      }
    };

    void poll();
    const id = setInterval(poll, EMBEDDING_POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);
}
