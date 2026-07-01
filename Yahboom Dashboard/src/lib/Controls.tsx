import { useMetricsStore } from '../app/store';
import { notifyTestBenchManualStop, notifyTestBenchStopLabelStop, benchModeHasDashboardBottleStop, skipAutoOffOnBenchStop } from './testBenchSession';
/**
 * Engage or release the emergency stop on both the local store and the shared
 * backend, so every connected client reflects the change within ~3 seconds.
 */
export async function setEstopState(active: boolean): Promise<void> {
  if (active && useMetricsStore.getState().autoRunning) {
    sendCommand('auto_off');
  }

  // Optimistic local update — UI responds immediately.
  useMetricsStore.setState({
    estopActive:    active,
    estopIgnoreLatchUntil: active ? 0 : Date.now() + 3000,
    currentCommand: active ? 'STOP' : 'IDLE',
    missionStatus:  active ? 'E-STOP' : 'IDLE',
    mode:           'manual',
    autoMode:       active ? false : useMetricsStore.getState().autoMode,
    autoRunning:    active ? false : useMetricsStore.getState().autoRunning,
    movementVec:    active ? null : useMetricsStore.getState().movementVec,
  });
  if (active) {
    useMetricsStore.getState().pushEvent('warning', 'Emergency stop engaged', 'estop');
  }

  try {
    await fetch('/api/estop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
    });
  } catch {
    // Backend unreachable — local state is still updated.
  }
}

export type MovementCommand =
  | 'fwd' | 'bck' | 'left' | 'right'
  | 'fwdleft' | 'fwdright' | 'bckleft' | 'bckright'
  | 'stop';

export type AutoMoveCommand = 'auto_on' | 'auto_off';

export type EStopCommand = 'estop_on' | 'estop_off';

export type BotCommand = MovementCommand | AutoMoveCommand | EStopCommand;

const MOVEMENT_COMMANDS = new Set<MovementCommand>([
  'fwd', 'bck', 'left', 'right',
  'fwdleft', 'fwdright', 'bckleft', 'bckright',
  'stop',
]);

function isMovementCommand(command: BotCommand): command is MovementCommand {
  return MOVEMENT_COMMANDS.has(command as MovementCommand);
}

export type CameraCommand =
  | 'up' | 'down' | 'cright' | 'cleft'
  | 'upcright' | 'upcleft' | 'downcright' | 'downcleft'
  | 'crst' | 'cstop';

/**
 * Who is allowed to send a 'stop' command:
 *  'release' — a WASD key-up or joystick pointer-up event.
 *  'manual'  — an explicit stop key/button action.
 *  'estop'   — the emergency-stop button or X-key shortcut (always honoured).
 */
export type StopSource = 'release' | 'manual' | 'estop' | 'stop_label';
export type CommandSource = StopSource;

function commandState(command: BotCommand, source?: CommandSource) {
  if (command === 'auto_on') {
    return {
      currentCommand: 'AUTO ON' as const,
      missionStatus: 'AUTO MODE' as const,
      mode: 'auto' as const,
      autoMode: true,
      autoRunning: true,
      movementVec: null,
    };
  }
  if (command === 'auto_off') {
    if (useMetricsStore.getState().estopActive) {
      return {
        currentCommand: 'STOP' as const,
        missionStatus: useMetricsStore.getState().missionStatus === 'E-STOP' ? 'E-STOP' as const : 'STOPPED' as const,
        mode: 'manual' as const,
        autoMode: false,
        autoRunning: false,
        movementVec: null,
      };
    }

    return {
      currentCommand: 'AUTO OFF' as const,
      missionStatus: 'IDLE' as const,
      mode: 'manual' as const,
      autoMode: false,
      autoRunning: false,
      movementVec: null,
    };
  }
  if (command === 'estop_on') {
    return {
      currentCommand: 'STOP' as const,
      missionStatus: 'E-STOP' as const,
      mode: 'manual' as const,
      autoMode: false,
      autoRunning: false,
      movementVec: null,
    };
  }
  if (command === 'estop_off') {
    return {
      currentCommand: 'IDLE' as const,
      missionStatus: 'IDLE' as const,
      mode: 'manual' as const,
      movementVec: null,
    };
  }

  return {
    currentCommand: (command === 'stop' ? 'STOP' : command.toUpperCase()) as never,
    missionStatus: command === 'stop' ? 'IDLE' as const : 'MANUAL CONTROL' as const,
    mode: 'manual' as const,
    movementVec: cmdToMovementVec(command === 'stop' ? null : command),
  };
}

