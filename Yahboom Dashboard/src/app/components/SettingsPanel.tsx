// Settings modal — broker IP + Connect action wired to /api/connect via Connections.tsx.
import { useEffect, useState } from 'react';
import { Plug, Server, X } from 'lucide-react';
import { useSettingsStore, useMetricsStore } from '../store';
import type { SettingsStore } from '../store';
import { connectBroker } from '../../lib/Connections';

export function SettingsPanel() {
  const isOpen = useSettingsStore((s: SettingsStore) => s.isOpen);
  const setOpen = useSettingsStore((s: SettingsStore) => s.setOpen);
  const brokerIp = useSettingsStore((s: SettingsStore) => s.brokerIp);
  const setBrokerIp = useSettingsStore((s: SettingsStore) => s.setBrokerIp);
  const isConnected = useSettingsStore((s: SettingsStore) => s.isConnected);

  const [draftIp, setDraftIp] = useState(brokerIp);
  const [busy, setBusy] = useState(false);
  // Fetched from /api/config so the placeholder always mirrors backend/config.py.
  const [defaultIp, setDefaultIp] = useState('');

  // Keep the input field in sync with the store (updated by useConnectionSync).
  useEffect(() => {
    setDraftIp(brokerIp);
  }, [brokerIp]);

  useEffect(() => {
    fetch('/api/config')
      .then((r) => r.json())
      .then((d: { default_broker_ip: string }) => setDefaultIp(d.default_broker_ip))
      .catch(() => {/* backend not running yet — placeholder stays empty */});
  }, []);

  if (!isOpen) return null;

  const onConnect = async () => {
    setBusy(true);
    setBrokerIp(draftIp);
    useMetricsStore.getState().pushEvent('info', `Connecting to broker at ${draftIp}…`, 'mqtt');
    await connectBroker(draftIp);
    setBusy(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'rgba(8,6,24,0.7)', backdropFilter: 'blur(10px)' }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg rounded-3xl flex flex-col overflow-hidden"
        style={{
          background: 'var(--bg-elevated)',
          border: '1px solid var(--stroke-strong)',
          boxShadow: '0 30px 80px rgba(0,0,0,0.5), 0 0 60px var(--glow-color)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5" style={{ borderBottom: '1px solid var(--stroke-subtle)' }}>
          <h2>Settings</h2>
          <button onClick={() => setOpen(false)}
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)', color: 'var(--text-secondary)' }}>
            <X size={14} />
          </button>
        </div>

        <div className="p-5 flex flex-col gap-5">
          {/* ─── Broker connection section ─── */}
          <section className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Server size={14} style={{ color: 'var(--accent-purple)' }} />
              <h3 style={{ margin: 0 }}>Robot Connection</h3>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Enter the IP address or hostname of the robot's MQTT broker. The
              dashboard auto-connects on startup using the saved value.
            </p>

            <label className="flex flex-col gap-1.5 mt-2">
              <span style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                IP address / hostname
              </span>
              <input
                value={draftIp}
                onChange={(e) => setDraftIp(e.target.value)}
                placeholder={defaultIp || 'Loading…'}
                spellCheck={false}
                className="px-3 py-2.5 rounded-xl outline-none"
                style={{
                  background: 'var(--input-background)',
                  border: '1px solid var(--stroke-subtle)',
                  color: 'var(--text-primary)',
                  fontSize: 14, fontFamily: 'monospace',
                }}
              />
            </label>

            <div className="flex items-center justify-between mt-3">
              <div className="flex items-center gap-2" style={{ fontSize: 12 }}>
                <span className="w-2 h-2 rounded-full" style={{
                  background: isConnected ? 'var(--state-success)' : 'var(--text-muted)',
                  boxShadow: isConnected ? '0 0 8px var(--state-success)' : 'none',
                }} />
                <span style={{ color: 'var(--text-secondary)' }}>
                  {isConnected ? `Connected · ${brokerIp}` : 'Disconnected'}
                </span>
              </div>
              <button
                onClick={onConnect}
                disabled={busy || !draftIp}
                className="pill flex items-center gap-2"
                style={{
                  background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
                  color: '#fff', padding: '9px 18px', fontWeight: 600, fontSize: 13,
                  boxShadow: '0 6px 20px var(--glow-color)',
                  opacity: busy ? 0.6 : 1,
                }}
              >
                <Plug size={13} /> {busy ? 'Connecting…' : 'Connect'}
              </button>
            </div>
          </section>

        </div>
      </div>
    </div>
  );
}
