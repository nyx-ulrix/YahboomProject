// Login page — mock auth via useAuthStore. Wire Supabase in submitEmail/googleSignIn.

import { useState, type FormEvent } from 'react';
import { Eye, EyeOff, Loader2, Lock, Mail, Moon, Sun } from 'lucide-react';
import { useAuthStore } from '../store';
import type { AuthStore } from '../store';

export function LoginPage({ darkMode, toggleDark }: { darkMode: boolean; toggleDark: () => void }) {
  const signIn = useAuthStore((s: AuthStore) => s.signIn);
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState<null | 'email' | 'google'>(null);
  const [error, setError] = useState<string | null>(null);

  const submitEmail = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return setError('Enter a valid email address.');
    if (password.length < 8) return setError('Password must be at least 8 characters.');
    setLoading('email');
    await new Promise((r) => setTimeout(r, 700));
    signIn(email);
    setLoading(null);
  };

  const googleSignIn = async () => {
    setError(null);
    setLoading('google');
    await new Promise((r) => setTimeout(r, 700));
    signIn('operator@edgeops.io', 'Operator');
    setLoading(null);
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center p-6 relative ambient-glow"
      style={{ background: 'var(--bg-app)' }}>
      {/* Theme toggle */}
      <button
        onClick={toggleDark}
        className="absolute top-6 right-6 w-10 h-10 rounded-xl flex items-center justify-center transition-all hover:scale-105"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)', backdropFilter: 'blur(20px)' }}
      >
        {darkMode ? <Sun size={16} style={{ color: 'var(--accent-gold)' }} /> : <Moon size={16} style={{ color: 'var(--accent-purple)' }} />}
      </button>

      <div className="w-full max-w-md flex flex-col gap-6">
        {/* Brand */}
        <div className="text-center flex flex-col items-center gap-3">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{
              background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
              boxShadow: '0 0 40px var(--glow-color)',
            }}>
            <span style={{ color: '#fff', fontWeight: 700, fontSize: 24 }}>◈</span>
          </div>
          <div>
            <h1 style={{ letterSpacing: '-0.02em' }}>EdgeOps Console</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>
              5G command-and-control for Yahboom robotics
            </p>
          </div>
        </div>

        {/* Card */}
        <div className="p-7 rounded-3xl flex flex-col gap-5"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            backdropFilter: 'blur(24px)',
            WebkitBackdropFilter: 'blur(24px)',
            boxShadow: '0 20px 60px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.06)',
          }}>
          {/* SECTION 1: Google OAuth -------------------------------------- */}
          <button
            onClick={googleSignIn}
            disabled={loading !== null}
            className="w-full flex items-center justify-center gap-3 py-3 rounded-xl transition-all hover:-translate-y-0.5"
            style={{
              background: '#fff', color: '#1f1f1f',
              fontWeight: 600, fontSize: 14,
              border: '1px solid var(--stroke-subtle)',
              boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
              opacity: loading === 'google' ? 0.7 : 1,
            }}
          >
            {loading === 'google' ? <Loader2 size={16} className="animate-spin" /> : <GoogleLogo />}
            Continue with Google
          </button>

          {/* divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px" style={{ background: 'var(--stroke-subtle)' }} />
            <span style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.1em' }}>OR</span>
            <div className="flex-1 h-px" style={{ background: 'var(--stroke-subtle)' }} />
          </div>

          {/* SECTION 2: Email + password ---------------------------------- */}
          <form onSubmit={submitEmail} className="flex flex-col gap-3">
            <Field icon={Mail} label="Email">
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="operator@edgeops.io"
                className="w-full bg-transparent outline-none"
                style={{ color: 'var(--text-primary)', fontSize: 14 }}
              />
            </Field>
            <Field icon={Lock} label="Password">
              <input
                type={showPw ? 'text' : 'password'}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="flex-1 bg-transparent outline-none"
                style={{ color: 'var(--text-primary)', fontSize: 14 }}
              />
              <button type="button" onClick={() => setShowPw((v) => !v)}
                style={{ color: 'var(--text-muted)' }}>
                {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </Field>

            {error && (
              <div className="px-3 py-2 rounded-lg" style={{ background: 'rgba(244,63,94,0.1)', color: 'var(--state-error)', fontSize: 12 }}>
                {error}
              </div>
            )}

            {mode === 'signin' && (
              <div className="flex items-center justify-between" style={{ fontSize: 12 }}>
                <label className="flex items-center gap-2" style={{ color: 'var(--text-secondary)' }}>
                  <input type="checkbox" /> Remember me
                </label>
                <button type="button" style={{ color: 'var(--accent-purple)' }}>Forgot password?</button>
              </div>
            )}

            <button
              type="submit"
              disabled={loading !== null}
              className="w-full py-3 rounded-xl transition-all hover:-translate-y-0.5 mt-1"
              style={{
                background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
                color: '#fff', fontWeight: 600, fontSize: 14, letterSpacing: '0.01em',
                boxShadow: '0 8px 24px var(--glow-color)',
                opacity: loading === 'email' ? 0.7 : 1,
              }}
            >
              {loading === 'email' ? (
                <span className="inline-flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Signing in…</span>
              ) : (mode === 'signin' ? 'Sign in' : 'Create account')}
            </button>
          </form>

          {/* SECTION 3: Mode toggle (sign-in <-> sign-up) ----------------- */}
          <div className="text-center" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {mode === 'signin' ? 'New here?' : 'Already have an account?'}{' '}
            <button onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
              style={{ color: 'var(--accent-purple)', fontWeight: 600 }}>
              {mode === 'signin' ? 'Create an account' : 'Sign in'}
            </button>
          </div>
        </div>

        <p className="text-center" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          By continuing you agree to our Terms · Privacy · Data handling policy
        </p>
      </div>
    </div>
  );
}

function Field({ icon: Icon, label, children }: { icon: typeof Mail; label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span style={{ fontSize: 11, color: 'var(--text-muted)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>{label}</span>
      <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl"
        style={{ background: 'var(--input-background)', border: '1px solid var(--stroke-subtle)' }}>
        <Icon size={14} style={{ color: 'var(--text-muted)' }} />
        {children}
      </div>
    </label>
  );
}

function GoogleLogo() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.1 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34.5 6.1 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.4 0 10.3-2.1 14-5.4l-6.5-5.3c-2 1.4-4.5 2.7-7.5 2.7-5.2 0-9.6-3.3-11.2-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.3-4.3 5.7l6.5 5.3c-.5.5 7.2-5.2 7.2-15 0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
