// Touch-optimised full-screen controller for iPad and below.

import { useEffect, useRef, useState } from 'react';
import {
  Activity, Camera, Gamepad2, Joystick,
  Moon, Octagon, Signal, Sun, Timer,
} from 'lucide-react';
import { useMetricsStore, useViewStore } from '../store';
import type { ViewStore } from '../store';
import type { MetricsState } from '../types';
import {
  useConnectionSync, useDriveStatusPoll, useCloudAwareStopLabelEstop, useYoloBottleStop, useGlobalShortcuts,
  useGridStatusPoll, useKeyboardCamera, useKeyboardMovement, useSafetyStatusPoll,
} from '../hooks';
import { VideoFeedCore } from '../../lib/VideoFeed';
import { sendCommand, sendCameraCommand, setEstopState, vecToCommand, vecToCameraCommand, type BotCommand, type CameraCommand } from '../../lib/Controls';

// Inline joystick (no grid wrapper)
function TouchJoystick({
  label,
  accentColor = 'var(--accent-purple)',
  externalVec,
  onChange,
  onDoubleTap,
}: {
  label: string;
  accentColor?: string;
  externalVec?: { x: number; y: number } | null;
  onChange: (v: { x: number; y: number; released?: boolean }) => void;
  onDoubleTap?: () => void;
}) {
  const [vec, setVec] = useState({ x: 0, y: 0 });
  const padRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const lastTapRef = useRef(0);
  const [size, setSize] = useState(0);

  // Local drag always wins. External vec (keyboard / remote client) shows only when not dragging.
  const display = (!draggingRef.current && externalVec && (externalVec.x !== 0 || externalVec.y !== 0))
    ? externalVec
    : vec;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize(Math.min(width, height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const updateFromPointer = (e: PointerEvent | React.PointerEvent) => {
    const pad = padRef.current;
    if (!pad) return;
    const rect = pad.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const r = Math.min(rect.width, rect.height) / 2 - 20;
    let dx = (e as PointerEvent).clientX - cx;
    let dy = (e as PointerEvent).clientY - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > r) { dx = (dx / dist) * r; dy = (dy / dist) * r; }
    const nx = dx / r;
    const ny = -dy / r;
    onChange({ x: nx, y: ny });
    setVec({ x: nx, y: ny });
  };

  return (
    <div className="flex flex-col items-center gap-2 h-full min-h-0">
      {/* Label */}
      <div
        className="flex items-center gap-1.5 px-3 py-1 rounded-full flex-shrink-0"
        style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid var(--stroke-subtle)',
          fontSize: 11,
          color: 'var(--text-muted)',
          letterSpacing: '0.08em',
        }}
      >
        <Joystick size={11} style={{ color: accentColor }} />
        <span className="uppercase tracking-widest">{label}</span>
      </div>

      {/* Pad container — fills available height, constrains to square */}
      <div ref={containerRef} className="flex-1 w-full flex items-center justify-center min-h-0">
        <div
          ref={padRef}
          className="relative rounded-full select-none touch-none flex-shrink-0"
          style={{
            width: size || undefined,
            height: size || undefined,
            ...(size === 0 ? { width: '100%', aspectRatio: '1 / 1' } : {}),
            background: `radial-gradient(circle at 35% 35%, ${accentColor}18, rgba(0,0,0,0.25))`,
            border: `1.5px solid ${accentColor}44`,
            boxShadow: `inset 0 2px 24px rgba(0,0,0,0.5), 0 0 32px ${accentColor}22`,
          }}
          onPointerDown={(e) => {
            const now = Date.now();
            if (onDoubleTap && now - lastTapRef.current < 300) {
              lastTapRef.current = 0;
              onDoubleTap();
              return;
            }
            lastTapRef.current = now;
            draggingRef.current = true;
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            updateFromPointer(e);
          }}
          onPointerMove={(e) => { if (draggingRef.current) updateFromPointer(e); }}
          onPointerUp={() => { draggingRef.current = false; onChange({ x: 0, y: 0, released: true }); setVec({ x: 0, y: 0 }); }}
          onPointerCancel={() => { draggingRef.current = false; onChange({ x: 0, y: 0, released: true }); setVec({ x: 0, y: 0 }); }}
        >
          {/* Rings */}
          <div className="absolute inset-[22%] rounded-full" style={{ border: `1px solid ${accentColor}20` }} />
          <div className="absolute inset-[44%] rounded-full" style={{ border: `1px solid ${accentColor}30` }} />
          {/* Crosshairs */}
          <div className="absolute top-1/2 left-4 right-4 h-px -translate-y-1/2" style={{ background: `${accentColor}22` }} />
          <div className="absolute left-1/2 top-4 bottom-4 w-px -translate-x-1/2" style={{ background: `${accentColor}22` }} />
          {/* Thumb */}
          <div
            className="absolute rounded-full pointer-events-none"
            style={{
              width: '30%',
              aspectRatio: '1 / 1',
              top: '50%',
              left: '50%',
              transform: `translate(calc(-50% + ${display.x * 33}%), calc(-50% + ${-display.y * 33}%))`,
              background: `linear-gradient(135deg, ${accentColor}, var(--accent-cyan))`,
              boxShadow: `0 4px 24px ${accentColor}66, inset 0 2px 6px rgba(255,255,255,0.35)`,
              transition: draggingRef.current ? 'none' : 'transform 180ms ease-out',
            }}
          />
          {/* Direction ghost labels */}
          <span className="absolute top-2 left-1/2 -translate-x-1/2" style={{ fontSize: 9, color: `${accentColor}55`, fontFamily: 'monospace' }}>▲</span>
          <span className="absolute bottom-2 left-1/2 -translate-x-1/2" style={{ fontSize: 9, color: `${accentColor}55`, fontFamily: 'monospace' }}>▼</span>
          <span className="absolute left-2 top-1/2 -translate-y-1/2" style={{ fontSize: 9, color: `${accentColor}55`, fontFamily: 'monospace' }}>◀</span>
          <span className="absolute right-2 top-1/2 -translate-y-1/2" style={{ fontSize: 9, color: `${accentColor}55`, fontFamily: 'monospace' }}>▶</span>
        </div>
      </div>
    </div>
  );
}

