import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ImagePlus, ArrowRightLeft, Upload } from 'lucide-react';
import { loadReferenceLibrary } from '../../lib/clientVit/referenceStore';

type LibrarySample = {
  category: string;
  sample_id: number;
  label: string;
  source?: string;
  embedding_size_bytes: number;
  created_at?: number;
  has_image?: boolean;
};

type LibraryCategory = {
  category: string;
  snapshot_count: number;
};

const EMBED_SIZE_OPTIONS = [512, 1024, 2048] as const;
const CATEGORY_RE = /^[a-z0-9_-]{1,48}$/;

function normalizeCategory(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
}

function sampleImageUrl(sample: LibrarySample): string | null {
  if (!sample.has_image) return null;
  return `/api/vit/reference/sample-image/${encodeURIComponent(sample.category)}/${sample.sample_id}?embedding_size_bytes=${sample.embedding_size_bytes}`;
}

export function UploadEmbeddingsWidget() {
  const [name, setName] = useState('');
  const [categoryOverride, setCategoryOverride] = useState('');
  const [embedBytes, setEmbedBytes] = useState<(typeof EMBED_SIZE_OPTIONS)[number]>(2048);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [samples, setSamples] = useState<LibrarySample[]>([]);
  const [categories, setCategories] = useState<LibraryCategory[]>([]);
  const [moveTargets, setMoveTargets] = useState<Record<string, string>>({});
  const [movingId, setMovingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const derivedCategory = useMemo(() => {
    const raw = categoryOverride.trim() || name.trim();
    if (!raw) return '';
    return normalizeCategory(raw);
  }, [categoryOverride, name]);

  const categoryValid = derivedCategory.length > 0 && CATEGORY_RE.test(derivedCategory);

  const refreshSamples = useCallback(async () => {
    try {
      const res = await fetch(`/api/vit/reference/samples?embedding_size_bytes=${embedBytes}`, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json() as {
        samples?: LibrarySample[];
        categories?: LibraryCategory[];
      };
      setSamples(data.samples ?? []);
      setCategories(data.categories ?? []);
    } catch {
      /* backend may be starting */
    }
  }, [embedBytes]);

  useEffect(() => {
    void refreshSamples();
    const id = setInterval(() => { void refreshSamples(); }, 4000);
    return () => clearInterval(id);
  }, [refreshSamples]);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const onFileSelected = (file: File | null) => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(file);
    setPreviewUrl(file ? URL.createObjectURL(file) : null);
    setMessage(null);
  };

  const uploadEmbedding = async () => {
    if (!selectedFile || !name.trim() || !categoryValid || uploading) return;
    setUploading(true);
    setMessage(null);
    try {
      const form = new FormData();
      form.append('image', selectedFile);
      form.append('name', name.trim());
      if (categoryOverride.trim()) {
        form.append('category', categoryOverride.trim());
      }
      form.append('embedding_size_bytes', String(embedBytes));

      const res = await fetch('/api/vit/reference/upload', { method: 'POST', body: form });
      const data = await res.json() as {
        status?: string;
        message?: string;
        sample_id?: number;
        total?: number;
        category?: string;
        label?: string;
      };
      if (!res.ok || data.status === 'error') {
        setMessage(data.message ?? 'Upload failed');
        return;
      }
      setMessage(
        `Saved "${data.label ?? name.trim()}" as sample ${data.sample_id ?? '?'} in ${data.category ?? derivedCategory} (${data.total ?? '?'} total)`,
      );
      void loadReferenceLibrary(embedBytes, true);
      void refreshSamples();
      onFileSelected(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    } catch {
      setMessage('Could not reach backend');
    } finally {
      setUploading(false);
    }
  };

  const moveSample = async (sample: LibrarySample) => {
    const key = `${sample.category}:${sample.sample_id}`;
    const targetRaw = moveTargets[key]?.trim();
    if (!targetRaw || movingId) return;
    const targetCategory = normalizeCategory(targetRaw);
    if (!CATEGORY_RE.test(targetCategory)) {
      setMessage('Invalid destination category');
      return;
    }
    setMovingId(key);
    setMessage(null);
    try {
      const res = await fetch('/api/vit/reference/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_category: sample.category,
          sample_id: sample.sample_id,
          to_category: targetCategory,
          embedding_size_bytes: sample.embedding_size_bytes,
        }),
      });
      const data = await res.json() as {
        status?: string;
        message?: string;
        to_category?: string;
        sample_id?: number;
      };
      if (!res.ok || data.status === 'error') {
        setMessage(data.message ?? 'Move failed');
        return;
      }
      setMessage(
        `Moved to ${data.to_category ?? targetCategory} as sample ${data.sample_id ?? '?'}`,
      );
      void loadReferenceLibrary(embedBytes, true);
      void refreshSamples();
    } catch {
      setMessage('Could not reach backend');
    } finally {
      setMovingId(null);
    }
  };

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-2">
        <ImagePlus size={16} style={{ color: 'var(--accent-purple)' }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          Upload Embeddings
        </span>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45, margin: 0 }}>
        Upload a product photo, name it, and save a MobileCLIP embedding to the reference library.
        Move samples between categories below.
      </p>

      <div
        role="button"
        tabIndex={0}
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click(); }}
        className="rounded-2xl flex items-center justify-center cursor-pointer overflow-hidden"
        style={{
          minHeight: 120,
          border: '1px dashed var(--stroke-strong)',
          background: 'var(--bg-surface)',
        }}
      >
        {previewUrl ? (
          <img src={previewUrl} alt="Preview" style={{ maxHeight: 140, maxWidth: '100%', objectFit: 'contain' }} />
        ) : (
          <div className="flex flex-col items-center gap-1 py-6" style={{ color: 'var(--text-muted)' }}>
            <Upload size={22} />
            <span style={{ fontSize: 11 }}>Click to choose an image</span>
          </div>
        )}
      </div>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => onFileSelected(e.target.files?.[0] ?? null)}
      />

      <label className="flex flex-col gap-1">
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. tea canister"
          className="rounded-xl px-3 py-2 outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        />
      </label>

      <label className="flex flex-col gap-1">
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>
          Category slug (optional override)
        </span>
        <input
          value={categoryOverride}
          onChange={(e) => setCategoryOverride(e.target.value)}
          placeholder={derivedCategory || 'auto from name'}
          className="rounded-xl px-3 py-2 outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            color: 'var(--text-primary)',
            fontSize: 13,
            fontFamily: 'monospace',
          }}
        />
        <span style={{ fontSize: 10, color: categoryValid ? 'var(--text-muted)' : '#f87171' }}>
          {derivedCategory
            ? `Library folder: ${derivedCategory}`
            : 'Enter a name to derive the category slug'}
        </span>
      </label>

      <label className="flex flex-col gap-1">
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>Embedding size</span>
        <select
          value={embedBytes}
          onChange={(e) => setEmbedBytes(Number(e.target.value) as (typeof EMBED_SIZE_OPTIONS)[number])}
          className="rounded-xl px-3 py-2 outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        >
          {EMBED_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>{n} bytes</option>
          ))}
        </select>
      </label>

      <button
        type="button"
        onClick={() => { void uploadEmbedding(); }}
        disabled={!selectedFile || !name.trim() || !categoryValid || uploading}
        className="rounded-xl py-2.5 font-semibold"
        style={{
          background: 'linear-gradient(135deg, var(--accent-purple), #4c1d95)',
          color: '#fff',
          opacity: (!selectedFile || !name.trim() || !categoryValid || uploading) ? 0.5 : 1,
          border: '1px solid rgba(255,255,255,0.15)',
          fontSize: 13,
          cursor: uploading ? 'wait' : 'pointer',
        }}
      >
        {uploading ? 'Encoding…' : 'Save embedding'}
      </button>

      {message && (
        <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: 0 }}>{message}</p>
      )}

      <div className="flex items-center justify-between pt-1">
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-primary)' }}>
          Reference library
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          {samples.length} sample{samples.length === 1 ? '' : 's'} · {categories.length} categor{categories.length === 1 ? 'y' : 'ies'}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2 pr-1">
        {samples.length === 0 ? (
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: 0 }}>No samples yet at {embedBytes} B.</p>
        ) : samples.map((sample) => {
          const key = `${sample.category}:${sample.sample_id}`;
          const imgUrl = sampleImageUrl(sample);
          return (
            <div
              key={key}
              className="rounded-xl p-2 flex gap-2 items-center"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)' }}
            >
              <div
                className="rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center"
                style={{ width: 44, height: 44, background: 'rgba(0,0,0,0.25)' }}
              >
                {imgUrl ? (
                  <img src={imgUrl} alt={sample.label} style={{ width: 44, height: 44, objectFit: 'cover' }} />
                ) : (
                  <ImagePlus size={16} style={{ color: 'var(--text-muted)' }} />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="truncate" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                  {sample.label}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                  {sample.category} · #{sample.sample_id}
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <input
                  value={moveTargets[key] ?? ''}
                  onChange={(e) => setMoveTargets((prev) => ({ ...prev, [key]: e.target.value }))}
                  placeholder="target_bottle"
                  className="rounded-lg px-2 py-1 outline-none"
                  style={{
                    width: 96,
                    fontSize: 10,
                    fontFamily: 'monospace',
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--stroke-subtle)',
                    color: 'var(--text-primary)',
                  }}
                />
                <button
                  type="button"
                  title="Move to category"
                  disabled={movingId === key || !moveTargets[key]?.trim()}
                  onClick={() => { void moveSample(sample); }}
                  className="rounded-lg p-1.5"
                  style={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--stroke-subtle)',
                    color: 'var(--text-secondary)',
                    opacity: movingId === key ? 0.5 : 1,
                  }}
                >
                  <ArrowRightLeft size={14} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export const uploadEmbeddingsDef = {
  id: 'upload_embeddings_widget',
  name: 'Upload Embeddings',
  group: 'health' as const,
  sizeClass: 'L' as const,
  defaultSize: { w: 3, h: 5, minW: 2, minH: 4 },
  icon: 'ImagePlus',
  pinned: false,
  component: UploadEmbeddingsWidget,
};
