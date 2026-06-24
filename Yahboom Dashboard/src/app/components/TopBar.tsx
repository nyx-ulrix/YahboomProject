// Pinned top bar: nav pills, theme toggle, settings, plus inline status row.
import { useState } from 'react';
import { Antenna, Check, Gamepad2, LayoutGrid, LayoutTemplate, Moon, Play, Radio, Settings, Square, Sun, Timer } from 'lucide-react';
import { LAYOUT_TEMPLATES, useLayoutStore, useMetricsStore, usePickerStore, useSettingsStore, useViewStore } from '../store';
import type { LayoutStore, SettingsStore, ViewStore } from '../store';
import type { MetricsState } from '../types';
import { usePiVitVideoServer } from '../hooks';

export function TopBar({ darkMode, toggleDark }: { darkMode: boolean; toggleDark: () => void }) {
  const connectionStatus = useMetricsStore((s: MetricsState) => s.connectionStatus);
  const networkMode = useMetricsStore((s: MetricsState) => s.networkMode);
  const latencyMs = useMetricsStore((s: MetricsState) => s.latencyMs);
  const openSettings = useSettingsStore((s: SettingsStore) => s.setOpen);
  const togglePicker = usePickerStore((s) => s.toggle);
  const view = useViewStore((s: ViewStore) => s.view);
  const setView = useViewStore((s: ViewStore) => s.setView);
  const connColor = connectionStatus === 'CONNECTED' ? 'var(--state-success)'
    : connectionStatus === 'RECONNECTING' ? 'var(--state-warning)' : 'var(--state-error)';

  return (
    // Lift the whole TopBar above its ambient-glow siblings (heading + grid),
    // which the global `.ambient-glow > * { z-index: 1 }` rule otherwise ties
    // with it — letting the later-painted grid cover dropdowns like the layout
    // template menu. Stays below the z-50 modals (settings / widget picker).
    <div className="flex flex-col gap-3" style={{ position: 'relative', zIndex: 40 }}>
      {/* Row 1: nav + utilities */}
      <div className="flex items-center gap-4">
        <nav className="flex items-center gap-1">
          {([
            { label: 'Dashboard', id: 'dashboard' as const },
            { label: 'AI Agent', id: 'ai_agent' as const },
          ]).map(({ label, id }) => {
            const active = view === id;
            return (
              <button
                key={id}
                onClick={() => setView(id)}
                className="pill"
                style={{
                  background: active ? 'var(--bg-elevated)' : 'transparent',
                  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                  border: active ? '1px solid var(--stroke-subtle)' : '1px solid transparent',
                  boxShadow: active ? '0 0 12px var(--glow-color)' : 'none',
                }}
              >
                {label}
              </button>
            );
          })}
          {/* Controller button — visible on all screen sizes */}
          <button
            onClick={() => setView('controller')}
            className="pill flex items-center gap-1.5"
            style={{
              background: view === 'controller' ? 'var(--bg-elevated)' : 'transparent',
              color: view === 'controller' ? 'var(--accent-cyan)' : 'var(--text-secondary)',
              border: view === 'controller' ? '1px solid var(--accent-cyan)' : '1px solid transparent',
              boxShadow: view === 'controller' ? '0 0 12px rgba(103,232,249,0.35)' : 'none',
            }}
          >
            <Gamepad2 size={13} />
            Controller
          </button>
        </nav>

        <div className="flex-1" />

        <PiVitVideoServerButton />

        <div className="flex items-center gap-2">
          <TemplateMenu />
          <button
            onClick={togglePicker}
            className="w-9 h-9 rounded-xl flex items-center justify-center transition-all hover:scale-105"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)' }}
            title="Widget picker (P)"
          >
            <LayoutGrid size={15} style={{ color: 'var(--accent-purple)' }} />
          </button>
          <button
            onClick={toggleDark}
            className="w-9 h-9 rounded-xl flex items-center justify-center transition-all hover:scale-105"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)' }}
            title="Toggle theme"
          >
            {darkMode ? <Sun size={15} style={{ color: 'var(--accent-gold)' }} /> : <Moon size={15} style={{ color: 'var(--accent-purple)' }} />}
          </button>
          <button
            onClick={() => openSettings(true)}
            className="w-9 h-9 rounded-xl flex items-center justify-center transition-all hover:scale-105"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)' }}
            title="Settings"
          >
            <Settings size={15} style={{ color: 'var(--text-secondary)' }} />
          </button>
        </div>
      </div>

      {/* Row 2: inline status text + keyboard legend */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
          <Radio size={12} style={{ color: connColor }} />
          <span style={{ color: 'var(--text-muted)' }}>Connection:</span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{connectionStatus}</span>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: connColor, boxShadow: `0 0 8px ${connColor}` }} />
        </div>
        <div className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
          <Antenna size={12} style={{ color: 'var(--accent-cyan)' }} />
          <span style={{ color: 'var(--text-muted)' }}>Network:</span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{networkMode ?? '--'}</span>
        </div>
        <div className="flex items-center gap-1.5" style={{ fontSize: 12 }}>
          <Timer size={12} style={{ color: 'var(--accent-cyan)' }} />
          <span style={{ color: 'var(--text-muted)' }}>Latency:</span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 600, fontFamily: 'monospace' }}>
            {latencyMs != null ? latencyMs : '--'}<span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> ms</span>
          </span>
        </div>
        <div className="flex-1 flex items-center justify-end gap-2 flex-wrap" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          <Legend label="Movement" keys={['W','A','S','D']} />
          <Legend label="E-Stop" keys={['X']} />
          <Legend label="Camera" keys={['↑','↓','←','→']} />
          <Legend label="Center Camera" keys={['C']} />
        </div>
      </div>
    </div>  
  );
}

