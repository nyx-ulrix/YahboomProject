// Widget implementations and WIDGET_REGISTRY.
// Components read from useMetricsStore; live values are synced via hooks.ts.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, FlaskConical, Joystick, Network, Octagon, Play,
  Radar, ScanEye, Download, Trash2,
  Signal, Timer, Video, type LucideIcon,
} from 'lucide-react';
import { useMetricsStore, useSettingsStore } from '../store';
import type { WidgetDefinition, MetricsState } from '../types';
import { sendCommand, sendCameraCommand, setEstopState, toggleRosAuto, vecToCommand, vecToCameraCommand } from '../../lib/Controls';
import { setEdgeAwareStopEnabled, setStopLabelEstopArmed, vitDecodeEventKey, type VitStatusForStopLabel } from '../../lib/edgeAwareStopLabelEstop';
import {
  benchNeedsPiScript,
  clearTestBenchCache,
  loadStopModePreference,
  loadTestBenchCache,
  saveStopModePreference,
  saveTestBenchCache,
  STOP_BENCH_MODES,
  STOP_MODE_LABELS,
  STOP_SOURCE_LABELS,
  type StopBenchMode,
  type StopSource,
} from '../../lib/testBenchStorage';
import {
  clearEdgeAwareStopLabelBenchStop,
  isEdgeAwareStopLabelBenchStop,
  setTestBenchManualStopHook,
  setTestBenchSessionActive,
  setTestBenchStopMode,
  skipAutoOffAfterBenchRun,
  takeTestBenchStopIsStopLabel,
  takeTestBenchStopReason,
} from '../../lib/testBenchSession';
import { inferEventTag, shortEventTag } from '../../lib/eventLogTag';
import { VideoFeedCore } from '../../lib/VideoFeed';
import { Slider } from './ui/slider';

// Shared presentational helpers
/** Consistent card shell with a small label row and vertically centred content. */
function MetricShell({
  label, icon: Icon, accent, children,
}: { label: string; icon?: LucideIcon; accent?: string; children: React.ReactNode }) {
  return (
    <div className="h-full flex flex-col gap-1 overflow-hidden">
      <div className="flex items-center gap-1 uppercase tracking-wider truncate"
        style={{ color: 'var(--text-muted)', fontSize: 9 }}>
        {Icon && <Icon size={10} style={{ color: accent ?? 'currentColor' }} />}
        <span className="truncate">{label}</span>
      </div>
      <div className="flex-1 flex flex-col justify-center min-h-0 overflow-hidden">
        {children}
      </div>
    </div>
  );
}

// Semantic accent colours mapped to CSS tokens for consistent theming.
const accents = {
  green:  'var(--state-success)',
  red:    'var(--state-error)',
  yellow: 'var(--state-warning)',
  purple: 'var(--accent-purple)',
  cyan:   'var(--accent-cyan)',
  pink:   'var(--accent-pink)',
};

const ROBOT_FORWARD_CANVAS_OFFSET = -Math.PI / 2;

// VIDEO — Live camera feed (WebRTC/MJPEG via VideoFeedCore)

function VideoFeedWidget() {
  const fps            = useMetricsStore((s: MetricsState) => s.videoFps);
  const videoStreamUrl = useSettingsStore((s) => s.videoStreamUrl);
  const hasVideoUrl = Boolean(videoStreamUrl);

  return (
    <div className="h-full flex flex-col gap-1.5 min-h-0">
      {/* Header strip */}
      <div className="flex-shrink-0 flex items-center gap-2">
        <div className="flex items-center gap-1.5" style={{ color: 'var(--text-muted)', fontSize: 10 }}>
          <Video size={11} style={{ color: accents.pink }} />
          <span className="uppercase tracking-wider">Live Video Feed</span>
        </div>

        {hasVideoUrl && (
          <>
            <span className="pill" style={{ padding: '1px 6px', background: 'rgba(244,63,94,0.16)', color: accents.red, fontSize: 9 }}>
              ● LIVE
            </span>
            <span className="pill" style={{ padding: '1px 6px', background: 'var(--secondary)', fontSize: 9, color: 'var(--text-secondary)' }}>
              {fps != null ? `${fps} fps` : 'WIP'}
            </span>
          </>
        )}
      </div>

      {/* Video area — stream URL from /api/status */}
      <div className="flex-1 min-h-0">
        <VideoFeedCore className="rounded-xl" style={{ border: '1px solid var(--stroke-subtle)' }} />
      </div>
    </div>
  );
}

export const videoFeedDef: WidgetDefinition = {
  id: 'video_feed_widget', name: 'Video Feed', group: 'video',
  sizeClass: 'XL', defaultSize: { w: 8, h: 4, minW: 4, minH: 2 },
  icon: 'Video', pinned: false, component: VideoFeedWidget,
};

// HEALTH — System Status (MQTT link, ROS2 bridge, latency, heartbeat, video delay)
function SystemStatusWidget() {
  const mqtt    = useMetricsStore((s: MetricsState) => s.mqttLinkStatus);
  const ros2    = useMetricsStore((s: MetricsState) => s.ros2BridgeStatus);
  const latency = useMetricsStore((s: MetricsState) => s.latencyMs);
  const mode    = useMetricsStore((s: MetricsState) => s.mode);
  const rosAutoRunning = useMetricsStore((s: MetricsState) => s.autoRunning);
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  const safetyStatus = useMetricsStore((s: MetricsState) => s.safetyStatus);
  const driveStatus = useMetricsStore((s: MetricsState) => s.driveStatus);
  const frontDistance = useMetricsStore((s: MetricsState) => s.frontDistance);
  const leftDistance = useMetricsStore((s: MetricsState) => s.leftDistance);
  const rightDistance = useMetricsStore((s: MetricsState) => s.rightDistance);
  const mqttC = mqtt === 'CONNECTED' ? accents.green : mqtt === 'DISCONNECTED' ? 'var(--text-muted)' : accents.red;
  const ros2C = ros2 === 'ACTIVE'    ? accents.green : 'var(--text-muted)';
  const modeC = mode === 'auto' ? accents.purple : accents.cyan;
  const estopC = estopActive ? accents.red : accents.green;
  const fmtDistance = (value: number | null) => value == null ? '—' : `${value.toFixed(2)} m`;

  const gridItems = [
    { Icon: Signal,     color: mqttC,        label: 'MQTT Link',      value: mqtt,                                            dot: true          },
    { Icon: Network,    color: ros2C,         label: 'ROS2 Bridge',    value: ros2 ?? 'WIP',                                   dot: ros2 != null  },
    { Icon: Activity,   color: modeC,         label: 'Mode',           value: mode,                                            dot: true          },
    { Icon: Play,       color: rosAutoRunning ? accents.green : 'var(--text-muted)', label: 'Auto Running', value: String(rosAutoRunning), dot: true },
    { Icon: Octagon,    color: estopC,        label: 'E-stop Active',  value: String(estopActive),                             dot: true          },
    { Icon: Timer,      color: accents.cyan,  label: 'Round-Trip',     value: latency != null ? `${latency} ms` : 'WIP',       dot: false         },
    { Icon: Radar,      color: accents.yellow, label: 'Safety Status', value: safetyStatus,                                    dot: false         },
    { Icon: Joystick,   color: accents.pink,  label: 'Drive Status',   value: driveStatus,                                     dot: false         },
  ] as const;

  const distanceItems = [
    { label: 'Front', value: fmtDistance(frontDistance) },
    { label: 'Left', value: fmtDistance(leftDistance) },
    { label: 'Right', value: fmtDistance(rightDistance) },
  ] as const;

  return (
    <div className="h-full flex flex-col gap-1 overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 flex items-center gap-1.5 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9 }}>
        <Signal size={10} style={{ color: accents.cyan }} />
        <span>System Status</span>
      </div>

      {/* 2 × 2 status grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 flex-1 min-h-0">
        {gridItems.map(({ Icon, color, label, value, dot }) => (
          <div key={label} className="flex items-center gap-2 overflow-hidden">
            <Icon size={13} style={{ color, flexShrink: 0 }} />
            <div className="min-w-0 flex-1 overflow-hidden">
              <div style={{ fontSize: 8, color: 'var(--text-muted)', lineHeight: 1 }}
                className="uppercase tracking-wider truncate">
                {label}
              </div>
              <div className="flex items-center gap-1">
                {dot && (
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    style={{ background: color, boxShadow: `0 0 5px ${color}` }} />
                )}
                <span style={{ fontSize: 11, fontWeight: 700, color }} className="truncate">
                  {value}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex-shrink-0 h-px" style={{ background: 'var(--stroke-subtle)' }} />

      {/* Local LiDAR distance readouts */}
      <div className="flex-shrink-0 grid grid-cols-3 gap-1">
        {distanceItems.map((item) => (
          <div key={item.label} className="rounded px-1.5 py-1" style={{ background: 'var(--bg-elevated)' }}>
            <div className="uppercase tracking-wider" style={{ fontSize: 7, color: 'var(--text-muted)' }}>
              {item.label}
            </div>
            <div className="truncate" style={{ fontSize: 11, fontWeight: 800, color: accents.cyan, fontFamily: 'monospace' }}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export const systemStatusDef: WidgetDefinition = {
  id: 'system_status_widget', name: 'System Status', group: 'health',
  sizeClass: 'M', defaultSize: { w: 3, h: 2, minW: 3, minH: 2 },
  icon: 'Signal', pinned: false, component: SystemStatusWidget,
};

// CONTROL — Joystick pad (shared by movement + camera widgets)
/**
 * Circular analogue joystick pad.
 *
 * @param onChange  Called on every pointer move with normalised {x, y} in [-1, 1].
 *                  Values snap back to {0, 0} on pointer release.
 * @param externalVec  Optional override vector (e.g. from keyboard input).
 */
function JoystickPad({
  label,
  onChange,
  externalVec,
  onDoubleTap,
}: {
  label: string;
  onChange: (v: { x: number; y: number; released?: boolean }) => void;
  externalVec?: { x: number; y: number } | null;
  onDoubleTap?: () => void;
}) {
  const [vec, setVec] = useState({ x: 0, y: 0 });
  const padRef       = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef  = useRef(false);
  const lastTapRef   = useRef(0);
  const [circleSize, setCircleSize] = useState(0);

  // Local drag always wins. External vec (keyboard / remote client) shows only when not dragging.
  const display = (!draggingRef.current && externalVec && (externalVec.x !== 0 || externalVec.y !== 0))
    ? externalVec
    : vec;

  // Keep the circle a perfect square matching the smaller container dimension.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setCircleSize(Math.min(width, height));
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
    const r  = Math.min(rect.width, rect.height) / 2 - 18; // thumb clearance
    let dx = (e as PointerEvent).clientX - cx;
    let dy = (e as PointerEvent).clientY - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > r) { dx = (dx / dist) * r; dy = (dy / dist) * r; }
    const nx =  dx / r;
    const ny = -dy / r; // invert Y so up = positive
    onChange({ x: nx, y: ny });
    setVec({ x: nx, y: ny });
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex-shrink-0 flex items-center gap-1 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9, lineHeight: 1.1 }}>
        <Joystick size={10} style={{ color: accents.purple }} />
        <span>{label}</span>
      </div>

      {/* Square-constrained container */}
      <div ref={containerRef} className="flex-1 flex items-center justify-center min-h-0 min-w-0">
        <div
          ref={padRef}
          className="relative rounded-full select-none touch-none flex-shrink-0"
          style={{
            width:  circleSize || undefined,
            height: circleSize || undefined,
            ...(circleSize === 0 ? { width: '100%', aspectRatio: '1 / 1' } : {}),
            background: 'radial-gradient(circle at 30% 30%, rgba(139,92,246,0.18), rgba(0,0,0,0.2))',
            border: '1px solid var(--stroke-strong)',
            boxShadow: 'inset 0 2px 16px rgba(0,0,0,0.4), 0 0 24px var(--glow-color)',
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
          onPointerUp={() => {
            draggingRef.current = false;
            onChange({ x: 0, y: 0, released: true });
            setVec({ x: 0, y: 0 });
          }}
          onPointerCancel={() => {
            draggingRef.current = false;
            onChange({ x: 0, y: 0, released: true });
            setVec({ x: 0, y: 0 });
          }}
        >
          {/* Crosshairs */}
          <div className="absolute top-1/2 left-2 right-2 h-px" style={{ background: 'var(--stroke-subtle)' }} />
          <div className="absolute left-1/2 top-2 bottom-2 w-px" style={{ background: 'var(--stroke-subtle)' }} />
          {/* Thumb */}
          <div
            className="absolute rounded-full pointer-events-none"
            style={{
              width: '32%', aspectRatio: '1 / 1',
              top: '50%', left: '50%',
              transform: `translate(calc(-50% + ${display.x * 34}%), calc(-50% + ${-display.y * 34}%))`,
              background: 'linear-gradient(135deg, var(--accent-purple), var(--accent-cyan))',
              boxShadow: '0 4px 20px rgba(139,92,246,0.5), inset 0 2px 4px rgba(255,255,255,0.3)',
              transition: draggingRef.current ? 'none' : 'transform 180ms ease-out',
            }}
          />
        </div>
      </div>
    </div>
  );
}

// Movement Joystick
function MovementJoystickWidget() {
  const lastCmd     = useRef<ReturnType<typeof vecToCommand> | null>(null);
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  const movementVec = useMetricsStore((s: MetricsState) => s.movementVec);

  // When estop is cleared, reset lastCmd so the first joystick gesture after
  // resuming always fires — even if it's the same direction as before estop.
  useEffect(() => {
    if (!estopActive) lastCmd.current = null;
  }, [estopActive]);

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: '100%', aspectRatio: '1 / 1', maxHeight: '100%' }}>
        <JoystickPad
          label="Movement"
          externalVec={movementVec}
          onChange={({ x, y, released }) => {
            const cmd = vecToCommand(y, -x);
            // Only send stop when the finger is lifted, not when drifting through the dead-zone.
            if (cmd === 'stop' && !released) return;
            // Deduplicate movement commands — skip if same direction as last send.
            if (cmd !== 'stop' && cmd === lastCmd.current) return;
            // Don't record or forward non-stop commands while estop is latched
            // (prevents lastCmd becoming stale and suppressing the first real
            // move after the latch is cleared).
            if (cmd !== 'stop' && useMetricsStore.getState().estopActive) return;
            lastCmd.current = cmd === 'stop' ? null : cmd;
            if (cmd === 'stop') sendCommand('stop', 'release');
            else sendCommand(cmd);
          }}
        />
      </div>
    </div>
  );
}

