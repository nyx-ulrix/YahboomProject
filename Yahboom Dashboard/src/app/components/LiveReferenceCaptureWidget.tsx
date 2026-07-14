import { useCallback, useEffect, useMemo, useState } from 'react';
import { Camera, Loader2 } from 'lucide-react';
import { useMetricsStore } from '../store';
import type { MetricsState } from '../types';
import { DEFAULT_STOP_TARGET_CATEGORY } from '../../lib/testBenchStorage';
import { loadReferenceLibrary } from '../../lib/clientVit/referenceStore';

type LibrarySample = {
  category: string;
  sample_id: number;
  label: string;
  embedding_size_bytes: number;
};

type LibraryCategory = {
  category: string;
  snapshot_count: number;
};

type ReferenceOption = {
  display: string;
  name: string;
  category: string;
};

type VitStatusSummary = {
  encoder_live?: boolean;
  vit_server_running?: boolean;
};

const EMBED_SIZE_OPTIONS = [512, 1024, 2048] as const;
const CATEGORY_RE = /^[a-z0-9_-]{1,48}$/;

function normalizeCategory(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
}

export function LiveReferenceCaptureWidget() {
  const [name, setName] = useState('target bottle');
  const [categoryOverride, setCategoryOverride] = useState(DEFAULT_STOP_TARGET_CATEGORY);
  const [embedBytes, setEmbedBytes] = useState<(typeof EMBED_SIZE_OPTIONS)[number]>(2048);
  const [samples, setSamples] = useState<LibrarySample[]>([]);
  const [categories, setCategories] = useState<LibraryCategory[]>([]);
  const [vitStatus, setVitStatus] = useState<VitStatusSummary | null>(null);
  const [capturingRef, setCapturingRef] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const streamRunning = useMetricsStore((s: MetricsState) => s.streamRunning);
  const encoderLive = vitStatus?.encoder_live ?? streamRunning;

  const derivedCategory = useMemo(() => {
    const raw = categoryOverride.trim() || name.trim();
    if (!raw) return '';
    return normalizeCategory(raw);
  }, [categoryOverride, name]);

  const categoryValid = derivedCategory.length > 0 && CATEGORY_RE.test(derivedCategory);

  const referenceOptions = useMemo(() => {
    const seenDisplay = new Set<string>();
    const options: ReferenceOption[] = [];

    const addOption = (display: string, optionName: string, category: string) => {
      const key = display.toLowerCase();
      if (seenDisplay.has(key)) return;
      seenDisplay.add(key);
      options.push({ display, name: optionName, category });
    };

    addOption('target bottle', 'target bottle', DEFAULT_STOP_TARGET_CATEGORY);

    for (const cat of categories) {
      const displayName = cat.category.replace(/_/g, ' ');
      addOption(displayName, displayName, cat.category);
    }

    for (const sample of samples) {
      const label = sample.label.trim();
      if (!label) continue;
      const matchingCategories = new Set(
        samples
          .filter((s) => s.label.trim().toLowerCase() === label.toLowerCase())
          .map((s) => s.category),
      );
      const display = matchingCategories.size > 1
        ? `${label} (${sample.category})`
        : label;
      addOption(display, label, sample.category);
    }

    return options.sort((a, b) => a.display.localeCompare(b.display));
  }, [samples, categories]);

  const onNameChange = (value: string) => {
    setName(value);
    const trimmed = value.trim();
    const match = referenceOptions.find(
      (opt) => opt.display === trimmed || opt.name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (match) {
      setCategoryOverride(match.category);
    }
  };

  const refreshLibrary = useCallback(async () => {
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
    void refreshLibrary();
    const id = setInterval(() => { void refreshLibrary(); }, 4000);
    return () => clearInterval(id);
  }, [refreshLibrary]);

  useEffect(() => {
    let alive = true;
    const pollStatus = async () => {
      try {
        const res = await fetch('/api/vit/status', { cache: 'no-store' });
        if (!res.ok || !alive) return;
        const data = await res.json() as VitStatusSummary;
        if (!alive) return;
        setVitStatus(data);
      } catch {
        /* backend may be starting */
      }
    };
    void pollStatus();
    const id = setInterval(() => { void pollStatus(); }, 2000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const captureReferenceSnapshot = async () => {
    if (!categoryValid || capturingRef) return;
    setCapturingRef(true);
    setMessage(null);
    const captureLabel = name.trim() || derivedCategory.replace(/_/g, ' ');
    try {
      const embRes = await fetch('/api/vit/client/latest_embedding', { cache: 'no-store' });
      if (!embRes.ok) {
        setMessage('No Pi embedding available — start VIT.py on the Pi');
        return;
      }
      const emb = await embRes.json() as { seq?: number; data?: string | null };
      if (!emb.data || !emb.seq) {
        setMessage('Waiting for Pi embedding — point the camera at your object');
        return;
      }

      const form = new FormData();
      form.append('category', derivedCategory);
      form.append('label', captureLabel);
      form.append('seq', String(emb.seq));

      const res = await fetch('/api/vit/reference/capture', { method: 'POST', body: form });
      const data = await res.json() as {
        status?: string;
        message?: string;
        sample_id?: number;
        total?: number;
        category?: string;
        embedding_size_bytes?: number;
      };
      if (!res.ok || data.status === 'error') {
        setMessage(data.message ?? 'Capture failed');
        return;
      }
      const sizeNote = data.embedding_size_bytes ? ` @ ${data.embedding_size_bytes} B` : '';
      setMessage(
        `Saved sample ${data.sample_id ?? '?'} (${data.total ?? '?'} in ${data.category ?? derivedCategory}${sizeNote})`,
      );
      void loadReferenceLibrary(data.embedding_size_bytes ?? embedBytes, true);
      void refreshLibrary();
    } catch {
      setMessage('Capture failed — Pi encoder or video stream unavailable');
    } finally {
      setCapturingRef(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex items-center gap-2 min-w-0">
        <Camera size={16} style={{ color: 'var(--accent-purple)', flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          Live Reference Capture
        </span>
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45, margin: 0 }}>
        Save the latest Pi camera embedding into the reference library. Defaults to target bottle — pick another reference or type a new name.
      </p>

      <label className="flex flex-col gap-1">
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>Name</span>
        <input
          list="live-ref-capture-names"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="e.g. target bottle"
          className="rounded-xl px-3 py-2 outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            color: 'var(--text-primary)',
            fontSize: 13,
          }}
        />
        <datalist id="live-ref-capture-names">
          {referenceOptions.map((opt) => (
            <option key={`${opt.category}:${opt.display}`} value={opt.display} />
          ))}
        </datalist>
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
        onClick={() => { void captureReferenceSnapshot(); }}
        disabled={!categoryValid || capturingRef || !encoderLive}
        title={encoderLive ? 'Save latest Pi MQTT embedding' : 'Start VIT.py on the Pi first'}
        className="flex items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 font-semibold"
        style={{
          fontSize: 13,
          border: '1px solid var(--accent-purple)',
          background: 'linear-gradient(135deg, var(--accent-purple), #4c1d95)',
          color: '#fff',
          cursor: !categoryValid || capturingRef || !encoderLive ? 'not-allowed' : 'pointer',
          opacity: !categoryValid || capturingRef || !encoderLive ? 0.5 : 1,
        }}
      >
        {capturingRef ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />}
        {capturingRef ? 'Capturing…' : 'Capture from Pi'}
      </button>

      {message && (
        <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: 0 }}>{message}</p>
      )}

      {!encoderLive && (
        <p style={{ fontSize: 10, color: 'var(--text-muted)', margin: 0 }}>
          Pi encoder offline — start VIT.py on the Pi to capture live embeddings.
        </p>
      )}
    </div>
  );
}

export const liveReferenceCaptureDef = {
  id: 'live_reference_capture_widget',
  name: 'Live Reference Capture',
  group: 'health' as const,
  sizeClass: 'M' as const,
  defaultSize: { w: 2, h: 4, minW: 2, minH: 3 },
  icon: 'Camera',
  pinned: false,
  component: LiveReferenceCaptureWidget,
};
