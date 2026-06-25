// Shared types for widgets, layout, and metrics.

import type { ComponentType } from 'react';

/** Widget category used for grouping in the picker overlay. */
export type WidgetGroup = 'connectivity' | 'video' | 'health' | 'control' | 'logging';

/** T-shirt size hint used by the picker UI to describe widget footprint. */
export type SizeClass = 'S' | 'M' | 'L' | 'XL' | 'FULL';

/** Grid dimensions passed to react-grid-layout when spawning a widget. */
export interface GridSize {
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  maxW?: number;
  maxH?: number;
}

/** Full static description of a widget that the registry and picker consume. */
export interface WidgetDefinition {
  id: string;
  name: string;
  group: WidgetGroup;
  sizeClass: SizeClass;
  defaultSize: GridSize;
  /** Lucide icon name (looked up dynamically in the picker). */
  icon: string;
  /** Pinned widgets are excluded from the picker (they live in the TopBar). */
  pinned: boolean;
  component: ComponentType;
}

export type ClientMode = 'manual' | 'auto';

export interface LiveGridData {
  raw?: string;
  status?: string;
  resolution?: number | null;
  size?: number | { rows?: number; cols?: number; width?: number; height?: number } | [number, number] | null;
  w: number;
  h: number;
  grid: number[] | null;
  robot_row: number | null;
  robot_col: number | null;
  front: number | null;
  left: number | null;
  right: number | null;
  auto_mode?: boolean;
  estop_active?: boolean;
  timestamp?: number | string | null;
  updatedAt?: number | null;
}

// Robot telemetry — updated by hooks polling backend /api/* endpoints.
export interface MetricsState {
  connectionStatus: 'CONNECTED' | 'DISCONNECTED' | 'RECONNECTING';
  networkMode: '5G' | 'Wi-Fi' | 'Unknown' | null;
  latencyMs: number | null;
  lastHeartbeat: string | null;
  missionStatus:
    | 'IDLE'
    | 'MANUAL CONTROL'
    | 'AI ASSISTED'
    | 'AUTO MODE'
    | 'STOPPED'
    | 'E-STOP'
    | 'ERROR'
    | 'RETURNING TO SAFE STATE';
  currentCommand: 'FORWARD' | 'BACKWARD' | 'LEFT' | 'RIGHT' | 'STOP' | 'IDLE' | 'AUTO ON' | 'AUTO OFF';
  commandAck: 'ACK_EXECUTING' | 'ACK_RECEIVED' | 'NACK_FAILED' | 'TIMEOUT' | 'WAITING';
  commandSuccessRate: number | null;
  videoStreamStatus: 'LIVE' | 'BUFFERING' | 'OFFLINE';
  videoFps: number | null;
  videoDelayMs: number | null;
  aiModelStatus: 'RUNNING' | 'IDLE' | 'LOADING' | 'ERROR' | 'OFFLINE';
  detections: Array<{ label: string; confidence: number }>;
  batteryPercent: number | null;
  cpuPercent: number | null;
  memoryPercent: number | null;
  ros2BridgeStatus: 'ACTIVE' | 'INACTIVE' | 'ERROR' | null;
  mqttLinkStatus: 'CONNECTED' | 'DISCONNECTED' | 'ERROR';
  cameraSensorStatus: 'OK' | 'NO SIGNAL' | 'ERROR' | null;
  events: Array<{ id: number; timestamp: string; level: 'info' | 'warning' | 'error'; message: string; tag?: string }>;
  /** Rolling 30-sample history arrays, used by sparkline charts. */
  latencyHistory: number[];
  cpuHistory: number[];
  /** When true, sendCommand blocks all non-stop commands until explicitly cleared. */
  estopActive: boolean;
  /** Ignore backend e-stop re-latch until this timestamp (ms) after manual resume. */
  estopIgnoreLatchUntil: number;
  mode: ClientMode;
  /** When true, release events do not publish stop because ROS auto mode owns stopping. */
  autoMode: boolean;
  /** Robot ROS auto mode — set from auto_on/auto_off MQTT commands only. */
  autoRunning: boolean;
  latestGrid: LiveGridData | null;
  frontDistance: number | null;
  leftDistance: number | null;
  rightDistance: number | null;
  safetyStatus: string;
  safetyGraceStatus: string | null;
  driveStatus: string;
  /**
   * Normalised joystick vector for the last active movement command.
   * { x: 0, y: 0 } when stopped/idle, null when a safety stop clears the stick.
   * Written by sendCommand, useKeyboardMovement, and useConnectionSync.
   */
  movementVec: { x: number; y: number } | null;
  /** Normalised arrow-key camera vector for joystick thumb mirroring. */
  cameraKeyboardVec: { x: number; y: number };
  /** True while video_server.py is known to be running on the Pi. Synced from /api/status. */
  streamRunning: boolean;
}