async function postBotCommand(command: BotCommand): Promise<void> {
  const res = await fetch('/api/send_command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command }),
  });
  const data: { status: string; command: string; topic?: string; latency?: number; message?: string } =
    await res.json();

  if (!res.ok) {
    useMetricsStore.getState().pushEvent(
      'error',
      `Command '${command}' rejected — ${data.message ?? res.statusText}`,
      data.topic ?? 'yahboom/cmd',
    );
    return;
  }

  const latencyStr = data.latency != null ? `${data.latency}ms` : '—';
  useMetricsStore.getState().pushEvent(
    'info',
    `POST -> ${data.topic ?? 'yahboom/cmd'}: ${data.command ?? command} (${latencyStr} publish)`,
    data.topic ?? 'yahboom/cmd',
  );
  useMetricsStore.setState({
    latencyMs: data.latency ?? useMetricsStore.getState().latencyMs,
  });
}

export function sendCommand(command: BotCommand, source?: CommandSource): void {
  const state = useMetricsStore.getState();

  // Edge-aware bottle stop — auto_off + stop in parallel; freeze test-bench timer.
  // Must run before the autoMode movement gate (explore is on during bench START).
  if (command === 'stop' && source === 'stop_label') {
    if (!benchModeHasDashboardBottleStop()) return;
    notifyTestBenchStopLabelStop();
    useMetricsStore.setState({
      ...commandState('auto_off'),
      ...commandState('stop', 'stop_label'),
    });
    void (async () => {
      try {
        await Promise.all([postBotCommand('auto_off'), postBotCommand('stop')]);
      } catch {
        useMetricsStore.getState().pushEvent('error', 'Failed to send stop-label commands — backend unreachable', 'yahboom/cmd');
      }
    })();
    return;
  }

  if (
    state.autoMode
    && isMovementCommand(command)
    && !(command === 'stop' && (source === 'manual' || source === 'estop'))
  ) {
    return;
  }

  // Gate: stop may only be sent from an explicit release or an emergency stop.
  if (command === 'stop' && source !== 'release' && source !== 'manual' && source !== 'estop' && source !== 'stop_label') return;
  if (command === 'stop' && source === 'release' && state.autoMode) return;

  // E-stop latch: block movement / auto_on while latched. Off/safety commands
  // may still be sent so modes can be disabled without clearing e-stop first.
  if (
    command !== 'stop'
    && command !== 'auto_off'
    && command !== 'estop_on'
    && command !== 'estop_off'
    && state.estopActive
  ) return;

  // Manual stop also turns off explore/auto on the Pi (same as STOP EXPLORING).
  if (command === 'stop' && source === 'manual' && state.autoRunning && !skipAutoOffOnBenchStop()) {
    sendCommand('auto_off');
  }
  if (command === 'stop' && source === 'manual') {
    notifyTestBenchManualStop();
  }

  // POST is fired immediately; joystick mirror updates after (not after await).
  void (async () => {
    try {
      await postBotCommand(command);
      useMetricsStore.setState({
        ...commandState(command, source),
        latencyMs: useMetricsStore.getState().latencyMs,
      });

      if (source === 'estop') {
        const latencyMs = useMetricsStore.getState().latencyMs;
        const latencyStr = latencyMs != null ? `${latencyMs}ms` : '—';
        useMetricsStore.getState().pushEvent('warning', `Emergency stop — robot halted (${latencyStr} publish)`, 'yahboom/cmd');
      }
    } catch {
      useMetricsStore.getState().pushEvent('error', 'Failed to send command — backend unreachable', 'yahboom/cmd');
    }
  })();

  // Mirror UI after fetch is dispatched (async fn runs until first await).
  useMetricsStore.setState(commandState(command, source));
}