// Mini video feed (16:9, fills container)
function MiniVideoFeed() {
  const fps   = useMetricsStore((s: MetricsState) => s.videoFps);
  const delay = useMetricsStore((s: MetricsState) => s.videoDelayMs);

  return (
    <VideoFeedCore
      compact
      className="rounded-2xl"
      style={{ border: '1px solid var(--stroke-subtle)' }}
    >
      {/* Crosshair */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
        <div className="w-8 h-px" style={{ background: 'rgba(103,232,249,0.55)' }} />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-px h-8"
          style={{ background: 'rgba(103,232,249,0.55)' }} />
      </div>

      {/* HUD overlays */}
      <div className="absolute top-2 left-3 flex items-center gap-2 pointer-events-none">
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-full"
          style={{ background: 'rgba(244,63,94,0.2)', border: '1px solid rgba(244,63,94,0.4)', fontSize: 9 }}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--state-error)' }} />
          <span style={{ color: 'var(--state-error)', fontWeight: 700 }}>LIVE</span>
        </span>
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-full"
          style={{ background: 'rgba(0,0,0,0.4)', border: '1px solid var(--stroke-subtle)', fontSize: 9, color: 'var(--text-secondary)' }}>
          <Activity size={8} style={{ color: 'var(--accent-cyan)' }} /> {fps ?? '--'} fps
        </span>
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-full"
          style={{ background: 'rgba(0,0,0,0.4)', border: '1px solid var(--stroke-subtle)', fontSize: 9, color: 'var(--text-secondary)' }}>
          <Timer size={8} style={{ color: 'var(--accent-pink)' }} /> {delay ?? '--'} ms
        </span>
      </div>

      {/* Corner bracket decorations */}
      {([
        'top-0 left-0 border-t-2 border-l-2 rounded-tl-2xl',
        'top-0 right-0 border-t-2 border-r-2 rounded-tr-2xl',
        'bottom-0 left-0 border-b-2 border-l-2 rounded-bl-2xl',
        'bottom-0 right-0 border-b-2 border-r-2 rounded-br-2xl',
      ] as const).map((cls, i) => (
        <div key={i} className={`absolute w-6 h-6 ${cls} pointer-events-none`}
          style={{ borderColor: 'rgba(103,232,249,0.4)' }} />
      ))}
    </VideoFeedCore>
  );
}

