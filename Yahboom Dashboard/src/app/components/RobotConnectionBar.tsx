// Compact robot MQTT broker IP + Connect — inline in the top nav.
import { useEffect, useState, type KeyboardEvent } from 'react';
import { Eye, EyeOff, Plug } from 'lucide-react';
import { useMetricsStore, useSettingsStore } from '../store';
import type { SettingsStore } from '../store';
import { connectBroker } from '../../lib/Connections';

export function RobotConnectionBar() {
  const brokerIp = useSettingsStore((s: SettingsStore) => s.brokerIp);
  const setBrokerIp = useSettingsStore((s: SettingsStore) => s.setBrokerIp);
  const isConnected = useSettingsStore((s: SettingsStore) => s.isConnected);

  const [draftIp, setDraftIp] = useState(brokerIp);
  const [busy, setBusy] = useState(false);
  const [defaultIp, setDefaultIp] = useState('');
  const [showHost, setShowHost] = useState(false);

  useEffect(() => {
    setDraftIp(brokerIp);
  }, [brokerIp]);

  useEffect(() => {
    fetch('/api/config')
      .then((r) => r.json())
      .then((d: { default_broker_ip: string }) => setDefaultIp(d.default_broker_ip))
      .catch(() => {});
  }, []);

  const onConnect = async () => {
    setBusy(true);
    setBrokerIp(draftIp);
    useMetricsStore.getState().pushEvent('info', `Connecting to broker at ${draftIp}…`, 'mqtt');
    await connectBroker(draftIp);
    setBusy(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && draftIp.trim() && !busy) void onConnect();
  };

  const statusColor = isConnected ? 'var(--state-success)' : 'var(--text-muted)';
  const statusTitle = isConnected ? `Connected · ${brokerIp}` : 'Disconnected';

  return (
    <div
      className="flex items-center gap-1.5 ml-1.5 pl-2"
      style={{ borderLeft: '1px solid var(--stroke-subtle)' }}
    >
      {showHost ? (
        <input
          value={draftIp}
          onChange={(e) => setDraftIp(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={defaultIp || 'raspberrypi.local'}
          spellCheck={false}
          aria-label="Robot IP address or hostname"
          className="w-28 sm:w-36 px-2 py-1 rounded-lg outline-none"
          style={{
            background: 'var(--input-background)',
            border: '1px solid var(--stroke-subtle)',
            color: 'var(--text-primary)',
            fontSize: 11,
            fontFamily: 'monospace',
          }}
        />
      ) : null}

      <button
        type="button"
        onClick={() => setShowHost((v) => !v)}
        title={showHost ? 'Hide IP / hostname' : 'Show IP / hostname'}
        className="pill flex items-center shrink-0"
        style={{
          background: 'transparent',
          color: 'var(--text-muted)',
          border: '1px solid transparent',
          padding: '4px 6px',
          fontSize: 10,
        }}
      >
        {showHost ? <EyeOff size={11} /> : <Eye size={11} />}
      </button>

      <button
        type="button"
        onClick={() => void onConnect()}
        disabled={busy || !draftIp.trim()}
        title={busy ? 'Connecting…' : 'Connect to robot'}
        className="pill flex items-center gap-1 shrink-0"
        style={{
          background: 'var(--bg-elevated)',
          color: 'var(--text-primary)',
          border: '1px solid var(--stroke-subtle)',
          padding: '4px 8px',
          fontWeight: 600,
          fontSize: 10,
          opacity: busy || !draftIp.trim() ? 0.5 : 1,
        }}
      >
        <Plug size={10} />
        {busy ? '…' : 'Connect'}
      </button>

      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        title={statusTitle}
        style={{
          background: statusColor,
          boxShadow: isConnected ? '0 0 6px var(--state-success)' : 'none',
        }}
      />
    </div>
  );
}