/**
 * Maps a joystick pan/tilt vector to a discrete CameraCommand (8 directions).
 * Returns null when the stick is in the dead-zone (no command to send).
 */
export function vecToCameraCommand(pan: number, tilt: number): CameraCommand | null {
  const up = tilt >  0.3;
  const dn = tilt < -0.3;
  const rt = pan  >  0.3;
  const lt = pan  < -0.3;

  if (up && rt) return 'upcright';
  if (up && lt) return 'upcleft';
  if (dn && rt) return 'downcright';
  if (dn && lt) return 'downcleft';
  if (up)       return 'up';
  if (dn)       return 'down';
  if (rt)       return 'cright';
  if (lt)       return 'cleft';
  return null;
}

/** Sends a camera direction command via the backend HTTP route. Camera is not gated by e-stop. */
export async function sendCameraCommand(command: CameraCommand): Promise<void> {

  try {    const res = await fetch('/api/send_command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    });
    const data: { status: string; command: string; topic?: string; latency?: number; message?: string } =
      await res.json();

    if (!res.ok) {
      useMetricsStore.getState().pushEvent('error', `Camera command '${command}' rejected — ${data.message ?? res.statusText}`, data.topic ?? 'yahboom/cmd');
      return;
    }

    useMetricsStore.setState({ latencyMs: data.latency ?? useMetricsStore.getState().latencyMs });
    const latencyStr = data.latency != null ? `${data.latency}ms` : '—';
    useMetricsStore.getState().pushEvent('info', `POST -> ${data.topic ?? 'yahboom/cmd'}: ${data.command ?? command} (${latencyStr} publish)`, data.topic ?? 'yahboom/cmd');
  } catch {
    useMetricsStore.getState().pushEvent('error', 'Failed to send camera command — backend unreachable', 'yahboom/cmd');
  }
}

/**
 * Reverse of vecToCommand — maps a movement command back to its canonical joystick
 * {x, y} position. Used to drive the thumb display from an external command.
 * Returns {0, 0} for stop/null.
 */
const MOVE_VEC: Record<MovementCommand, { x: number; y: number }> = {
  fwd:      { x:  0,     y:  1    },
  bck:      { x:  0,     y: -1    },
  left:     { x: -1,     y:  0    },
  right:    { x:  1,     y:  0    },
  fwdleft:  { x: -0.71,  y:  0.71 },
  fwdright: { x:  0.71,  y:  0.71 },
  bckleft:  { x: -0.71,  y: -0.71 },
  bckright: { x:  0.71,  y: -0.71 },
  stop:     { x:  0,     y:  0    },
};
export function cmdToMovementVec(command: MovementCommand | null): { x: number; y: number } {
  if (!command) return { x: 0, y: 0 };
  return MOVE_VEC[command];
}

/** Maps the joystick linear_x / angular_z vector to a discrete BotCommand (8 directions). */
export function vecToCommand(linear_x: number, angular_z: number): BotCommand {
  const fwd = linear_x >  0.3;
  const bck = linear_x < -0.3;
  const lft = angular_z >  0.3;
  const rgt = angular_z < -0.3;

  if (fwd && lft) return 'fwdleft';
  if (fwd && rgt) return 'fwdright';
  if (bck && lft) return 'bckleft';
  if (bck && rgt) return 'bckright';
  if (fwd)        return 'fwd';
  if (bck)        return 'bck';
  if (lft)        return 'left';
  if (rgt)        return 'right';
  return 'stop';
}

/** Toggle robot ROS auto — sends auto_on or auto_off only. */
export function toggleRosAuto(): void {
  const state = useMetricsStore.getState();
  if (state.autoRunning) {
    sendCommand('auto_off');
    // Explicitly halt motion when exploration is turned off.
    sendCommand('stop', 'manual');
    return;
  }
  if (state.estopActive) return;
  sendCommand('auto_on');
}