// Emergency Stop
function EStop() {
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  // Tracks whether onPointerDown just fired an engage so the subsequent onClick
  // on the same press cycle is suppressed and does not immediately resume.
  const justEngagedRef = useRef(false);

  // Engage on pointerDown for fastest possible response.
  const handleEngage = () => {
    if (estopActive) return;
    justEngagedRef.current = true;
    void setEstopState(true);
  };

  // Resume requires a complete click cycle to prevent accidental dismissal on swipe.
  // Also guards against the onClick that always follows the engage onPointerDown.
  const handleResume = () => {
    if (justEngagedRef.current) {
      justEngagedRef.current = false;
      return;
    }
    if (!estopActive) return;
    void setEstopState(false);
  };

  return (
    <button
      onPointerDown={handleEngage}
      onClick={handleResume}
      className="flex flex-col items-center justify-center gap-2 rounded-2xl w-full h-full transition-all select-none touch-none"
      style={{
        background: estopActive
          ? 'linear-gradient(145deg, #f59e0b, #92400e)'
          : 'linear-gradient(145deg, #dc2626, #7f1d1d)',
        border: estopActive
          ? '2px solid #fbbf24'
          : '2px solid rgba(244,63,94,0.6)',
        boxShadow: estopActive
          ? '0 0 48px rgba(251,191,36,0.8), inset 0 2px 8px rgba(255,255,255,0.3)'
          : '0 8px 32px rgba(244,63,94,0.5), inset 0 2px 6px rgba(255,255,255,0.2)',
        minHeight: 64,
        minWidth: 64,
      }}
      title={estopActive ? 'E-Stop active — tap to resume' : 'Emergency stop'}
    >
      <Octagon
        size={28}
        fill="white"
        style={{ color: 'white', filter: 'drop-shadow(0 2px 4px rgba(0,0,0,0.4))' }}
      />
      <span style={{
        fontSize: 10, fontWeight: 800, color: '#fff',
        letterSpacing: '0.1em', textShadow: '0 1px 3px rgba(0,0,0,0.5)',
      }}>
        {estopActive ? 'RESUME' : 'E-STOP'}
      </span>
    </button>
  );
}

