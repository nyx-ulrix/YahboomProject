// AI Agent page — text command console (mock responses until /api/agent exists).

import { useEffect, useRef, useState } from 'react';
import { Bot, Loader2, Send, Sparkles, User } from 'lucide-react';
import { TopBar } from './TopBar';

interface Message {
  id: number;
  role: 'user' | 'agent';
  content: string;
  ts: string;
}

const STARTER_SUGGESTIONS = [
  'Drive forward 1 metre then stop',
  'Identify all people in view',
  'What is the current battery level?',
  'Return to base',
];

async function runAgent(prompt: string): Promise<string> {
  await new Promise((r) => setTimeout(r, 600 + Math.random() * 600));
  const lower = prompt.toLowerCase();
  if (lower.includes('battery')) return 'Battery is at 87%. Estimated 42 minutes of operation remaining at current load.';
  if (lower.includes('forward') || lower.includes('drive')) return 'Plan: publish linear_x=0.5 to robot/cmd/movement for 2.0s, then STOP.\nAwaiting confirmation. Reply "execute" to dispatch.';
  if (lower.includes('person') || lower.includes('identify')) return 'Detected 1 person (conf 0.92), 1 chair (0.78), 1 door (0.66).\nNo unknown actors in current frame.';
  if (lower.includes('return') || lower.includes('base')) return 'Calculating return path… 6 waypoints, ETA 38 s.\nMission status set to RETURNING TO SAFE STATE.';
  if (lower.includes('stop')) return 'Emergency stop dispatched. linear_x=0, angular_z=0. Mission status: STOPPED.';
  return `Acknowledged: "${prompt}".\nNo deterministic skill matched in the offline model. In production this will be routed to the cloud LLM.`;
}

export function AiAgentPage({ darkMode, toggleDark }: { darkMode: boolean; toggleDark: () => void }) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 1,
      role: 'agent',
      content: 'Hello operator. I can plan, execute, and explain robot actions. Try one of the prompts below or type your own.',
      ts: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  const send = async (text?: string) => {
    const prompt = (text ?? input).trim();
    if (!prompt || busy) return;
    setInput('');
    const now = Date.now();
    setMessages((m) => [...m, { id: now, role: 'user', content: prompt, ts: new Date().toISOString() }]);
    setBusy(true);
    const reply = await runAgent(prompt);
    setMessages((m) => [...m, { id: now + 1, role: 'agent', content: reply, ts: new Date().toISOString() }]);
    setBusy(false);
  };

  return (
    <div className="min-h-screen w-full p-4 sm:p-6" style={{ background: 'var(--bg-app)' }}>
      <div className="app-shell ambient-glow w-full mx-auto p-4 sm:p-6 flex flex-col gap-5"
        style={{ maxWidth: 1600 }}>
        <TopBar darkMode={darkMode} toggleDark={toggleDark} />

        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-2xl flex items-center justify-center"
            style={{
              background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
              boxShadow: '0 0 24px var(--glow-color)',
            }}>
            <Sparkles size={18} style={{ color: '#fff' }} />
          </div>
          <div>
            <h2>AI Agent Console</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 2 }}>
              Issue natural-language commands. The agent plans, dispatches, and reports on robot actions.
            </p>
          </div>
        </div>

        <div className="rounded-3xl flex flex-col"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            height: 'calc(100vh - 320px)',
            minHeight: 420,
          }}>
          {/* Transcript */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">
            {messages.map((m) => (
              <Bubble key={m.id} message={m} />
            ))}
            {busy && (
              <div className="flex items-center gap-2 self-start" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                <Loader2 size={14} className="animate-spin" /> Agent is thinking…
              </div>
            )}
          </div>

          {/* Suggestions (only when conversation is fresh) */}
          {messages.length <= 1 && (
            <div className="px-5 pb-3 flex flex-wrap gap-2">
              {STARTER_SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="pill"
                  style={{
                    background: 'var(--secondary)',
                    border: '1px solid var(--stroke-subtle)',
                    color: 'var(--text-secondary)',
                    fontSize: 12,
                  }}>
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Composer */}
          <form
            onSubmit={(e) => { e.preventDefault(); send(); }}
            className="p-3 flex items-center gap-2"
            style={{ borderTop: '1px solid var(--stroke-subtle)' }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask the agent — e.g. ‘drive forward 1 metre then stop’"
              className="flex-1 px-4 py-3 rounded-xl bg-transparent outline-none"
              style={{
                background: 'var(--input-background)',
                border: '1px solid var(--stroke-subtle)',
                color: 'var(--text-primary)',
                fontSize: 14,
              }}
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="pill flex items-center gap-2"
              style={{
                background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
                color: '#fff', padding: '12px 20px', fontWeight: 600, fontSize: 13,
                boxShadow: '0 6px 20px var(--glow-color)',
                opacity: busy || !input.trim() ? 0.5 : 1,
              }}
            >
              <Send size={13} /> Send
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

function Bubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className="w-8 h-8 rounded-xl flex-none flex items-center justify-center"
        style={{
          background: isUser
            ? 'linear-gradient(135deg, var(--accent-pink), var(--accent-purple))'
            : 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
          color: '#fff',
        }}>
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>
      <div className={`flex flex-col gap-1 max-w-[80%] ${isUser ? 'items-end' : ''}`}>
        <div className="px-4 py-2.5 rounded-2xl"
          style={{
            background: isUser ? 'var(--accent-purple)' : 'var(--bg-elevated)',
            color: isUser ? '#fff' : 'var(--text-primary)',
            border: isUser ? 'none' : '1px solid var(--stroke-subtle)',
            fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap',
          }}>
          {message.content}
        </div>
        <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
          {new Date(message.ts).toLocaleTimeString()}
        </span>
      </div>
    </div>
  );
}