export const movementJoystickDef: WidgetDefinition = {
  id: 'movement_joystick_widget', name: 'Movement Joystick', group: 'control',
  sizeClass: 'L', defaultSize: { w: 2, h: 2 },
  icon: 'Joystick', pinned: false, component: MovementJoystickWidget,
};

// Camera Joystick
function CameraJoystickWidget() {
  const kbd = useMetricsStore((s: MetricsState) => s.cameraKeyboardVec);
  const lastCmdRef = useRef<ReturnType<typeof vecToCameraCommand>>(null);

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: '100%', aspectRatio: '1 / 1', maxHeight: '100%' }}>
        <JoystickPad
          label="Camera"
          externalVec={kbd}
          onDoubleTap={() => sendCameraCommand('crst')}
          onChange={({ x, y, released }) => {
            const cmd = vecToCameraCommand(x, y);
            if (cmd !== null && cmd !== lastCmdRef.current) {
              lastCmdRef.current = cmd;
              sendCameraCommand(cmd);
            } else if (cmd === null) {
              if (lastCmdRef.current !== null || released) sendCameraCommand('cstop');
              lastCmdRef.current = null;
            }
          }}
        />
      </div>
    </div>
  );
}

export const cameraJoystickDef: WidgetDefinition = {
  id: 'camera_joystick_widget', name: 'Camera Joystick', group: 'control',
  sizeClass: 'L', defaultSize: { w: 2, h: 2 },
  icon: 'Camera', pinned: false, component: CameraJoystickWidget,
};

// Emergency Stop
function StopButtonWidget() {
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);

  const handleClick = () => {
    void setEstopState(!estopActive);
  };

  return (
    <div className="h-full w-full flex items-center justify-center p-1">
      <button
        onClick={handleClick}
        className="w-full h-full rounded-2xl flex items-center justify-center gap-2 transition-all"
        style={{
          minHeight: 48,
          background: estopActive
            ? 'linear-gradient(135deg, #f59e0b, #92400e)'
            : 'linear-gradient(135deg, var(--state-error), #7f1d1d)',
          color: '#fff', fontWeight: 700, fontSize: 13, letterSpacing: '0.08em',
          border: estopActive
            ? '2px solid #fbbf24'
            : '1px solid rgba(255,255,255,0.2)',
          boxShadow: estopActive
            ? '0 0 24px rgba(251,191,36,0.7), inset 0 1px 0 rgba(255,255,255,0.2)'
            : '0 8px 24px rgba(244,63,94,0.4), inset 0 1px 0 rgba(255,255,255,0.2)',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-2px)')}
        onMouseLeave={(e) => (e.currentTarget.style.transform = 'translateY(0)')}
        title={estopActive ? 'E-Stop active — click to resume' : 'Emergency stop'}
      >
        <Octagon size={16} fill="#fff" />
        {estopActive ? 'RESUME CONTROL' : 'EMERGENCY STOP'}
      </button>
    </div>
  );
}

export const stopButtonDef: WidgetDefinition = {
  id: 'stop_button_widget', name: 'Emergency Stop', group: 'control',
  sizeClass: 'M', defaultSize: { w: 2, h: 1, minW: 1, minH: 1 },
  icon: 'Octagon', pinned: false, component: StopButtonWidget,
};