function PiVitVideoServerButton() {
  const { brokerIp, isRunning, isBusy, label, error, start, stop } = usePiVitVideoServer();
  const green = 'var(--state-success)';
  const red = 'var(--state-error)';
  const muted = 'var(--text-muted)';
  const disabled = !brokerIp || isBusy;
  const busyStyle = isBusy;

  const onPress = () => {
    if (disabled) return;
    if (isRunning) void stop();
    else void start();
  };

  return (
    <div className="flex flex-col items-end gap-0.5">
      <button
        type="button"
        onPointerDown={(e) => {
          if (disabled) {
            e.preventDefault();
            return;
          }
          // Block double-click before React re-renders disabled state.
          if (!isRunning && startBlocked) {
            e.preventDefault();
          }
        }}
        onClick={onPress}
        disabled={disabled}
        className="flex items-center gap-1.5 pill"
        title={
          !brokerIp
            ? 'Connect to a robot in Settings first'
            : isBusy
              ? 'Waiting for video and VIT stream…'
              : undefined
        }
        style={{
          padding: '4px 12px',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.05em',
          border: '1px solid',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          background: busyStyle
            ? 'rgba(148,163,184,0.12)'
            : isRunning
              ? 'rgba(244,63,94,0.15)'
              : 'rgba(34,197,94,0.15)',
          borderColor: busyStyle ? muted : isRunning ? red : green,
          color: busyStyle ? muted : isRunning ? red : green,
          transition: 'all 0.15s',
        }}
      >
        {!isBusy && (isRunning
          ? <Square size={11} fill="currentColor" />
          : <Play size={11} fill="currentColor" />)}
        {label}
      </button>
      {error && (
        <span style={{ fontSize: 10, color: red, maxWidth: 280, textAlign: 'right' }}>
          {error}
        </span>
      )}
    </div>
  );
}

function TemplateMenu() {
  const applyTemplate = useLayoutStore((s: LayoutStore) => s.applyTemplate);
  const activeTemplateId = useLayoutStore((s: LayoutStore) => s.activeTemplateId);
  const [open, setOpen] = useState(false);

  // When open, lift the whole wrapper into its own elevated stacking context so
  // the menu always sits above the dashboard grid widgets below it.
  return (
    <div className="relative" style={{ zIndex: open ? 60 : undefined }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-9 h-9 rounded-xl flex items-center justify-center transition-all hover:scale-105"
        style={{
          background: 'var(--bg-surface)',
          border: `1px solid ${open ? 'var(--accent-cyan)' : 'var(--stroke-subtle)'}`,
          boxShadow: open ? '0 0 12px rgba(103,232,249,0.35)' : 'none',
        }}
        title="Layout templates"
      >
        <LayoutTemplate size={15} style={{ color: 'var(--accent-cyan)', pointerEvents: 'none' }} />
      </button>

      {open && (
        <>
          {/* Full-screen backdrop: catches outside clicks and guarantees nothing
              behind the menu can intercept clicks meant for it. */}
          <div
            className="fixed inset-0"
            style={{ zIndex: 55 }}
            onClick={() => setOpen(false)}
          />
          <div
            className="absolute right-0 mt-2 rounded-2xl overflow-hidden"
            style={{
              zIndex: 60,
              minWidth: 200,
              background: 'var(--bg-elevated)',
              border: '1px solid var(--stroke-strong)',
              boxShadow: '0 20px 50px rgba(0,0,0,0.45), 0 0 40px var(--glow-color)',
            }}
          >
            <div className="px-3 py-2" style={{ borderBottom: '1px solid var(--stroke-subtle)' }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                Layout Templates
              </span>
            </div>
            {LAYOUT_TEMPLATES.map((t) => {
              const active = t.id === activeTemplateId;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => { applyTemplate(t.id); setOpen(false); }}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left transition-colors"
                  style={{
                    background: active ? 'var(--secondary)' : 'transparent',
                    color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontSize: 13,
                    fontWeight: active ? 600 : 500,
                  }}
                  onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--bg-surface)'; }}
                  onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
                >
                  <span>{t.name}</span>
                  {active && <Check size={14} style={{ color: 'var(--accent-cyan)' }} />}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function Legend({ label, keys }: { label: string; keys: string[] }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg"
      style={{ background: 'var(--secondary)', border: '1px solid var(--stroke-subtle)' }}>
      <span style={{ color: 'var(--text-secondary)' }}>{label}:</span>
      {keys.map((k) => ( 
        <kbd key={k} style={{
          fontSize: 10, padding: '1px 5px', borderRadius: 3, fontFamily: 'monospace',
          background: 'var(--bg-elevated)', color: 'var(--text-primary)',
          border: '1px solid var(--stroke-subtle)',
        }}>{k}</kbd>
      ))}
    </div>
  );
}