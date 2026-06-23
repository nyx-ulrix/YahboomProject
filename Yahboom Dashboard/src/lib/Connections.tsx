import { useMetricsStore, useSettingsStore } from '../app/store';

export async function connectBroker(ip: string): Promise<void> {
  try {
    const res = await fetch('/api/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip }),
    });
    const data: { status: string; message: string; broker_ip: string } = await res.json();

    const ok = data.status === 'connected';
    useSettingsStore.getState().setConnected(ok);
    useMetricsStore.setState({
      connectionStatus: ok ? 'CONNECTED' : 'DISCONNECTED',
      mqttLinkStatus: ok ? 'CONNECTED' : 'DISCONNECTED',
    });
    // Backend already logs the connection result — it will arrive via the next poll.
  } catch {
    useSettingsStore.getState().setConnected(false);
    useMetricsStore.setState({ connectionStatus: 'DISCONNECTED', mqttLinkStatus: 'DISCONNECTED' });
    useMetricsStore.getState().pushEvent('error', 'Cannot reach Flask backend');
  }
}