// LOGGING — Event log
function EventLogWidget() {
  const events = useMetricsStore((s: MetricsState) => s.events);
  const colorFor = (l: string) =>
    l === 'error' ? accents.red : l === 'warning' ? accents.yellow : accents.cyan;

  // Keep history in state, but render only the latest N rows so the widget stays compact.
  const visible = [...events].slice(-80).reverse();

  return (
    <div className="h-full flex flex-col gap-2 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider"
          style={{ color: 'var(--text-muted)' }}>
          <Activity size={12} style={{ color: accents.purple }} />
          <span>Event Log</span>
        </div>
        <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{events.length} events</span>
      </div>

      {/* Scrollable log */}
      <div className="flex-1 overflow-y-auto rounded-xl min-h-0"
        style={{ background: 'rgba(0,0,0,0.18)', border: '1px solid var(--stroke-subtle)' }}>
        <div className="flex flex-col">
          {visible.map((ev) => {
            const tag = inferEventTag(ev);
            return (
            <div key={ev.id} className="flex items-start gap-2 px-3 py-1 border-b"
              style={{ borderColor: 'var(--stroke-subtle)' }}>
              <span style={{ fontSize: 9, color: 'var(--text-muted)', minWidth: 70, fontFamily: 'monospace' }}>
                {new Date(ev.timestamp).toLocaleTimeString()}
              </span>
              <span className="px-1.5 rounded" style={{
                fontSize: 10, fontWeight: 600,
                color: colorFor(ev.level),
                background: `${colorFor(ev.level)}22`,
                minWidth: 50, textAlign: 'center', flexShrink: 0,
              }}>
                {ev.level.toUpperCase()}
              </span>
              {tag && (
                <span
                  title={tag}
                  className="px-1.5 rounded truncate"
                  style={{
                    fontSize: 9,
                    fontWeight: 600,
                    fontFamily: 'monospace',
                    color: accents.purple,
                    background: 'rgba(168,85,247,0.15)',
                    maxWidth: 120,
                    flexShrink: 0,
                  }}
                >
                  {shortEventTag(tag)}
                </span>
              )}
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace', minWidth: 0 }}>
                {ev.message}
              </span>
            </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export const eventLogDef: WidgetDefinition = {
  id: 'event_log_widget', name: 'Event Log', group: 'logging',
  sizeClass: 'FULL', defaultSize: { w: 9, h: 3, minW: 4, minH: 2 },
  icon: 'Activity', pinned: false, component: EventLogWidget,
};

// ROS Auto Button — sends auto_on / auto_off to the Pi.
function RosAutoButtonWidget() {
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  const rosAutoRunning = useMetricsStore((s: MetricsState) => s.autoRunning);
  const blocked = estopActive && !rosAutoRunning;

  return (
    <div className="h-full w-full flex items-center justify-center p-1">
      <button
        type="button"
        onClick={() => toggleRosAuto()}
        disabled={blocked}
        className="w-full h-full rounded-2xl flex items-center justify-center gap-2 transition-all"
        style={{
          minHeight: 48,
          opacity: blocked ? 0.5 : 1,
          cursor: blocked ? 'not-allowed' : 'pointer',
          background: rosAutoRunning
            ? 'linear-gradient(135deg, var(--accent-purple), #4c1d95)'
            : 'linear-gradient(135deg, var(--state-success), #14532d)',
          color: '#fff',
          fontWeight: 700,
          fontSize: 13,
          letterSpacing: '0.08em',
          border: rosAutoRunning
            ? '2px solid var(--accent-purple)'
            : '1px solid rgba(255,255,255,0.2)',
          boxShadow: rosAutoRunning
            ? '0 0 24px rgba(139,92,246,0.55), inset 0 1px 0 rgba(255,255,255,0.2)'
            : '0 8px 24px rgba(34,197,94,0.4), inset 0 1px 0 rgba(255,255,255,0.2)',
        }}
        onMouseEnter={(e) => {
          if (!blocked) e.currentTarget.style.transform = 'translateY(-2px)';
        }}
        onMouseLeave={(e) => (e.currentTarget.style.transform = 'translateY(0)')}
        title={blocked ? 'E-stop active — blocked' : rosAutoRunning ? 'Send auto_off' : 'Send auto_on'}
      >
        {rosAutoRunning ? 'STOP EXPLORING' : 'EXPLORE'}
      </button>
    </div>
  );
}

export const rosAutoButtonDef: WidgetDefinition = {
  id: 'ros_auto_button_widget', name: 'ROS Auto Button', group: 'control',
  sizeClass: 'M', defaultSize: { w: 2, h: 1, minW: 1, minH: 1 },
  icon: 'Bot', pinned: false, component: RosAutoButtonWidget,
};

// LiDAR — latest scan numbers (from GRID_TOPIC via /api/grid_status)
function LidarScanWidget() {
  const grid = useMetricsStore((s: MetricsState) => s.latestGrid);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [circleSize, setCircleSize] = useState(0);

  useEffect(() => {
    const cvs = canvasRef.current;
    const cells = grid?.grid ?? null;
    const w = grid?.w ?? 120;
    const h = grid?.h ?? 120;
    if (!cvs || !grid || !cells || w <= 0 || h <= 0 || cells.length !== w * h) return;

    const { robot_row, robot_col } = grid;

    // Draw as 1px-per-cell image, scaled by CSS.
    // No rotation: "front" points up.
    const outW = w;
    const outH = h;
    if (cvs.width !== outW) cvs.width = outW;
    if (cvs.height !== outH) cvs.height = outH;
    const ctx = cvs.getContext('2d');
    if (!ctx) return;

    const img = ctx.createImageData(outW, outH);
    const data = img.data;
    for (let sy = 0; sy < h; sy++) {
      for (let sx = 0; sx < w; sx++) {
        const v = cells[sy * w + sx];
        const dx = sx;
        const dy = sy;

        const o = (dy * outW + dx) * 4;
        if (v === 1) {
          data[o] = 0;
          data[o + 1] = 0;
          data[o + 2] = 0;
        } else if (v === 0) {
          data[o] = 255;
          data[o + 1] = 255;
          data[o + 2] = 255;
        } else {
          data[o] = 211;
          data[o + 1] = 211;
          data[o + 2] = 211;
        }
        data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);

    if (robot_row != null && robot_col != null) {
      ctx.fillStyle = '#ef4444';
      ctx.beginPath();
      ctx.arc(robot_col + 0.5, robot_row + 0.5, Math.max(2, Math.min(w, h) * 0.025), 0, Math.PI * 2);
      ctx.fill();
    }
  }, [grid]);

  const ageMs = grid?.updatedAt ? (Date.now() - grid.updatedAt) : null;
  const ageSec = ageMs != null ? Math.max(0, Math.round(ageMs / 100) / 10) : null;

  // Force the scan viewport to always be a perfect circle (even if the widget is a rectangle).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setCircleSize(Math.min(width, height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="h-full flex flex-col min-h-0">
      {/* Header (styled similar to JoystickPad) */}
      <div
        className="flex-shrink-0 flex items-center justify-between gap-2 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9, lineHeight: 1.1 }}
      >
        <div className="flex items-center gap-1 min-w-0">
          <Radar size={10} style={{ color: accents.cyan, flexShrink: 0 }} />
          <span className="truncate">Local LiDAR Grid</span>
        </div>
        <span style={{ fontSize: 10, fontFamily: 'monospace' }}>
          {ageSec != null ? `${ageSec}s` : '—'}
        </span>
      </div>

      {/* Square-constrained container (like JoystickPad) */}
      <div ref={containerRef} className="flex-1 flex items-center justify-center min-h-0 min-w-0">
        <div
          className="relative overflow-hidden flex-shrink-0"
          style={{
            width: circleSize || undefined,
            height: circleSize || undefined,
            ...(circleSize === 0 ? { width: '100%', aspectRatio: '1 / 1' } : {}),
            borderRadius: 9999,
            background: 'rgba(0,0,0,0.18)',
            border: '1px solid var(--stroke-subtle)',
          }}
        >
          <canvas
            ref={canvasRef}
            style={{
              width: '100%',
              height: '100%',
              imageRendering: 'pixelated',
              display: 'block',
            }}
          />

          {/* Front indicator (up) */}
          <div
            className="absolute left-1/2 top-1/2"
            style={{
              transform: 'translate(-50%, -50%)',
              pointerEvents: 'none',
              filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.8))',
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 3 L18 13 H13 V21 H11 V13 H6 Z"
                fill="rgba(244,63,94,0.95)"
              />
            </svg>
          </div>
        </div>
      </div>

      {/* Status (outside the circle) */}
      <div className="flex-shrink-0 flex items-center justify-between gap-2 min-w-0">
        <span className="truncate" style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
          {grid?.status ?? 'unknown'}
        </span>
        <span className="pill" style={{
          padding: '1px 6px',
          background: grid?.estop_active ? 'rgba(244,63,94,0.18)' : 'rgba(34,197,94,0.18)',
          color: grid?.estop_active ? accents.red : accents.green,
          fontSize: 9,
          fontWeight: 700,
        }}>
          {grid?.estop_active ? 'E-STOP' : 'OK'}
        </span>
      </div>
    </div>
  );
}

export const lidarScanDef: WidgetDefinition = {
  id: 'lidar_scan_widget', name: 'Local LiDAR Grid', group: 'health',
  sizeClass: 'M', defaultSize: { w: 2, h: 2, minW: 2, minH: 2 },
  icon: 'Radar', pinned: false, component: LidarScanWidget,
};

// SLAM — occupancy grid map (from backend slam_map.json via /api/slam/map)
type SlamMapResponse = {
  timestamp?: string;
  slam_status?: string;
  robot_pose?: { x: number; y: number; theta: number; confidence?: number; icp_confidence?: number; turn_cal_confidence?: number };
  map?: {
    width: number;
    height: number;
    resolution: number;
    origin: { x: number; y: number };
    cells: number[]; // -1 unknown, 0 free, 100 occupied
  };
  trajectory?: { x: number; y: number; theta: number; t?: string }[];
  latest_scan?: { x: number; y: number }[] | null;
  stats?: {
    scans_processed?: number;
    rejected_scans?: number;
    map_coverage_pct?: number;
    uptime_s?: number;
    turn_calibration?: {
      angular_rps_effective?: number;
      angular_rps_nominal?: number;
      scale?: number;
      samples?: number;
    };
  };
};

function SlamMapWidget() {
  const [slam, setSlam] = useState<SlamMapResponse | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [squareSize, setSquareSize] = useState(0);
  const [resetBusy, setResetBusy] = useState(false);

  const mapToCanvas = (
    x: number,
    y: number,
    map: NonNullable<SlamMapResponse['map']>,
  ) => ({
    x: (x - map.origin.x) / map.resolution,
    y: (map.height - 1) - ((y - map.origin.y) / map.resolution),
  });

  const poll = async () => {
    try {
      const res = await fetch('/api/slam/map?crop=1', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json() as SlamMapResponse;
      setSlam(data);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    let alive = true;
    const tick = async () => { if (alive) await poll(); };
    tick();
    const id = setInterval(tick, 250);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resetMap = async () => {
    if (resetBusy) return;
    setResetBusy(true);
    try {
      // Optimistically clear the canvas immediately.
      setSlam((prev) => prev ? ({ ...prev, trajectory: [], map: prev.map ? { ...prev.map, cells: prev.map.cells.map(() => -1) } : prev.map }) : prev);
      await fetch('/api/slam/reset', { method: 'POST' });
      await poll();
    } catch { /* ignore */ }
    setResetBusy(false);
  };

  // Keep a square viewport regardless of widget aspect ratio.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSquareSize(Math.min(width, height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const cvs = canvasRef.current;
    const m = slam?.map;
    if (!cvs || !m?.cells || m.width <= 0 || m.height <= 0) return;
    if (m.cells.length !== m.width * m.height) return;

    const ctx = cvs.getContext('2d');
    if (!ctx) return;

    const W = m.width;
    const H = m.height;
    // Render the backend-provided view. The SLAM API crops this to explored space.
    const minX = 0;
    const minY = 0;
    const viewW = W;
    const viewH = H;

    // No downsampling: render 1px-per-cell for the cropped viewport.
    const step = 1;
    const outW = viewW;
    const outH = viewH;

    if (cvs.width !== outW) cvs.width = outW;
    if (cvs.height !== outH) cvs.height = outH;
    ctx.imageSmoothingEnabled = false;

    // Base occupancy grid
    const img = ctx.createImageData(outW, outH);
    const data = img.data;
    for (let sy = 0; sy < outH; sy++) {
      const srcY = minY + sy * step;
      if (srcY < 0 || srcY >= H) continue;
      for (let sx = 0; sx < outW; sx++) {
        const srcX = minX + sx * step;
        if (srcX < 0 || srcX >= W) continue;
        const v = m.cells[srcY * W + srcX];

        // Encode: always draw the full grid (unknown/free/occupied).
        // This makes the widget \"filled\" even when most of the map is unknown.
        let r = 0, g = 0, b = 0, a = 255;
        if (v === 0) {          // free
          r = 10; g = 10; b = 12; a = 255;
        } else if (v > 0) {     // occupied (100)
          r = 235; g = 235; b = 240; a = 255;
        } else {                // unknown (-1)
          r = 24; g = 24; b = 28; a = 255;
        }

        // Flip Y for canvas so +Y in world is up.
        const dy = (outH - 1) - sy;
        const o = (dy * outW + sx) * 4;
        data[o] = r;
        data[o + 1] = g;
        data[o + 2] = b;
        data[o + 3] = a;
      }
    }
    ctx.putImageData(img, 0, 0);

    const toCanvas = (x: number, y: number) => mapToCanvas(x, y, m);
    const inView = (p: { x: number; y: number }) =>
      p.x >= 0 && p.x < outW && p.y >= 0 && p.y < outH;

    const latestScan = slam?.latest_scan ?? [];
    if (latestScan.length > 0) {
      const radius = Math.max(1.2, Math.min(outW, outH) / 150);
      ctx.save();
      ctx.fillStyle = 'rgba(245,158,11,0.82)';
      for (const scanPoint of latestScan) {
        const p = toCanvas(scanPoint.x, scanPoint.y);
        if (!inView(p)) continue;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    const trajectory = slam?.trajectory ?? [];
    if (trajectory.length > 1) {
      ctx.save();
      ctx.lineWidth = Math.max(1, Math.round(Math.min(outW, outH) / 180));
      ctx.strokeStyle = 'rgba(34,211,238,0.72)';
      ctx.beginPath();
      let drawing = false;
      for (const pose of trajectory) {
        const p = toCanvas(pose.x, pose.y);
        if (!inView(p)) {
          drawing = false;
          continue;
        }
        if (!drawing) {
          ctx.moveTo(p.x, p.y);
          drawing = true;
        } else {
          ctx.lineTo(p.x, p.y);
        }
      }
      ctx.stroke();
      ctx.restore();
    }

    const start = trajectory[0];
    if (start) {
      const p = toCanvas(start.x, start.y);
      if (inView(p)) {
        const radius = Math.max(3, Math.min(outW, outH) / 45);
        ctx.save();
        ctx.fillStyle = 'rgba(34,197,94,0.95)';
        ctx.strokeStyle = 'rgba(10,10,12,0.95)';
        ctx.lineWidth = Math.max(1, radius / 3);
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
    }

    const robot = slam?.robot_pose;
    if (robot) {
      const p = toCanvas(robot.x, robot.y);
      if (inView(p)) {
        const size = Math.max(7, Math.min(outW, outH) / 22);
        const theta = -robot.theta + ROBOT_FORWARD_CANVAS_OFFSET;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(theta);
        ctx.fillStyle = 'rgba(168,85,247,0.98)';
        ctx.strokeStyle = 'rgba(255,255,255,0.92)';
        ctx.lineWidth = Math.max(1, size / 8);
        ctx.beginPath();
        ctx.moveTo(size, 0);
        ctx.lineTo(-size * 0.62, -size * 0.55);
        ctx.lineTo(-size * 0.36, 0);
        ctx.lineTo(-size * 0.62, size * 0.55);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
    }
  }, [slam?.map, slam?.robot_pose, slam?.trajectory]);

  const status = slam?.slam_status ?? '—';
  const conf = slam?.robot_pose?.confidence;
  const icpConf = slam?.robot_pose?.icp_confidence;
  const turnConf = slam?.robot_pose?.turn_cal_confidence;
  const angRps = slam?.stats?.turn_calibration?.angular_rps_effective;
  const confText = conf == null
    ? '—'
    : `${Math.round(conf * 100)}%/${Math.round((icpConf ?? 0) * 100)}%/${Math.round((turnConf ?? 0) * 100)}%`;
  const angRpsText = angRps != null ? `${angRps.toFixed(2)} rad/s` : '—';
  const scans = slam?.stats?.scans_processed ?? 0;
  const rejected = slam?.stats?.rejected_scans ?? 0;

  return (
    <div className="h-full flex flex-col min-h-0">
      <div
        className="flex-shrink-0 flex items-center justify-between gap-2 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9, lineHeight: 1.1 }}
      >
        <div className="flex items-center gap-1 min-w-0">
          <Radar size={10} style={{ color: accents.purple, flexShrink: 0 }} />
          <span className="truncate">Persistent SLAM Map</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={resetMap}
            disabled={resetBusy}
            className="pill"
            title="Clear the current SLAM map"
            style={{
              padding: '1px 6px',
              background: 'rgba(244,63,94,0.16)',
              color: accents.red,
              fontSize: 9,
              fontWeight: 800,
              letterSpacing: '0.06em',
              cursor: resetBusy ? 'not-allowed' : 'pointer',
              opacity: resetBusy ? 0.6 : 1,
              border: '1px solid rgba(244,63,94,0.35)',
            }}
          >
            {resetBusy ? 'CLEARING…' : 'CLEAR'}
          </button>
          <span style={{ fontSize: 10, fontFamily: 'monospace' }}>{confText}</span>
        </div>
      </div>

      <div
        className="flex-shrink-0 truncate"
        style={{ color: accents.yellow, fontSize: 9, lineHeight: 1.2, paddingTop: 2 }}
      >
        SLAM map may drift without wheel odometry
      </div>

      <div ref={containerRef} className="flex-1 flex items-center justify-center min-h-0 min-w-0">
        <div
          className="relative overflow-hidden flex-shrink-0 rounded-xl"
          style={{
            width: squareSize || undefined,
            height: squareSize || undefined,
            ...(squareSize === 0 ? { width: '100%', aspectRatio: '1 / 1' } : {}),
            background: 'rgba(0,0,0,0.18)',
            border: '1px solid var(--stroke-subtle)',
          }}
        >
          <canvas
            ref={canvasRef}
            style={{
              width: '100%',
              height: '100%',
              imageRendering: 'pixelated',
              display: 'block',
            }}
          />
        </div>
      </div>

      <div className="flex-shrink-0 flex items-center justify-between gap-2 min-w-0">
        <span className="truncate" style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
          {status} · scans {scans} · rej {rejected} · ω {angRpsText}
        </span>
        <span className="pill" style={{
          padding: '1px 6px',
          background: status === 'running' ? 'rgba(34,197,94,0.18)' : 'rgba(245,158,11,0.18)',
          color: status === 'running' ? accents.green : accents.yellow,
          fontSize: 9,
          fontWeight: 700,
        }}>
          {status.toUpperCase()}
        </span>
      </div>
    </div>
  );
}

export const slamMapDef: WidgetDefinition = {
  id: 'slam_map_widget', name: 'Persistent SLAM Map', group: 'health',
  sizeClass: 'L', defaultSize: { w: 4, h: 4, minW: 3, minH: 3 },
  icon: 'Radar', pinned: false, component: SlamMapWidget,
};

// VIT — MobileCLIP scene decoder (/api/vit/*)
type VitDetection = { label: string; confidence: number };

type VitActivity = {
  embeddings_received: number;
  decodes_succeeded: number;
  decode_failures: number;
  last_embedding_at: string | null;
  last_decode_at: string | null;
  last_status_at: string | null;
  last_decode_error: string | null;
};

type VitStatusResponse = {
  connected: boolean;
  broker_ip: string | null;
  vit_server_running: boolean;
  /** SSH probe or recent MQTT embeddings — encoder pipeline is active. */
  encoder_live?: boolean;
  model_enabled: boolean;
  model_ready: boolean;
  model_error: string | null;
  confidence_threshold: number;
  max_file_size_kb: number;
  requested_embedding_bytes?: number | null;
  embedding_command_active?: boolean;
  session_count: number;
  activity?: VitActivity;
  latest: {
    top_label: string;
    top_confidence: number;
    alert: boolean;
    results: VitDetection[];
    embedding_size: number | null;
    embedding_dim: number | null;
    image_file_size: number | null;
    source: string;
    timestamp: string;
  } | null;
};

const VIT_EMBED_SIZE_OPTIONS = [512, 1024, 2048] as const;
/** Slider uses equal-spaced indices 0|1|2 so 1024 B is always at 50% (not linear 512–2048). */
const VIT_SLIDER_INDEX_MAX = VIT_EMBED_SIZE_OPTIONS.length - 1;
const VIT_EMBED_CENTER_INDEX = 1;
/** Track fill for the latest received embedding size (legend swatch uses the same colour). */
const VIT_CURRENT_EMBED_FILL = 'rgba(100, 130, 165, 0.55)';
/** How recently a decode/embedding must have arrived to count as "active". */
const VIT_ACTIVE_MS = 2500;

function snapVitEmbedSize(value: number): (typeof VIT_EMBED_SIZE_OPTIONS)[number] {
  return VIT_EMBED_SIZE_OPTIONS.reduce((best, n) =>
    Math.abs(n - value) < Math.abs(best - value) ? n : best,
  );
}

function vitEmbedBytesToSliderIndex(bytes: number): number {
  const snapped = snapVitEmbedSize(bytes);
  const idx = VIT_EMBED_SIZE_OPTIONS.findIndex((n) => n === snapped);
  return idx >= 0 ? idx : VIT_EMBED_CENTER_INDEX;
}

function vitEmbedSliderIndexToBytes(index: number): number {
  const i = Math.round(index);
  const clamped = Math.max(0, Math.min(VIT_SLIDER_INDEX_MAX, i));
  return VIT_EMBED_SIZE_OPTIONS[clamped] ?? 2048;
}

/** Tick / overlay position — equal thirds: 0%, 50%, 100%. */
function vitEmbedTickPercent(index: number): number {
  if (VIT_SLIDER_INDEX_MAX <= 0) return 0;
  return (index / VIT_SLIDER_INDEX_MAX) * 100;
}

function vitIsoAgeMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Date.now() - t : null;
}

function vitDecoderPill(input: {
  serverRunning: boolean;
  encoderLive: boolean;
  linkUp: boolean;
  modelEnabled: boolean;
  modelReady: boolean;
  modelError: string | null | undefined;
  activity: VitActivity | undefined;
}): { label: string; color: string; dotActive: boolean } {
  const {
    serverRunning, encoderLive, linkUp, modelEnabled, modelReady, modelError, activity,
  } = input;
  const muted = 'var(--text-muted)';

  if (!linkUp) {
    return { label: 'NO BROKER — CONNECT IN SETTINGS', color: accents.yellow, dotActive: false };
  }
  if (!encoderLive && !serverRunning) {
    return { label: 'SERVER OFF — START VIT AND VIDEO', color: muted, dotActive: false };
  }
  if (!serverRunning && encoderLive) {
    return { label: 'PI ENCODER LIVE (MANUAL START)', color: accents.cyan, dotActive: true };
  }
  if (serverRunning && !encoderLive) {
    return { label: 'SERVER ON — WAITING FOR MQTT', color: accents.yellow, dotActive: false };
  }
  if (!modelEnabled) {
    const embAge = vitIsoAgeMs(activity?.last_embedding_at);
    if (embAge != null && embAge < VIT_ACTIVE_MS) {
      return { label: 'RECEIVING EMBEDDINGS', color: accents.cyan, dotActive: true };
    }
    return { label: 'RESULT FEED ONLY', color: accents.cyan, dotActive: false };
  }
  if (!modelReady) {
    if (modelError && modelError !== 'model disabled') {
      return { label: 'DECODER MODEL ERROR', color: accents.red, dotActive: false };
    }
    return { label: 'LOADING MOBILECLIP MODEL', color: accents.yellow, dotActive: false };
  }

  const decodeAge = vitIsoAgeMs(activity?.last_decode_at);
  const embAge = vitIsoAgeMs(activity?.last_embedding_at);

  if (decodeAge != null && decodeAge < VIT_ACTIVE_MS) {
    return { label: 'DECODING — MODEL READY', color: accents.green, dotActive: true };
  }
  if (embAge != null && embAge < VIT_ACTIVE_MS) {
    if (activity?.last_decode_error) {
      return { label: 'DECODE ERROR', color: accents.red, dotActive: true };
    }
    return { label: 'RECEIVING EMBEDDINGS', color: accents.cyan, dotActive: true };
  }
  if ((activity?.embeddings_received ?? 0) > 0) {
    return { label: 'IDLE — NO RECENT FRAMES', color: accents.yellow, dotActive: false };
  }
  return { label: 'WAITING FOR EMBEDDINGS', color: accents.yellow, dotActive: false };
}

function vitDetectionHint(input: {
  serverRunning: boolean;
  encoderLive: boolean;
  linkUp: boolean;
  modelReady: boolean;
  activity: VitActivity | undefined;
  latestLabel: string | null | undefined;
}): string {
  const { serverRunning, encoderLive, linkUp, modelReady, activity, latestLabel } = input;

  if (!linkUp) {
    return 'MQTT broker disconnected — connect to the Pi in Settings';
  }
  if (!encoderLive && !serverRunning) {
    return 'Use START VIT AND VIDEO in the top bar to launch webrtc_server.py';
  }
  if (serverRunning && !encoderLive) {
    return 'Server started — waiting for embeddings on yahboom/vit/embedding…';
  }
  if (!serverRunning && encoderLive && !latestLabel) {
    return 'Encoder running on Pi (manual start) — waiting for next detection…';
  }
  if (latestLabel) {
    return latestLabel;
  }
  if (activity?.last_decode_error) {
    return `Decode failed: ${activity.last_decode_error}`;
  }
  const embAge = vitIsoAgeMs(activity?.last_embedding_at);
  if (embAge != null && embAge < VIT_ACTIVE_MS && modelReady) {
    return 'Decoding latest embedding…';
  }
  if (encoderLive) {
    return 'Waiting for embeddings from webrtc_server.py…';
  }
  return 'Start webrtc_server.py from the top bar to begin detection';
}

function VitDecoderWidget() {
  const [status, setStatus] = useState<VitStatusResponse | null>(null);
  const streamRunning = useMetricsStore((s: MetricsState) => s.streamRunning);
  const mqttLink = useMetricsStore((s: MetricsState) => s.mqttLinkStatus);
  // Max embedding size (bytes). Synced from the backend on first load only,
  // so dragging never fights the 500 ms poll.
  const [maxEmbedBytes, setMaxEmbedBytes] = useState(2048);
  const fileSizeInitRef = useRef(false);
  const lastEmbedCommitRef = useRef<number | null>(null);
  const [exporting, setExporting] = useState(false);

  const serverActive = streamRunning || (status?.vit_server_running ?? false);
  const encoderLive = status?.encoder_live ?? serverActive;

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/vit/status', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json() as VitStatusResponse;
        if (!alive) return;
        setStatus(data);
        if (!fileSizeInitRef.current && typeof data.max_file_size_kb === 'number') {
          fileSizeInitRef.current = true;
          setMaxEmbedBytes(snapVitEmbedSize(data.max_file_size_kb));
        }
      } catch { /* backend unreachable — keep last state */ }
    };
    poll();
    const id = setInterval(poll, 500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    if (!status?.embedding_command_active) {
      lastEmbedCommitRef.current = null;
    }
  }, [status?.embedding_command_active]);

  const commitEmbedSize = async (bytes: number) => {
    const snapped = snapVitEmbedSize(bytes);
    if (lastEmbedCommitRef.current === snapped) return;
    lastEmbedCommitRef.current = snapped;
    try {
      await fetch('/api/vit/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ embedding_size_bytes: snapped }),
      });
    } catch { /* ignore network errors */ }
  };

  const onEmbedSliderChange = (sliderIndex: number) => {
    // UI-only: keep the thumb responsive while dragging.
    const bytes = vitEmbedSliderIndexToBytes(sliderIndex);
    setMaxEmbedBytes(bytes);
  };

  const onEmbedSliderCommit = (sliderIndex: number) => {
    // Network side-effect only on commit (mouse up / touch end / click release).
    const bytes = vitEmbedSliderIndexToBytes(sliderIndex);
    setMaxEmbedBytes(bytes);
    void commitEmbedSize(bytes);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const res = await fetch('/api/vit/export', { cache: 'no-store' });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vit_session_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
    setExporting(false);
  };

  const clearSession = async () => {
    try {
      await fetch('/api/vit/clear', { method: 'POST' });
      setStatus((prev) => prev ? { ...prev, latest: null, session_count: 0 } : prev);
    } catch { /* ignore */ }
  };

  const latest = status?.latest ?? null;
  // Show detections while SSH or MQTT confirms the encoder pipeline is active.
  const displayLatest = encoderLive ? latest : null;
  const threshold = status?.confidence_threshold ?? 60;
  const topConf = displayLatest?.top_confidence ?? null;
  const confColor =
    topConf == null ? 'var(--text-muted)'
    : topConf >= threshold ? accents.green
    : topConf >= threshold * 0.6 ? accents.yellow
    : accents.red;

  // Decoder activity pill — "MODEL READY" only while actively decoding; otherwise
  // shows what the server is doing (waiting, receiving embeddings, errors, etc.).
  const modelReady = status?.model_ready ?? false;
  const modelEnabled = status?.model_enabled ?? false;
  const linkUp = mqttLink === 'CONNECTED' || (status?.connected ?? false);
  const { label: decoderLabel, color: decoderColor, dotActive } = vitDecoderPill({
    serverRunning: serverActive,
    encoderLive,
    linkUp,
    modelEnabled,
    modelReady,
    modelError: status?.model_error,
    activity: status?.activity,
  });

  const detectionHint = vitDetectionHint({
    serverRunning: serverActive,
    encoderLive,
    linkUp,
    modelReady,
    activity: status?.activity,
    latestLabel: displayLatest?.top_label,
  });

  const sessionCount = status?.session_count ?? 0;
  const latestEmbedBytes = status?.latest?.embedding_size ?? null;
  const currentEmbedFillIndex =
    latestEmbedBytes != null && encoderLive
      ? vitEmbedBytesToSliderIndex(latestEmbedBytes)
      : undefined;

  return (
    <div className="h-full flex flex-col gap-1.5 min-h-0">
      {/* Header */}
      <div className="flex-shrink-0 flex items-center justify-between gap-2 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9, lineHeight: 1.1 }}>
        <div className="flex items-center gap-1 min-w-0">
          <ScanEye size={11} style={{ color: accents.purple, flexShrink: 0 }} />
          <span className="truncate">VIT Scene Decoder</span>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className="pill" style={{
            padding: '1px 6px', fontSize: 8, fontWeight: 700,
            background: `${decoderColor}22`, color: decoderColor,
          }}>
            {decoderLabel}
          </span>
          <span className="w-1.5 h-1.5 rounded-full"
            style={{ background: dotActive ? accents.green : 'var(--text-muted)',
              boxShadow: dotActive ? `0 0 5px ${accents.green}` : 'none' }} />
        </div>
      </div>

      {/* Primary detection readout */}
      <div className="flex-shrink-0 rounded-xl px-3 py-2"
        style={{ background: 'rgba(0,0,0,0.18)', border: '1px solid var(--stroke-subtle)' }}>
        <div className="uppercase tracking-wider" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
          Detected Object
        </div>
        <div className="truncate" style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.25 }}>
          {detectionHint}
        </div>
        <div className="flex items-baseline gap-1.5" style={{ marginTop: 2 }}>
          <span style={{ fontSize: 22, fontWeight: 800, color: confColor, fontFamily: 'monospace' }}>
            {topConf != null ? topConf.toFixed(1) : '--'}
          </span>
          <span style={{ fontSize: 12, fontWeight: 700, color: confColor }}>%</span>
          <span className="uppercase" style={{ fontSize: 8, color: 'var(--text-muted)', marginLeft: 4 }}>
            confidence
          </span>
          {displayLatest?.embedding_dim != null && (
            <span className="uppercase" style={{ fontSize: 8, color: 'var(--text-muted)', marginLeft: 6 }}>
              {`dims ${displayLatest.embedding_dim}`}
            </span>
          )}
          {displayLatest?.alert && (
            <span className="pill" style={{
              marginLeft: 'auto', padding: '1px 6px', fontSize: 8, fontWeight: 700,
              background: 'rgba(244,63,94,0.18)', color: accents.red,
            }}>
              LOW / UNKNOWN
            </span>
          )}
        </div>
      </div>

      {/* Top-K breakdown with confidence bars */}
      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-1">
        {(displayLatest?.results ?? []).map((d, i) => {
          const c = i === 0 ? confColor : 'var(--text-secondary)';
          return (
            <div key={`${d.label}-${i}`} className="flex flex-col gap-0.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate" style={{ fontSize: 11, color: c }}>
                  {`#${i + 1} ${d.label}`}
                </span>
                <span style={{ fontSize: 11, fontWeight: 700, color: c, fontFamily: 'monospace' }}>
                  {d.confidence.toFixed(1)}%
                </span>
              </div>
              <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--secondary)' }}>
                <div style={{
                  width: `${Math.max(0, Math.min(100, d.confidence))}%`,
                  height: '100%', background: c, transition: 'width 0.2s',
                }} />
              </div>
            </div>
          );
        })}
        {(displayLatest?.results ?? []).length === 0 && (
          <div className="flex-1 flex items-center justify-center text-center px-2" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {!encoderLive
              ? 'Use START VIT AND VIDEO in the top bar to run webrtc_server.py'
              : !displayLatest
                ? 'Embeddings arriving — decoded labels will appear here'
                : 'No detections in this session yet'}
          </div>
        )}
      </div>

      {/* Embedding size — 1024 B is the physical centre of 512–2048 */}
      <div className="flex-shrink-0 flex flex-col gap-1">
        <div className="flex items-center justify-between" style={{ fontSize: 9, color: 'var(--text-muted)' }}>
          <span className="uppercase tracking-wider">Embedding Size</span>
          <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
            {maxEmbedBytes} B
          </span>
        </div>
        <div className="flex items-center gap-1.5" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
          <span
            className="inline-block shrink-0 rounded-sm"
            style={{
              width: 14,
              height: 8,
              background: VIT_CURRENT_EMBED_FILL,
              border: '1px solid rgba(100, 130, 165, 0.75)',
            }}
          />
          <span className="uppercase tracking-wider">
            Current embedding size
            {latestEmbedBytes != null && encoderLive
              ? ` · ${latestEmbedBytes} B`
              : ''}
          </span>
        </div>
        <div className="pt-1">
          <Slider
            value={[vitEmbedBytesToSliderIndex(maxEmbedBytes)]}
            min={0}
            max={VIT_SLIDER_INDEX_MAX}
            step={1}
            fillToValue={currentEmbedFillIndex}
            fillColor={VIT_CURRENT_EMBED_FILL}
            centerTickIndex={VIT_EMBED_CENTER_INDEX}
            onValueChange={(v) => onEmbedSliderChange(v[0])}
            onValueCommit={(v) => onEmbedSliderCommit(v[0])}
          />
        </div>
        <div className="relative h-3 w-full" style={{ fontSize: 8, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
          {VIT_EMBED_SIZE_OPTIONS.map((n, index) => (
            <span
              key={n}
              className="absolute -translate-x-1/2"
              style={{
                left: `${vitEmbedTickPercent(index)}%`,
                color: maxEmbedBytes === n ? 'var(--text-secondary)' : undefined,
                fontWeight: index === VIT_EMBED_CENTER_INDEX || maxEmbedBytes === n ? 700 : undefined,
              }}
            >
              {n}
            </span>
          ))}
        </div>
      </div>

      {/* Session controls */}
      <div className="flex-shrink-0 flex items-center gap-2">
        <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>
          {sessionCount} record{sessionCount === 1 ? '' : 's'}
        </span>
        <button
          onClick={exportCsv}
          disabled={exporting || sessionCount === 0}
          className="ml-auto flex items-center gap-1"
          title={sessionCount === 0 ? 'No records to export yet' : 'Export this session as CSV'}
          style={{
            padding: '3px 8px', borderRadius: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            border: '1px solid var(--accent-purple)',
            background: 'rgba(139,92,246,0.16)', color: 'var(--accent-purple)',
            cursor: exporting || sessionCount === 0 ? 'not-allowed' : 'pointer',
            opacity: exporting || sessionCount === 0 ? 0.5 : 1,
          }}
        >
          <Download size={11} />
          {exporting ? 'EXPORTING…' : 'EXPORT CSV'}
        </button>
        <button
          onClick={clearSession}
          disabled={sessionCount === 0}
          title="Clear session history"
          style={{
            padding: '3px 8px', borderRadius: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            border: '1px solid var(--stroke-strong)',
            background: 'var(--bg-surface)', color: 'var(--text-secondary)',
            cursor: sessionCount === 0 ? 'not-allowed' : 'pointer',
            opacity: sessionCount === 0 ? 0.5 : 1,
          }}
        >
          <Trash2 size={11} />
        </button>
      </div>
    </div>
  );
}

export const vitDecoderDef: WidgetDefinition = {
  id: 'vit_decoder_widget', name: 'VIT Scene Decoder', group: 'video',
  sizeClass: 'L', defaultSize: { w: 3, h: 4, minW: 2, minH: 3 },
  icon: 'ScanEye', pinned: false, component: VitDecoderWidget,
};

// STOP-TIME TEST BENCH — measure how long the robot takes to stop after EXPLORE.
// Command time is stamped on the Pi clock when START is pressed; the official run
// start is when the Pi reports movement. Stop time comes from Pi drive-status MQTT.
type StopModeApiResponse = {
  mode?: StopBenchMode;
  cache_script_running?: boolean;
  cache_script_detection_ready?: boolean;
  cache_script_launch_mode?: 'terminal';
  cache_script_log?: string;
  edge_aware_enabled?: boolean;
  status?: string;
  message?: string;
};

function cacheBenchStartReady(data: StopModeApiResponse): boolean {
  if (data.mode === 'edge_aware') return true;
  if (!benchNeedsPiScript(data.mode ?? 'edge_aware')) return true;
  return data.cache_script_running === true && data.cache_script_detection_ready === true;
}

type StopTestRun = {
  id: number;
  run: number;
  commandSentAt: number;
  startedAt: number;
  stoppedAt: number;
  durationMs: number;
  commandToMoveMs: number;
  stoppingDistance: string;
  networkType: string;
  stopMode: StopBenchMode;
  stopSource?: StopSource;
};

type DriveStatusPayload = {
  status?: string;
  robotTimestamp?: number | null;
  timestamp?: number | null;
  auto_mode?: boolean | null;
};

const NETWORK_OPTIONS = ['Wi-Fi', '5G', '4G/LTE', 'Ethernet', 'Other'] as const;
/** Pi statuses that explicitly mean the robot has halted. */
const DRIVE_STOP_STATUSES = new Set([
  'stopped',
  'auto_disabled',
  'estop_active',
  'auto_all_blocked_front_and_rear',
  'auto_waiting_for_scan',
]);
/** Not movement — but also not a run-ending stop (pre-move or post-estop-clear). */
const DRIVE_PRE_MOVE_STATUSES = new Set([
  'auto_enabled',
  'estop_cleared',
  'unknown',
]);
const ROBOT_TIME_POLL_MS = 100;
const MOVEMENT_WAIT_MS = 30000;
/** After a manual stop is sent, wait for Pi drive-status `stopped` before recording time. */
const STOP_COMMAND_WAIT_MS = 8000;

function isPiStopCommandStatus(status: string | undefined, acceptAutoDisabled = false): boolean {
  if (status === 'stopped') return true;
  if (acceptAutoDisabled && status === 'auto_disabled') return true;
  return false;
}

/** Pi drive-status values that mean the wheels are (or were just) in motion. */
function isMovementDriveStatus(status: string | undefined): boolean {
  if (!status) return false;
  if (DRIVE_STOP_STATUSES.has(status)) return false;
  if (DRIVE_PRE_MOVE_STATUSES.has(status)) return false;
  if (status.startsWith('blocked_by_estop')) return false;
  if (status.includes('stop')) return false;
  return true;
}

/** True when the Pi drive-status means e-stop (do not record as a completed test). */
function isEstopDriveStatus(status: string | undefined): boolean {
  if (!status) return false;
  if (status === 'estop_active') return true;
  return status.startsWith('blocked_by_estop');
}

/** True when the Pi reports the robot is no longer driving (incl. unlisted halt strings). */
function isStopDriveStatus(status: string | undefined): boolean {
  if (!status || status === 'unknown') return false;
  if (isEstopDriveStatus(status)) return false;
  if (DRIVE_STOP_STATUSES.has(status)) return true;
  // Any other non-movement status after we've seen motion (e.g. future Pi statuses).
  if (!isMovementDriveStatus(status) && !DRIVE_PRE_MOVE_STATUSES.has(status)) return true;
  return false;
}

/** Pi `time.time()` seconds (or ms) → epoch ms. */
function robotTimestampToMs(ts: unknown): number | null {
  if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) return null;
  return ts > 1e12 ? ts : ts * 1000;
}

function robotMsFromPayload(data: Record<string, unknown>): number | null {
  return robotTimestampToMs(data.robotTimestamp ?? data.timestamp);
}

async function fetchDriveStatus(): Promise<DriveStatusPayload | null> {
  try {
    const res = await fetch('/api/drive_status', { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json() as DriveStatusPayload;
  } catch {
    return null;
  }
}

async function fetchGridRobotMs(): Promise<number | null> {
  try {
    const res = await fetch('/api/grid_status', { cache: 'no-store' });
    if (!res.ok) return null;
    const data = await res.json() as Record<string, unknown>;
    const fromField = robotMsFromPayload(data);
    if (fromField != null) return fromField;
    if (typeof data.raw === 'string' && data.raw.trim()) {
      try {
        const parsed = JSON.parse(data.raw) as Record<string, unknown>;
        return robotMsFromPayload(parsed);
      } catch { /* ignore */ }
    }
    return null;
  } catch {
    return null;
  }
}

async function fetchLatestRobotMs(): Promise<number | null> {
  const drive = await fetchDriveStatus();
  const fromDrive = drive ? robotMsFromPayload(drive as Record<string, unknown>) : null;
  if (fromDrive != null) return fromDrive;
  return fetchGridRobotMs();
}

async function fetchBackendEstopActive(): Promise<boolean> {
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    if (!res.ok) return false;
    const data = await res.json() as { estop_active?: boolean };
    return data.estop_active === true;
  } catch {
    return false;
  }
}

async function fetchGridEstopActive(): Promise<boolean> {
  try {
    const res = await fetch('/api/grid_status', { cache: 'no-store' });
    if (!res.ok) return false;
    const data = await res.json() as Record<string, unknown>;
    if (data.estop === true || data.estop_active === true) return true;
    if (typeof data.raw === 'string' && data.raw.trim()) {
      try {
        const parsed = JSON.parse(data.raw) as Record<string, unknown>;
        return parsed.estop === true || parsed.estop_active === true;
      } catch { /* ignore */ }
    }
    return false;
  } catch {
    return false;
  }
}

/** Pick a stop timestamp that is never before the run movement start. */
function resolveStopMs(startTs: number, piTs: number | null): number {
  if (piTs != null && piTs >= startTs) return piTs;
  return Math.max(startTs, piTs ?? Date.now());
}

/** Sync dashboard e-stop UI when the backend/Pi latch outside the widget. */
function mirrorBackendEstop() {
  const state = useMetricsStore.getState();
  if (state.estopActive) return;
  useMetricsStore.setState({
    estopActive: true,
    currentCommand: 'STOP',
    missionStatus: 'E-STOP',
    mode: 'manual',
    autoMode: false,
    autoRunning: false,
    movementVec: null,
  });
}

/** Quote a CSV field only when it contains a comma, quote, or newline. */
function csvField(value: string | number): string {
  const s = String(value ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Seconds with 2 decimals, e.g. 1.74 s. */
function fmtSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)}`;
}

function formatRobotIso(ms: number): string {
  return new Date(ms).toISOString();
}

function StopTestBenchWidget() {
  const estopActive = useMetricsStore((s: MetricsState) => s.estopActive);
  const gridEstop = useMetricsStore((s: MetricsState) => s.latestGrid?.estop_active);
  const networkMode = useMetricsStore((s: MetricsState) => s.networkMode);

  const cachedBench = useRef(loadTestBenchCache());
  const userPickedNetworkRef = useRef(cachedBench.current.userPickedNetwork);
  const [runs, setRuns] = useState<StopTestRun[]>(() => cachedBench.current.runs);
  const [commandSentAt, setCommandSentAt] = useState<number | null>(null);
  const [activeStart, setActiveStart] = useState<number | null>(null);
  const [networkType, setNetworkType] = useState<string>(
    () => cachedBench.current.networkType ?? networkMode ?? 'Wi-Fi',
  );
  const [stopMode, setStopMode] = useState<StopBenchMode>(() => loadStopModePreference());
  const [cacheScriptReady, setCacheScriptReady] = useState(() => loadStopModePreference() === 'edge_aware');
  const [cacheScriptRunning, setCacheScriptRunning] = useState(false);
  const [modeSwitching, setModeSwitching] = useState(false);
  const [, setTick] = useState(0);
  const [frozenElapsedMs, setFrozenElapsedMs] = useState<number | null>(null);

  const commandSentAtRef = useRef<number | null>(null);
  const activeStartRef = useRef<number | null>(null);
  const armedRef = useRef(false);
  const sessionActiveRef = useRef(false);
  const lastPiMsRef = useRef<number | null>(null);
  const lastPiWallMsRef = useRef<number | null>(null);
  const movementDeadlineRef = useRef<number | null>(null);
  const stopCommandPendingRef = useRef(false);
  const stopCommandPendingAtRef = useRef<number | null>(null);
  const preMoveStopReasonRef = useRef<string | null>(null);
  const firstStopTsRef = useRef<number | null>(null);
  const stopSourceRef = useRef<StopSource | null>(null);
  const networkTypeRef = useRef(networkType);
  const stopModeRef = useRef(stopMode);
  useEffect(() => { networkTypeRef.current = networkType; }, [networkType]);
  useEffect(() => { stopModeRef.current = stopMode; }, [stopMode]);
  useEffect(() => { setTestBenchStopMode(stopMode); }, [stopMode]);

  const freezeTimerAtNow = useCallback(() => {
    const anchor = activeStartRef.current ?? commandSentAtRef.current;
    if (anchor == null) return;
    const piNow =
      lastPiMsRef.current != null && lastPiWallMsRef.current != null
        ? lastPiMsRef.current + (Date.now() - lastPiWallMsRef.current)
        : lastPiMsRef.current ?? anchor;
    setFrozenElapsedMs(Math.max(0, piNow - anchor));
  }, []);

  const latchStopSource = useCallback((source: StopSource) => {
    if (stopSourceRef.current != null) return;
    stopSourceRef.current = source;
  }, []);

  useEffect(() => {
    setTestBenchManualStopHook(() => {
      if (!sessionActiveRef.current) return;
      stopCommandPendingRef.current = true;
      stopCommandPendingAtRef.current = Date.now();
      firstStopTsRef.current = null;
      preMoveStopReasonRef.current = takeTestBenchStopReason() ?? null;
      const isStopLabel = takeTestBenchStopIsStopLabel();
      if (isStopLabel) {
        latchStopSource('edge_dashboard');
      } else {
        latchStopSource('manual');
      }
      freezeTimerAtNow();
    });
    return () => setTestBenchManualStopHook(null);
  }, [freezeTimerAtNow, latchStopSource]);

  const applyStopModeApi = useCallback((data: StopModeApiResponse) => {
    if (data.mode && STOP_BENCH_MODES.includes(data.mode)) {
      setStopMode(data.mode);
      const edgeEnabled = data.edge_aware_enabled === true
        || data.mode === 'edge_aware'
        || data.mode === 'hybrid';
      setEdgeAwareStopEnabled(edgeEnabled);
    }
    const mode = data.mode ?? stopModeRef.current;
    const running = data.cache_script_running === true;
    setCacheScriptRunning(benchNeedsPiScript(mode) && running);
    setCacheScriptReady(cacheBenchStartReady({ ...data, mode }));
  }, []);

  useEffect(() => {
    saveTestBenchCache({ runs });
  }, [runs]);

  useEffect(() => {
    saveTestBenchCache({ networkType, userPickedNetwork: userPickedNetworkRef.current });
  }, [networkType]);

  useEffect(() => {
    let alive = true;
    const preferred = loadStopModePreference();
    const load = async () => {
      try {
        const res = await fetch('/api/test_bench/stop_mode', { cache: 'no-store' });
        if (!res.ok || !alive) return;
        const data = await res.json() as StopModeApiResponse;
        if (data.mode !== preferred) {
          setModeSwitching(true);
          setCacheScriptReady(preferred === 'edge_aware');
          setCacheScriptRunning(false);
          const syncRes = await fetch('/api/test_bench/stop_mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: preferred }),
          });
          if (!alive) return;
          const syncData = await syncRes.json() as StopModeApiResponse;
          applyStopModeApi({ ...syncData, mode: preferred });
          setModeSwitching(false);
          if (!syncRes.ok) {
            useMetricsStore.getState().pushEvent(
              'error',
              syncData.message ?? 'Failed to sync stop mode with backend',
            );
            return;
          }
        } else {
          applyStopModeApi({ ...data, mode: preferred });
        }
      } catch { /* backend may be starting */ }
    };
    void load();
    return () => { alive = false; };
  }, [applyStopModeApi]);

  useEffect(() => {
    if (!benchNeedsPiScript(stopMode) || cacheScriptReady || modeSwitching) return;
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/test_bench/stop_mode?force=1', { cache: 'no-store' });
        if (!res.ok || !alive) return;
        const data = await res.json() as StopModeApiResponse;
        applyStopModeApi({ ...data, mode: data.mode ?? stopMode });
      } catch { /* backend unreachable */ }
    };
    void poll();
    const id = setInterval(() => { void poll(); }, 1500);
    return () => { alive = false; clearInterval(id); };
  }, [stopMode, cacheScriptReady, modeSwitching, applyStopModeApi]);

  const applyStopMode = async (mode: StopBenchMode) => {
    if (modeSwitching || sessionActiveRef.current) return;
    setModeSwitching(true);
    setCacheScriptReady(mode === 'edge_aware');
    setCacheScriptRunning(false);
    try {
      const res = await fetch('/api/test_bench/stop_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const data = await res.json() as StopModeApiResponse;
      if (!res.ok) {
        useMetricsStore.getState().pushEvent(
          'error',
          data.message ?? 'Failed to update stop mode on Pi',
        );
        const cur = await fetch('/api/test_bench/stop_mode', { cache: 'no-store' });
        if (cur.ok) applyStopModeApi(await cur.json() as StopModeApiResponse);
        return;
      }
      applyStopModeApi({ ...data, mode });
      saveStopModePreference(mode);
      if (benchNeedsPiScript(mode) && !data.cache_script_running) {
        useMetricsStore.getState().pushEvent(
          'warning',
          data.message ?? 'Pi cache-aware script did not start',
        );
      } else if (data.message) {
        useMetricsStore.getState().pushEvent('warning', data.message);
      } else {
        useMetricsStore.getState().pushEvent('info', `Stop mode: ${STOP_MODE_LABELS[mode]}`);
      }
    } catch {
      useMetricsStore.getState().pushEvent('error', 'Failed to reach backend for stop mode');
    } finally {
      setModeSwitching(false);
    }
  };

  useEffect(() => {
    if (!userPickedNetworkRef.current && networkMode) setNetworkType(networkMode);
  }, [networkMode]);

  const resetSession = useCallback(() => {
    const hadSession = sessionActiveRef.current;
    const recordedStopSource = stopSourceRef.current;
    commandSentAtRef.current = null;
    activeStartRef.current = null;
    armedRef.current = false;
    sessionActiveRef.current = false;
    lastPiMsRef.current = null;
    lastPiWallMsRef.current = null;
    movementDeadlineRef.current = null;
    stopCommandPendingRef.current = false;
    stopCommandPendingAtRef.current = null;
    firstStopTsRef.current = null;
    preMoveStopReasonRef.current = null;
    clearEdgeAwareStopLabelBenchStop();
    stopSourceRef.current = null;
    setFrozenElapsedMs(null);
    setStopLabelEstopArmed(false);
    setCommandSentAt(null);
    setActiveStart(null);
    // START always enables explore — release manual-drive lock when the session ends.
    if (hadSession && !skipAutoOffAfterBenchRun(recordedStopSource)) {
      const { autoRunning, autoMode } = useMetricsStore.getState();
      if (autoRunning || autoMode) {
        sendCommand('auto_off');
      }
    }
    setTestBenchSessionActive(false);
  }, []);

  const endRun = useCallback((stoppedAt: number) => {
    const startedAt = activeStartRef.current;
    const cmdAt = commandSentAtRef.current;
    if (startedAt == null || cmdAt == null) return;
    const recordedSource = stopSourceRef.current ?? 'manual';
    const mode = stopModeRef.current;
    resetSession();
    setRuns((prev) => [
      ...prev,
      {
        id: Date.now() + Math.random(),
        run: prev.length + 1,
        commandSentAt: cmdAt,
        startedAt,
        stoppedAt,
        durationMs: Math.max(0, stoppedAt - startedAt),
        commandToMoveMs: Math.max(0, startedAt - cmdAt),
        stoppingDistance: '',
        networkType: networkTypeRef.current,
        stopMode: mode,
        stopSource: recordedSource,
      },
    ]);
  }, [resetSession]);

  const disableAutoRoam = useCallback(() => {
    if (stopModeRef.current === 'cache_aware_offloading') return;
    if (stopModeRef.current === 'hybrid' && stopSourceRef.current === 'cache_pi') return;
    if (isEdgeAwareStopLabelBenchStop()) return;
    if (useMetricsStore.getState().autoRunning) {
      sendCommand('auto_off');
    }
  }, []);

  const tryEndRunOnPiStopCommand = useCallback(async (drive: DriveStatusPayload | null) => {
    const startTs = activeStartRef.current;
    if (startTs == null || !armedRef.current) return false;
    const acceptAutoDisabled = stopCommandPendingRef.current;
    if (!drive?.status || !isPiStopCommandStatus(drive.status, acceptAutoDisabled)) return false;

    const stopTs = robotMsFromPayload(drive as Record<string, unknown>);
    if (stopTs == null) return false;

    if (firstStopTsRef.current == null || stopTs < firstStopTsRef.current) {
      firstStopTsRef.current = stopTs;
    }

    if (!stopSourceRef.current) {
      if (isEdgeAwareStopLabelBenchStop() || stopCommandPendingRef.current) {
        latchStopSource('edge_dashboard');
      } else if (benchNeedsPiScript(stopModeRef.current)) {
        latchStopSource('cache_pi');
      }
    }

    if (
      stopModeRef.current === 'cache_aware_offloading'
      || stopModeRef.current === 'hybrid'
      || isEdgeAwareStopLabelBenchStop()
    ) {
      freezeTimerAtNow();
    } else {
      disableAutoRoam();
    }
    stopCommandPendingRef.current = false;
    stopCommandPendingAtRef.current = null;
    endRun(resolveStopMs(startTs, firstStopTsRef.current));
    return true;
  }, [disableAutoRoam, endRun, freezeTimerAtNow, latchStopSource]);

  const tryEndRunFromStopSignal = useCallback(async (drive: DriveStatusPayload | null) => {
    const startTs = activeStartRef.current;
    if (startTs == null || !armedRef.current) return;
    if (!stopSourceRef.current) {
      if (isEdgeAwareStopLabelBenchStop() || stopCommandPendingRef.current) {
        latchStopSource('edge_dashboard');
      } else if (benchNeedsPiScript(stopModeRef.current)) {
        latchStopSource('cache_pi');
      }
    }
    if (
      stopModeRef.current === 'cache_aware_offloading'
      || stopModeRef.current === 'hybrid'
      || isEdgeAwareStopLabelBenchStop()
    ) {
      freezeTimerAtNow();
    }
    const fromDrive = drive ? robotMsFromPayload(drive as Record<string, unknown>) : null;
    const stopTs = fromDrive ?? await fetchLatestRobotMs();
    endRun(resolveStopMs(startTs, stopTs));
  }, [endRun, freezeTimerAtNow, latchStopSource]);

  const cancelSession = useCallback((message: string) => {
    if (!sessionActiveRef.current) return;
    resetSession();
    useMetricsStore.getState().pushEvent('warning', message);
  }, [resetSession]);

  const cancelSessionOnEstop = useCallback(() => {
    cancelSession('Stop-time test — e-stop engaged (run not recorded)');
  }, [cancelSession]);

  const cancelPreMoveStop = useCallback((suffix?: string) => {
    const base = preMoveStopReasonRef.current
      ?? 'Stop-time test — stopped before movement started';
    cancelSession(suffix ? `${base} (${suffix})` : base);
  }, [cancelSession]);

  const startTest = async () => {
    if (sessionActiveRef.current) return;
    if (useMetricsStore.getState().estopActive) {
      useMetricsStore.getState().pushEvent('warning', 'Stop-time test blocked — clear E-stop first');
      return;
    }

    const commandTs = await fetchLatestRobotMs();
    if (commandTs == null) {
      useMetricsStore.getState().pushEvent(
        'warning',
        'Stop-time test — no Pi clock available (is mqtt_ros_node connected?)',
      );
      return;
    }

    sessionActiveRef.current = true;
    setTestBenchSessionActive(true);
    commandSentAtRef.current = commandTs;
    lastPiMsRef.current = commandTs;
    lastPiWallMsRef.current = Date.now();
    movementDeadlineRef.current = Date.now() + MOVEMENT_WAIT_MS;

    let ignoreDecodeKey: string | null = null;
    try {
      const vitRes = await fetch('/api/vit/status', { cache: 'no-store' });
      if (vitRes.ok) {
        const vit = await vitRes.json() as VitStatusForStopLabel;
        ignoreDecodeKey = vitDecodeEventKey(vit);
      }
    } catch { /* VIT may be offline */ }

    setStopLabelEstopArmed(true, ignoreDecodeKey);
    setCommandSentAt(commandTs);
    setTick((n) => n + 1);
    toggleRosAuto();
  };

  // Single session poll — Pi time, movement start, stop detection, live timer tick.
  useEffect(() => {
    if (commandSentAt == null) return;
    let alive = true;

    const syncPiSample = (ts: number) => {
      if (lastPiMsRef.current !== ts) {
        lastPiMsRef.current = ts;
        lastPiWallMsRef.current = Date.now();
      }
    };

    const poll = async () => {
      if (!alive) return;

      const ts = await fetchLatestRobotMs();
      if (alive && ts != null) syncPiSample(ts);
      if (!alive) return;

      const drive = await fetchDriveStatus();
      if (!alive) return;

      if (activeStartRef.current == null) {
        if (stopCommandPendingRef.current) {
          if (drive?.status && isPiStopCommandStatus(drive.status, true)) {
            if (!isEdgeAwareStopLabelBenchStop()) {
              disableAutoRoam();
            } else {
              freezeTimerAtNow();
            }
            stopCommandPendingRef.current = false;
            stopCommandPendingAtRef.current = null;
            cancelPreMoveStop();
            return;
          }
          const pendingAt = stopCommandPendingAtRef.current;
          if (pendingAt != null && Date.now() - pendingAt > STOP_COMMAND_WAIT_MS) {
            if (!isEdgeAwareStopLabelBenchStop()) {
              disableAutoRoam();
            } else {
              freezeTimerAtNow();
            }
            stopCommandPendingRef.current = false;
            stopCommandPendingAtRef.current = null;
            cancelPreMoveStop('timeout');
            return;
          }
          return;
        }

        const deadline = movementDeadlineRef.current;
        if (deadline != null && Date.now() > deadline) {
          cancelSession('Stop-time test — robot did not start moving in time');
          return;
        }

        const backendEstop = await fetchBackendEstopActive();
        const gridEstopNow = await fetchGridEstopActive();
        if (backendEstop || gridEstopNow || useMetricsStore.getState().estopActive) {
          if (backendEstop || gridEstopNow) mirrorBackendEstop();
          cancelSessionOnEstop();
          return;
        }

        if (drive?.status) {
          const moveTs = robotMsFromPayload(drive as Record<string, unknown>);
          const cmdAt = commandSentAtRef.current;
          if (
            moveTs != null
            && cmdAt != null
            && moveTs > cmdAt
            && isMovementDriveStatus(drive.status)
          ) {
            activeStartRef.current = moveTs;
            armedRef.current = true;
            setActiveStart(moveTs);
            syncPiSample(moveTs);
          }
        }
        return;
      }

      if (!armedRef.current) return;

      if (
        drive?.status
        && isPiStopCommandStatus(drive.status, stopCommandPendingRef.current)
      ) {
        await tryEndRunOnPiStopCommand(drive);
        return;
      }

      if (stopCommandPendingRef.current) {
        const pendingAt = stopCommandPendingAtRef.current;
        if (pendingAt != null && Date.now() - pendingAt > STOP_COMMAND_WAIT_MS) {
          stopCommandPendingRef.current = false;
          stopCommandPendingAtRef.current = null;
          if (!isEdgeAwareStopLabelBenchStop()) {
            disableAutoRoam();
          } else {
            freezeTimerAtNow();
          }
          await tryEndRunFromStopSignal(drive);
        }
        return;
      }

      const backendEstop = await fetchBackendEstopActive();
      const gridEstopNow = await fetchGridEstopActive();
      const driveStop = drive?.status ? isStopDriveStatus(drive.status) : false;

      if (backendEstop || gridEstopNow || useMetricsStore.getState().estopActive) {
        if (backendEstop || gridEstopNow) mirrorBackendEstop();
        cancelSessionOnEstop();
        return;
      }

      if (drive?.status && isEstopDriveStatus(drive.status)) {
        mirrorBackendEstop();
        cancelSessionOnEstop();
        return;
      }

      if (drive?.status && driveStop) {
        await tryEndRunFromStopSignal(drive);
      }
    };

    const id = setInterval(() => { void poll(); }, ROBOT_TIME_POLL_MS);
    void poll();
    return () => { alive = false; clearInterval(id); };
  }, [cancelPreMoveStop, cancelSession, cancelSessionOnEstop, commandSentAt, disableAutoRoam, freezeTimerAtNow, latchStopSource, tryEndRunFromStopSignal, tryEndRunOnPiStopCommand]);

  // Re-render ~10×/s so the live Pi-clock extrapolation advances between MQTT samples.
  useEffect(() => {
    if (commandSentAt == null || frozenElapsedMs != null) return;
    const id = setInterval(() => setTick((n) => n + 1), 100);
    return () => clearInterval(id);
  }, [commandSentAt, frozenElapsedMs]);

  // E-stop during an active session cancels without recording (any phase).
  useEffect(() => {
    if (commandSentAt == null) return;
    if (!estopActive && !gridEstop) return;
    cancelSessionOnEstop();
  }, [cancelSessionOnEstop, commandSentAt, estopActive, gridEstop]);

  const updateRun = (id: number, patch: Partial<StopTestRun>) =>
    setRuns((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const exportCsv = () => {
    if (runs.length === 0) return;
    const headers = [
      'Run',
      'Command Time (Pi)',
      'Movement Start (Pi)',
      'Stop Time (Pi)',
      'Command-to-Move (ms)',
      'Stop Duration (ms)',
      'Stop Time (s)',
      'Stopping Distance (m)',
      'Network Type',
      'Stop Mode',
      'Stop Source',
    ];
    const lines = runs.map((r) => [
      r.run,
      formatRobotIso(r.commandSentAt),
      formatRobotIso(r.startedAt),
      formatRobotIso(r.stoppedAt),
      r.commandToMoveMs,
      r.durationMs,
      fmtSeconds(r.durationMs),
      csvField(r.stoppingDistance),
      csvField(r.networkType),
      csvField(STOP_MODE_LABELS[r.stopMode]),
      csvField(r.stopSource ? STOP_SOURCE_LABELS[r.stopSource] : '—'),
    ].join(','));
    const csv = [headers.join(','), ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `stop_time_test_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const clearRuns = () => {
    resetSession();
    setRuns([]);
    clearTestBenchCache();
  };

  const waitingForMovement = commandSentAt != null && activeStart == null && frozenElapsedMs == null;
  const running = activeStart != null && frozenElapsedMs == null;
  const stopping = frozenElapsedMs != null;
  const sessionActive = commandSentAt != null;
  const displayPiNow =
    lastPiMsRef.current != null && lastPiWallMsRef.current != null
      ? lastPiMsRef.current + (Date.now() - lastPiWallMsRef.current)
      : null;
  const elapsedAnchor = activeStart ?? commandSentAt;
  const elapsedMs = frozenElapsedMs != null
    ? frozenElapsedMs
    : sessionActive && displayPiNow != null && elapsedAnchor != null
      ? Math.max(0, displayPiNow - elapsedAnchor)
      : 0;
  const cacheStartBlocked = benchNeedsPiScript(stopMode) && !cacheScriptReady;
  const cacheWaitingEmbedding = benchNeedsPiScript(stopMode) && cacheScriptRunning && !cacheScriptReady;
  const stopModeIndex = STOP_BENCH_MODES.indexOf(stopMode);
  const startBlocked = sessionActive || estopActive || modeSwitching || cacheStartBlocked;
  const benchPillLabel = modeSwitching
    ? 'SCRIPT…'
    : cacheWaitingEmbedding
      ? 'WARMUP'
      : stopping
      ? 'STOPPING'
      : waitingForMovement
        ? 'WAITING'
        : running
          ? 'RUNNING'
          : cacheStartBlocked
            ? 'NO SCRIPT'
            : 'IDLE';
  const benchPillColor = modeSwitching
    ? accents.cyan
    : cacheWaitingEmbedding
      ? accents.purple
      : stopping
      ? accents.yellow
      : waitingForMovement
        ? accents.yellow
        : running
          ? accents.green
          : cacheStartBlocked
            ? accents.red
            : 'var(--text-muted)';
  const benchPillBg = modeSwitching
    ? 'rgba(6,182,212,0.18)'
    : cacheWaitingEmbedding
      ? 'rgba(168,85,247,0.18)'
      : stopping
      ? 'rgba(249,115,22,0.18)'
      : waitingForMovement
        ? 'rgba(245,158,11,0.18)'
        : running
          ? 'rgba(34,197,94,0.18)'
          : cacheStartBlocked
            ? 'rgba(239,68,68,0.18)'
            : 'var(--secondary)';
  const inputStyle: React.CSSProperties = {
    width: '100%', background: 'var(--bg-surface)', color: 'var(--text-primary)',
    border: '1px solid var(--stroke-subtle)', borderRadius: 6,
    padding: '3px 6px', fontSize: 11, outline: 'none',
  };

  return (
    <div className="h-full flex flex-col gap-1.5 min-h-0">
      {/* Header */}
      <div className="flex-shrink-0 flex items-center justify-between gap-2 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)', fontSize: 9, lineHeight: 1.1 }}>
        <div className="flex items-center gap-1 min-w-0">
          <FlaskConical size={11} style={{ color: accents.cyan, flexShrink: 0 }} />
          <span className="truncate">Stop-Time Test Bench</span>
        </div>
        <span className="pill" style={{
          padding: '1px 6px', fontSize: 8, fontWeight: 700,
          background: benchPillBg,
          color: benchPillColor,
        }}>
          {benchPillLabel}
        </span>
      </div>

      {/* Run configuration */}
      <div className="flex-shrink-0 grid grid-cols-2 gap-1.5">
        <label className="flex flex-col gap-0.5">
          <span className="uppercase tracking-wider" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
            Network Type
          </span>
          <select
            value={networkType}
            onChange={(e) => { userPickedNetworkRef.current = true; setNetworkType(e.target.value); }}
            style={inputStyle}
          >
            {NETWORK_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            {!NETWORK_OPTIONS.includes(networkType as typeof NETWORK_OPTIONS[number]) && (
              <option value={networkType}>{networkType}</option>
            )}
          </select>
        </label>
        <div className="flex flex-col gap-0.5">
          <span className="uppercase tracking-wider" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
            Detected
          </span>
          <div className="truncate" style={{
            ...inputStyle, display: 'flex', alignItems: 'center',
            color: 'var(--text-secondary)', fontFamily: 'monospace',
          }}>
            {networkMode ?? '—'}
          </div>
        </div>
      </div>

      {/* Stop mode slider — cache | hybrid | edge (default right) */}
      <div className="flex-shrink-0 rounded-xl px-2.5 py-2"
        style={{ background: 'rgba(0,0,0,0.12)', border: '1px solid var(--stroke-subtle)' }}>
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <span className="uppercase tracking-wider" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
            Stop mode
          </span>
          <span className="pill truncate" style={{
            padding: '1px 6px', fontSize: 8, fontWeight: 700, maxWidth: '55%',
            background: stopMode === 'edge_aware'
              ? 'rgba(6,182,212,0.18)'
              : stopMode === 'hybrid'
                ? 'rgba(139,92,246,0.18)'
                : 'var(--secondary)',
            color: stopMode === 'edge_aware'
              ? accents.cyan
              : stopMode === 'hybrid'
                ? accents.purple
                : 'var(--text-muted)',
          }}>
            {STOP_MODE_LABELS[stopMode]}
          </span>
        </div>
        <div className="flex items-center gap-2 mb-1">
          <span style={{
            fontSize: 8, fontWeight: stopMode === 'cache_aware_offloading' ? 700 : 500,
            color: stopMode === 'cache_aware_offloading' ? 'var(--text-primary)' : 'var(--text-muted)',
            lineHeight: 1.2, flex: 1, textAlign: 'left',
          }}>
            Cache
          </span>
          <span style={{
            fontSize: 8, fontWeight: stopMode === 'hybrid' ? 700 : 500,
            color: stopMode === 'hybrid' ? accents.purple : 'var(--text-muted)',
            lineHeight: 1.2, flex: 1, textAlign: 'center',
          }}>
            Hybrid
          </span>
          <span style={{
            fontSize: 8, fontWeight: stopMode === 'edge_aware' ? 700 : 500,
            color: stopMode === 'edge_aware' ? accents.cyan : 'var(--text-muted)',
            lineHeight: 1.2, flex: 1, textAlign: 'right',
          }}>
            Edge
          </span>
        </div>
        <Slider
          min={0}
          max={2}
          step={1}
          value={[stopModeIndex >= 0 ? stopModeIndex : 2]}
          centerTickIndex={1}
          fillToValue={stopModeIndex >= 0 ? stopModeIndex : 2}
          fillColor={
            stopMode === 'edge_aware'
              ? 'rgba(6,182,212,0.45)'
              : stopMode === 'hybrid'
                ? 'rgba(139,92,246,0.45)'
                : 'rgba(100,130,165,0.45)'
          }
          hideRange
          disabled={sessionActive || modeSwitching}
          onValueChange={(vals) => {
            const idx = vals[0];
            if (idx == null || idx < 0 || idx >= STOP_BENCH_MODES.length) return;
            const next = STOP_BENCH_MODES[idx];
            if (next !== stopMode) void applyStopMode(next);
          }}
          className="py-1"
        />
        {stopMode === 'edge_aware' && (
          <p style={{ margin: '6px 0 0', fontSize: 9, color: 'var(--text-muted)', lineHeight: 1.35 }}>
            VIT bottle detection on the dashboard sends auto_off + stop. Requires VIT and video.
          </p>
        )}
        {stopMode === 'hybrid' && (
          <p style={{ margin: '6px 0 0', fontSize: 9, color: 'var(--text-muted)', lineHeight: 1.35 }}>
            {modeSwitching
              ? 'Starting cache_aware_offloading.py on the Pi…'
              : cacheScriptReady
                ? 'Pi script + dashboard VIT both armed — first detector wins; run records which stopped.'
                : cacheScriptRunning
                  ? 'Waiting for bottle embedding — ensure VIT and video are running.'
                  : 'Waiting for Pi script — START is disabled.'}
          </p>
        )}
        {stopMode === 'cache_aware_offloading' && (
          <p style={{ margin: '6px 0 0', fontSize: 9, color: 'var(--text-muted)', lineHeight: 1.35 }}>
            {modeSwitching
              ? 'Starting cache_aware_offloading.py on the Pi…'
              : cacheScriptReady
                ? 'Pi script in lxterminal — stop from Pi only.'
                : cacheScriptRunning
                  ? 'Waiting for bottle embedding — ensure VIT and video are running.'
                  : 'Waiting for cache_aware_offloading.py on the Pi — START is disabled.'}
          </p>
        )}
      </div>

      {/* Live timer + start/stop */}
      <div className="flex-shrink-0 flex items-center gap-2 rounded-xl px-3 py-2"
        style={{ background: 'rgba(0,0,0,0.18)', border: '1px solid var(--stroke-subtle)' }}>
        <div className="flex flex-col min-w-0">
          <span className="uppercase tracking-wider" style={{ fontSize: 8, color: 'var(--text-muted)' }}>
            {stopping
              ? 'Stopped at (Pi)'
              : running
                ? 'Stop elapsed (Pi)'
                : waitingForMovement
                  ? 'Since command (Pi)'
                  : 'Elapsed (Pi)'}
          </span>
          <span style={{
            fontSize: 24, fontWeight: 800, fontFamily: 'monospace',
            color: stopping
              ? accents.yellow
              : running
                ? accents.green
                : waitingForMovement
                  ? accents.yellow
                  : 'var(--text-primary)',
            lineHeight: 1.1,
          }}>
            {fmtSeconds(elapsedMs)}<span style={{ fontSize: 12, marginLeft: 2 }}>s</span>
          </span>
        </div>
        <button
          type="button"
          onClick={() => { void startTest(); }}
          disabled={startBlocked}
          className="ml-auto flex items-center justify-center gap-1.5 rounded-xl transition-all"
          style={{
            minWidth: 92, padding: '10px 14px',
            fontWeight: 800, fontSize: 12, letterSpacing: '0.06em', color: '#fff',
            cursor: startBlocked ? 'not-allowed' : 'pointer',
            opacity: startBlocked ? 0.5 : 1,
            background: 'linear-gradient(135deg, var(--state-success), #14532d)',
            border: '1px solid rgba(255,255,255,0.2)',
            boxShadow: '0 8px 24px rgba(34,197,94,0.4), inset 0 1px 0 rgba(255,255,255,0.2)',
          }}
          title={
            sessionActive
              ? 'Test in progress — ends when the robot stops'
              : modeSwitching
                ? 'Stop mode switching — waiting for Pi script'
                : cacheWaitingEmbedding
                  ? 'Waiting for bottle embedding on the Pi'
                  : cacheStartBlocked
                    ? 'Cache-aware script must be running on the Pi before START'
                  : estopActive
                    ? 'E-stop active — clear it first'
                    : 'Start a run (sends explore command)'
          }
        >
          <Play size={13} fill="#fff" />
          START
        </button>
      </div>

      {/* Recorded runs */}
      <div className="flex-1 min-h-0 overflow-y-auto rounded-xl"
        style={{ background: 'rgba(0,0,0,0.18)', border: '1px solid var(--stroke-subtle)' }}>
        {/* Column header */}
        <div className="sticky top-0 grid items-center gap-1 px-2 py-1 uppercase tracking-wider"
          style={{
            gridTemplateColumns: '22px 48px 64px 1fr 1fr 72px',
            fontSize: 8,
            color: 'var(--text-muted)',
            background: 'var(--bg-elevated)',
            borderBottom: '1px solid var(--stroke-subtle)',
          }}>
          <span>#</span>
          <span>Stop</span>
          <span>Mode</span>
          <span>Stopped by</span>
          <span>Dist (m)</span>
          <span>Net</span>
        </div>

        {runs.length === 0 ? (
          <div className="flex items-center justify-center text-center px-3 py-6"
            style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Press START to begin a run. Stop time is measured from when the Pi reports movement.
          </div>
        ) : (
          runs.map((r) => (
            <div key={r.id} className="grid items-center gap-1 px-2 py-1 border-b"
              style={{
                gridTemplateColumns: '22px 48px 64px 1fr 1fr 72px',
                borderColor: 'var(--stroke-subtle)',
              }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                {r.run}
              </span>
              <span style={{ fontSize: 11, fontWeight: 800, color: accents.cyan, fontFamily: 'monospace' }}>
                {fmtSeconds(r.durationMs)}
              </span>
              <span
                className="truncate pill"
                title={STOP_MODE_LABELS[r.stopMode]}
                style={{
                  fontSize: 7,
                  fontWeight: 700,
                  padding: '1px 4px',
                  textAlign: 'center',
                  background:
                    r.stopMode === 'edge_aware'
                      ? 'rgba(6,182,212,0.18)'
                      : r.stopMode === 'hybrid'
                        ? 'rgba(139,92,246,0.18)'
                        : 'var(--secondary)',
                  color:
                    r.stopMode === 'edge_aware'
                      ? accents.cyan
                      : r.stopMode === 'hybrid'
                        ? accents.purple
                        : 'var(--text-secondary)',
                }}
              >
                {STOP_MODE_LABELS[r.stopMode]}
              </span>
              <span
                className="truncate"
                title={r.stopSource ? STOP_SOURCE_LABELS[r.stopSource] : 'Unknown'}
                style={{
                  fontSize: 8,
                  fontWeight: 600,
                  color:
                    r.stopSource === 'edge_dashboard'
                      ? accents.cyan
                      : r.stopSource === 'cache_pi'
                        ? accents.purple
                        : 'var(--text-muted)',
                }}
              >
                {r.stopSource ? STOP_SOURCE_LABELS[r.stopSource] : '—'}
              </span>
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                placeholder="—"
                value={r.stoppingDistance}
                onChange={(e) => updateRun(r.id, { stoppingDistance: e.target.value })}
                style={{ ...inputStyle, padding: '2px 5px', fontFamily: 'monospace', minWidth: 0 }}
              />
              <select
                value={r.networkType}
                onChange={(e) => updateRun(r.id, { networkType: e.target.value })}
                style={{ ...inputStyle, padding: '2px 5px', minWidth: 0 }}
              >
                {NETWORK_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
                {!NETWORK_OPTIONS.includes(r.networkType as typeof NETWORK_OPTIONS[number]) && (
                  <option value={r.networkType}>{r.networkType}</option>
                )}
              </select>
            </div>
          ))
        )}
      </div>

      {/* Footer controls */}
      <div className="flex-shrink-0 flex items-center gap-2">
        <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>
          {runs.length} run{runs.length === 1 ? '' : 's'}
        </span>
        <button
          onClick={exportCsv}
          disabled={runs.length === 0}
          className="ml-auto flex items-center gap-1"
          title={runs.length === 0 ? 'No runs to export yet' : 'Export all runs as CSV'}
          style={{
            padding: '3px 8px', borderRadius: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            border: '1px solid var(--accent-purple)',
            background: 'rgba(139,92,246,0.16)', color: 'var(--accent-purple)',
            cursor: runs.length === 0 ? 'not-allowed' : 'pointer',
            opacity: runs.length === 0 ? 0.5 : 1,
          }}
        >
          <Download size={11} />
          EXPORT CSV
        </button>
        <button
          onClick={clearRuns}
          disabled={runs.length === 0 && !sessionActive}
          title="Clear all recorded runs"
          style={{
            padding: '3px 8px', borderRadius: 6, fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
            border: '1px solid var(--stroke-strong)',
            background: 'var(--bg-surface)', color: 'var(--text-secondary)',
            cursor: runs.length === 0 && !sessionActive ? 'not-allowed' : 'pointer',
            opacity: runs.length === 0 && !sessionActive ? 0.5 : 1,
          }}
        >
          <Trash2 size={11} />
        </button>
      </div>
    </div>
  );
}

export const stopTestBenchDef: WidgetDefinition = {
  id: 'stop_test_bench_widget', name: 'Stop-Time Test Bench', group: 'control',
  sizeClass: 'L', defaultSize: { w: 3, h: 4, minW: 2, minH: 3 },
  icon: 'FlaskConical', pinned: false, component: StopTestBenchWidget,
};

// Widget registry — consumed by the picker and addWidget.
export const WIDGET_REGISTRY: WidgetDefinition[] = [
  videoFeedDef,
  systemStatusDef,
  lidarScanDef,
  slamMapDef,
  vitDecoderDef,
  movementJoystickDef,
  cameraJoystickDef,
  stopButtonDef,
  rosAutoButtonDef,
  stopTestBenchDef,
  eventLogDef,
];

/** Keyed by widget ID for O(1) lookup in DashboardGrid. */
export const WIDGET_BY_ID: Record<string, WidgetDefinition> = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.id, w])
);