// Mini status bar
function MiniStatusBar({ cameraCmd }: { cameraCmd: CameraCommand | null }) {
  const conn = useMetricsStore((s: MetricsState) => s.connectionStatus);
  const mqtt = useMetricsStore((s: MetricsState) => s.mqttLinkStatus);
  const ros2 = useMetricsStore((s: MetricsState) => s.ros2BridgeStatus);
  const network = useMetricsStore((s: MetricsState) => s.networkMode);
  const latency = useMetricsStore((s: MetricsState) => s.latencyMs);
  const battery = useMetricsStore((s: MetricsState) => s.batteryPercent);
  const cmd = useMetricsStore((s: MetricsState) => s.currentCommand);

  const connColor = conn === 'CONNECTED' ? 'var(--state-success)'
    : conn === 'RECONNECTING' ? 'var(--state-warning)' : 'var(--state-error)';
  const mqttColor = mqtt === 'CONNECTED' ? 'var(--state-success)' : 'var(--state-error)';
  const ros2Color = ros2 === 'ACTIVE' ? 'var(--state-success)' : 'var(--text-muted)';
  const battColor = battery == null ? 'var(--text-muted)'
    : battery > 40 ? 'var(--state-success)' : battery > 15 ? 'var(--state-warning)' : 'var(--state-error)';

  return (
    <div
      className="flex items-center gap-2 px-3 py-1.5 rounded-xl overflow-hidden flex-wrap"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--stroke-subtle)', fontSize: 10 }}
    >
      <Dot color={connColor} label={conn} />
      <Sep />
      <span style={{ color: 'var(--text-muted)' }}>MQTT:</span>
      <Dot color={mqttColor} label={mqtt} />
      <Sep />
      <span style={{ color: 'var(--text-muted)' }}>ROS2:</span>
      <Dot color={ros2Color} label={ros2 ?? 'WIP'} />
      <Sep />
      <Signal size={9} style={{ color: 'var(--accent-cyan)' }} />
      <span style={{ color: network ? 'var(--text-primary)' : 'var(--text-muted)', fontWeight: 600 }}>{network ?? 'WIP'}</span>
      <Sep />
      <Timer size={9} style={{ color: 'var(--text-muted)' }} />
      <span style={{ color: latency != null ? 'var(--text-primary)' : 'var(--text-muted)', fontFamily: 'monospace' }}>
        {latency != null ? `${latency}ms` : 'WIP'}
      </span>
      <Sep />
      <span style={{ color: 'var(--text-muted)' }}>BAT:</span>
      <span style={{ color: battColor, fontWeight: 600 }}>
        {battery != null ? `${battery.toFixed(0)}%` : 'WIP'}
      </span>
      <Sep />
      <span style={{ color: 'var(--text-muted)' }}>CMD:</span>
      <span style={{ color: 'var(--accent-purple)', fontWeight: 700 }}>{cmd}</span>
      <Sep />
      <Camera size={9} style={{ color: 'var(--accent-cyan)' }} />
      <span style={{ color: 'var(--text-muted)' }}>CAM:</span>
      <span style={{
        color: cameraCmd ? 'var(--accent-cyan)' : 'var(--text-muted)',
        fontWeight: 700,
        fontFamily: 'monospace',
      }}>
        {cameraCmd ?? 'idle'}
      </span>
    </div>
  );
}
function Dot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: color, boxShadow: `0 0 5px ${color}` }} />
      <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>{label}</span>
    </div>
  );
}
function Sep() {
  return <span style={{ color: 'var(--stroke-subtle)', userSelect: 'none' }}>·</span>;
}

