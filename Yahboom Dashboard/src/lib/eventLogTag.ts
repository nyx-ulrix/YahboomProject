/** Event log row — backend may include `tag` (MQTT topic or source). */
export type EventLogEntry = {
  id: number;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  tag?: string;
};

/** Infer MQTT topic / source tag from message text when backend omitted `tag`. */
export function inferEventTag(ev: Pick<EventLogEntry, 'tag' | 'message'>): string | null {
  if (ev.tag?.trim()) return ev.tag.trim();

  const mqtt = ev.message.match(/MQTT\s+[<-]+\s+(\S+?):/);
  if (mqtt?.[1]) return mqtt[1];

  const post = ev.message.match(/POST\s+->\s+([^:]+):/);
  if (post?.[1]) return post[1].trim();

  if (/^LiDAR E-stop/i.test(ev.message)) return 'yahboom/safety/status';
  if (/^Edge Stop/i.test(ev.message)) return 'yahboom/vit/status';
  if (/^Connecting to broker/i.test(ev.message)) return 'mqtt';
  if (/^Emergency stop/i.test(ev.message)) return 'estop';
  if (/^Mission bench:|^Mission test/i.test(ev.message)) return 'test-bench';
  if (/^Cannot reach Flask/i.test(ev.message)) return 'api';
  if (/^Backend started/i.test(ev.message)) return 'system';

  return null;
}

/** Short label for narrow event-log columns. */
export function shortEventTag(tag: string): string {
  const parts = tag.split('/');
  if (parts.length <= 2) return tag;
  return `${parts[0]}/…/${parts[parts.length - 1]}`;
}
