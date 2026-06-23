// Spacebar-activated widget picker. Filterable by group tab, click-to-add.
import { useMemo, useState, type ComponentType } from 'react';
import { Search, X, Plus, Check } from 'lucide-react';
import * as Lucide from 'lucide-react';
import { useLayoutStore, usePickerStore } from '../store';
import type { LayoutStore, PickerStore } from '../store';
import { WIDGET_REGISTRY } from './Widgets';
import type { WidgetGroup } from '../types';

// Only include groups that have at least one widget in the registry.
const GROUPS: Array<'all' | WidgetGroup> = [
  'all', 'connectivity', 'video', 'health', 'control', 'logging',
];

export function WidgetPickerOverlay() {
  const isOpen = usePickerStore((s: PickerStore) => s.isOpen);
  const setOpen = usePickerStore((s: PickerStore) => s.setOpen);
  const activeIds = useLayoutStore((s: LayoutStore) => s.activeWidgetIds);
  const addWidget = useLayoutStore((s: LayoutStore) => s.addWidget);

  const [tab, setTab] = useState<'all' | WidgetGroup>('all');
  const [q, setQ] = useState('');

  const filtered = useMemo(() => {
    return WIDGET_REGISTRY.filter((w) => {
      if (w.pinned) return false; // pinned widgets live in the TopBar, not the grid
      if (tab !== 'all' && w.group !== tab) return false;
      if (q && !`${w.name} ${w.group}`.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
  }, [tab, q]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-8"
      style={{ background: 'rgba(8, 6, 24, 0.7)', backdropFilter: 'blur(12px)' }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-4xl max-h-[80vh] flex flex-col rounded-3xl overflow-hidden"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--stroke-strong)',
          boxShadow: '0 30px 80px rgba(0,0,0,0.5), 0 0 80px var(--glow-color)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 p-5 border-b" style={{ borderColor: 'var(--stroke-subtle)' }}>
          <div className="flex-1 flex items-center gap-2 pill"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)', padding: '10px 16px' }}>
            <Search size={15} style={{ color: 'var(--text-muted)' }} />
            <input
              placeholder="Search widgets…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="flex-1 bg-transparent outline-none"
              style={{ color: 'var(--text-primary)', fontSize: 14 }}
            />
            <kbd style={{ fontSize: 10, color: 'var(--text-muted)' }}>ESC to close</kbd>
          </div>
          <button onClick={() => setOpen(false)}
            className="w-9 h-9 rounded-xl flex items-center justify-center"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)', color: 'var(--text-secondary)' }}>
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-1.5 px-5 py-3 overflow-x-auto" style={{ borderBottom: '1px solid var(--stroke-subtle)' }}>
          {GROUPS.map((g) => (
            <button
              key={g}
              onClick={() => setTab(g)}
              className="pill capitalize whitespace-nowrap"
              style={{
                background: tab === g ? 'var(--accent-purple)' : 'var(--bg-surface)',
                color: tab === g ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--stroke-subtle)',
                boxShadow: tab === g ? '0 0 16px var(--glow-color)' : 'none',
              }}
            >
              {g}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {filtered.map((w) => {
              const Icon = ((Lucide as unknown) as Record<string, ComponentType<{ size?: number }>>)[w.icon] ?? Lucide.Box;
              const isActive = activeIds.includes(w.id);
              return (
                <button
                  key={w.id}
                  disabled={isActive}
                  onClick={() => { if (!isActive) { addWidget(w); setOpen(false); } }}
                  onDoubleClick={() => { if (!isActive) { addWidget(w); setOpen(false); } }}
                  className="text-left p-4 rounded-2xl transition-all hover:-translate-y-0.5"
                  style={{
                    background: isActive ? 'var(--secondary)' : 'var(--bg-surface)',
                    border: `1px solid ${isActive ? 'var(--stroke-subtle)' : 'var(--stroke-strong)'}`,
                    opacity: isActive ? 0.5 : 1,
                    cursor: isActive ? 'not-allowed' : 'pointer',
                  }}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="w-9 h-9 rounded-xl flex items-center justify-center"
                      style={{ background: 'rgba(139,92,246,0.16)', color: 'var(--accent-purple)' }}>
                      <Icon size={16} />
                    </div>
                    {isActive
                      ? <Check size={14} style={{ color: 'var(--state-success)' }} />
                      : <Plus size={14} style={{ color: 'var(--text-muted)' }} />}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{w.name}</div>
                  <div className="capitalize" style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    {w.group} · {w.sizeClass}
                  </div>
                </button>
              );
            })}
          </div>
          {filtered.length === 0 && (
            <div className="py-16 text-center" style={{ color: 'var(--text-muted)' }}>No widgets match.</div>
          )}
        </div>
      </div>
    </div>
  );
}