// Main ControllerPage
export function ControllerPage({
  darkMode, toggleDark,
}: { darkMode: boolean; toggleDark: () => void }) {
  const setView = useViewStore((s: ViewStore) => s.setView);
  const view = useViewStore((s: ViewStore) => s.view);
  const kbdCam = useMetricsStore((s: MetricsState) => s.cameraKeyboardVec);
  const [camCmd, setCamCmd]   = useState<CameraCommand | null>(null);
  const lastCmdRef    = useRef<BotCommand | null>(null);
  const lastCamCmdRef = useRef<ReturnType<typeof vecToCameraCommand>>(null);
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  const movementVec = useMetricsStore((s: MetricsState) => s.movementVec);

  // Reset lastCmdRef when estop is cleared so the first joystick move always fires.
  useEffect(() => {
    if (!estopActive) lastCmdRef.current = null;
  }, [estopActive]);

  useConnectionSync();
  useCloudAwareStopLabelEstop();
  useYoloBottleStop();
  useSafetyStatusPoll();
  useGridStatusPoll();
  useDriveStatusPoll();
  useKeyboardMovement();
  useKeyboardCamera();
  useGlobalShortcuts();

  return (
    <div
      className="h-screen w-screen flex flex-col overflow-hidden"
      style={{ background: 'var(--bg-app)' }}
    >
      {/* ── Compact header ──────────────────────────────────────────────── */}
      <header
        className="flex-shrink-0 flex items-center gap-2 px-3 py-2"
        style={{ background: 'var(--bg-surface)', borderBottom: '1px solid var(--stroke-subtle)' }}
      >
        {/* Nav pills */}
        <nav className="flex items-center gap-1">
          {([
            { label: 'Dashboard', id: 'dashboard' as const },
          ]).map(({ label, id }) => (
            <button
              key={id}
              onClick={() => setView(id)}
              className="pill"
              style={{
                background: 'transparent',
                color: 'var(--text-secondary)',
                border: '1px solid transparent',
                fontSize: 11,
                padding: '4px 10px',
              }}
            >
              {label}
            </button>
          ))}
          <button
            className="pill flex items-center gap-1.5"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--accent-cyan)',
              border: '1px solid var(--accent-cyan)',
              boxShadow: '0 0 12px rgba(103,232,249,0.3)',
              fontSize: 11,
              padding: '4px 10px',
            }}
          >
            <Gamepad2 size={11} />
            Controller
          </button>
        </nav>

        <div className="flex-1" />

        {/* Right-side utilities */}
        <button
          onClick={toggleDark}
          className="w-8 h-8 rounded-xl flex items-center justify-center"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--stroke-subtle)' }}
        >
          {darkMode
            ? <Sun size={13} style={{ color: 'var(--accent-gold)' }} />
            : <Moon size={13} style={{ color: 'var(--accent-purple)' }} />}
        </button>
      </header>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col gap-2 p-2 min-h-0 overflow-hidden">

        {/* 16:9 Video — width-driven, fills row */}
        <div
          className="flex-shrink-0 w-full overflow-hidden rounded-2xl"
          style={{ aspectRatio: '16 / 9', maxHeight: '42dvh' }}
        >
          <MiniVideoFeed />
        </div>

        {/* Status bar */}
        <div className="flex-shrink-0">
          <MiniStatusBar cameraCmd={camCmd ?? vecToCameraCommand(kbdCam.x, kbdCam.y)} />
        </div>

        {/* Controls row: [Movement] [E-STOP col] [Camera] */}
        <div className="flex-1 flex items-stretch gap-2 min-h-0 overflow-hidden">

          {/* Camera joystick */}
          <div className="flex-1 min-w-0 min-h-0">
            <TouchJoystick
              label="Camera"
              accentColor="var(--accent-cyan)"
              externalVec={kbdCam}
              onDoubleTap={() => sendCameraCommand('crst')}
              onChange={({ x, y, released }) => {
                const cmd = vecToCameraCommand(x, y);
                setCamCmd(cmd);
                if (cmd !== null && cmd !== lastCamCmdRef.current) {
                  lastCamCmdRef.current = cmd;
                  sendCameraCommand(cmd);
                } else if (cmd === null) {
                  if (lastCamCmdRef.current !== null || released) sendCameraCommand('cstop');
                  lastCamCmdRef.current = null;
                }
              }}
            />
          </div>

          {/* Centre column: E-STOP */}
          <div
            className="flex-shrink-0 flex flex-col items-center justify-center gap-2"
            style={{ width: 90 }}
          >
            <div className="w-full flex-1 max-h-36">
              <EStop />
            </div>
            <div
              className="text-center px-2 py-1 rounded-lg"
              style={{ background: 'var(--secondary)', border: '1px solid var(--stroke-subtle)', fontSize: 9, color: 'var(--text-muted)' }}
            >
              Hold <kbd style={{ fontSize: 8, fontFamily: 'monospace', padding: '1px 3px', background: 'var(--bg-elevated)', borderRadius: 2, border: '1px solid var(--stroke-subtle)', color: 'var(--text-primary)' }}>X</kbd> on keyboard
            </div>
          </div>
            {/* Movement joystick */}
          <div className="flex-1 min-w-0 min-h-0">
            <TouchJoystick
              label="Movement"
              accentColor="var(--accent-purple)"
              externalVec={movementVec}
              onChange={({ x, y, released }) => {
                const cmd = vecToCommand(y, -x);
                // Only send stop when the finger is lifted, not on dead-zone drift.
                if (cmd === 'stop' && !released) return;
                if (cmd !== 'stop' && cmd === lastCmdRef.current) return;
                if (cmd !== 'stop' && useMetricsStore.getState().estopActive) return;
                lastCmdRef.current = cmd === 'stop' ? null : cmd;
                if (cmd === 'stop') sendCommand('stop', 'release');
                else sendCommand(cmd);
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
