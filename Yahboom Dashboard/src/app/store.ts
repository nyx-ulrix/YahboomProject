// Zustand stores: layout, metrics, and UI state.
// Telemetry is updated by hooks polling /api/* endpoints.

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { LayoutItem } from 'react-grid-layout';
import type { MetricsState, WidgetDefinition } from './types';

// Layout store — persisted across refresh.

export type LayoutBreakpoint = 'laptop' | 'ipad';

/** Viewport width at or above this uses the laptop layout preset. */
export const LAYOUT_BREAKPOINT = 1024;

export function layoutBreakpointFromWidth(width: number): LayoutBreakpoint {
  return width >= LAYOUT_BREAKPOINT ? 'laptop' : 'ipad';
}

function layoutWidgetIds(layout: LayoutItem[]): string[] {
  return layout.map((l) => l.i);
}

export interface LayoutStore {
  laptopLayout: LayoutItem[];
  ipadLayout: LayoutItem[];
  activeBreakpoint: LayoutBreakpoint;
  activeWidgetIds: string[];
  /** IDs of widgets that are locked (static) in the grid. */
  lockedIds: string[];
  /** ID of the layout template that was last applied. */
  activeTemplateId: string;
  setActiveBreakpoint: (breakpoint: LayoutBreakpoint) => void;
  setLayout: (layout: LayoutItem[], breakpoint?: LayoutBreakpoint) => void;
  addWidget: (def: WidgetDefinition, position?: { x: number; y: number }) => void;
  removeWidget: (id: string) => void;
  toggleLock: (id: string) => void;
  /** Replace both breakpoint layouts with a named template preset. */
  applyTemplate: (templateId: string) => void;
  reset: (breakpoint?: LayoutBreakpoint) => void;
}

// Layout templates — switchable presets from the TopBar (VIT View is default).

/** VIT View (laptop) — decoder left, video center, controls right, event log bottom. */
const VIT_VIEW_LAPTOP: LayoutItem[] = [
  { i: 'vit_decoder_widget',          x: 0,  y: 0, w: 5, h: 4 },
  { i: 'movement_joystick_widget',    x: 10,  y: 2, w: 1, h: 1 },
  { i: 'video_feed_widget',           x: 5,  y: 0, w: 5, h: 4 },
  { i: 'camera_joystick_widget',      x: 11,  y: 2, w: 1, h: 1 },
  { i: 'ros_auto_button_widget',      x: 10,  y: 1, w: 2, h: 1 },
  { i: 'stop_button_widget',          x: 10,  y: 0, w: 2, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 5, w: 12, h: 4 }
];

/** VIT View (iPad / narrow) — same structure, stacked for a narrower viewport. */
const VIT_VIEW_IPAD: LayoutItem[] = [
  { i: 'vit_decoder_widget',          x: 0,  y: 0, w: 5, h: 4 },
  { i: 'movement_joystick_widget',    x: 10,  y: 2, w: 1, h: 1 },
  { i: 'video_feed_widget',           x: 5,  y: 0, w: 5, h: 4 },
  { i: 'camera_joystick_widget',      x: 11,  y: 2, w: 1, h: 1 },
  { i: 'ros_auto_button_widget',      x: 10,  y: 1, w: 2, h: 1 },
  { i: 'stop_button_widget',          x: 10,  y: 0, w: 2, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 5, w: 12, h: 4 }
];

/** LiDAR View (laptop) — the previous video-centric default. */
const LIDAR_VIEW_LAPTOP: LayoutItem[] = [
  { i: 'slam_map_widget',             x: 0,  y: 0, w: 2, h: 3 },
  { i: 'video_feed_widget',           x: 2,  y: 0, w: 4, h: 3 },
  { i: 'system_status_widget',        x: 9,  y: 3, w: 4, h: 3 },
  { i: 'movement_joystick_widget',    x: 8,  y: 0, w: 1, h: 1 },
  { i: 'lidar_scan_widget',           x: 10, y: 0, w: 3, h: 3 },
  { i: 'camera_joystick_widget',      x: 8,  y: 1, w: 1, h: 1 },
  { i: 'stop_button_widget',          x: 6,  y: 2, w: 2, h: 1 },
  { i: 'ros_auto_button_widget',      x: 6, y: 2, w: 2, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 3, w: 8, h: 3 },
];

/** LiDAR View (iPad / narrow) — the previous video-centric narrow default. */
const LIDAR_VIEW_IPAD: LayoutItem[] = [
  { i: 'slam_map_widget',             x: 0,  y: 0, w: 2, h: 3 },
  { i: 'video_feed_widget',           x: 2,  y: 0, w: 4, h: 3 },
  { i: 'system_status_widget',        x: 9,  y: 3, w: 4, h: 3 },
  { i: 'movement_joystick_widget',    x: 8,  y: 0, w: 1, h: 1 },
  { i: 'lidar_scan_widget',           x: 10, y: 0, w: 3, h: 3 },
  { i: 'camera_joystick_widget',      x: 8,  y: 1, w: 1, h: 1 },
  { i: 'stop_button_widget',          x: 6,  y: 2, w: 2, h: 1 },
  { i: 'ros_auto_button_widget',      x: 6, y: 2, w: 2, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 3, w: 8, h: 3 },
];

const STOP_DISTANCCE_TEST_LAPTOP: LayoutItem[] = [
  { i: 'vit_decoder_widget',          x: 0,  y: 0, w: 4, h: 5 },
  { i: 'video_feed_widget',           x: 4,  y: 0, w: 5, h: 5 },
  { i: 'stop_test_bench_widget',      x: 10,  y: 0, w: 3, h: 4 },
  { i: 'stop_button_widget',          x: 9,  y: 4, w: 3, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 5, w: 12, h: 4 }
];

/** VIT View (iPad / narrow) — same structure, stacked for a narrower viewport. */
const STOP_DISTANCCE_TEST_IPAD: LayoutItem[] = [
  { i: 'vit_decoder_widget',          x: 0,  y: 0, w: 4, h: 5 },
  { i: 'video_feed_widget',           x: 4,  y: 0, w: 5, h: 5 },
  { i: 'stop_test_bench_widget',      x: 10,  y: 0, w: 3, h: 4 },
  { i: 'stop_button_widget',          x: 9,  y: 4, w: 3, h: 1 },
  { i: 'event_log_widget',            x: 0,  y: 5, w: 12, h: 4 }
];

/** A named layout preset with both breakpoint variants. */
export interface LayoutTemplate {
  id: string;
  name: string;
  laptop: LayoutItem[];
  ipad: LayoutItem[];
}

/** Registry of switchable layout templates (shown in the TopBar dropdown). */
export const LAYOUT_TEMPLATES: LayoutTemplate[] = [
  { id: 'vit_view',   name: 'VIT View',   laptop: VIT_VIEW_LAPTOP,   ipad: VIT_VIEW_IPAD },
  { id: 'lidar_view', name: 'LiDAR View', laptop: LIDAR_VIEW_LAPTOP, ipad: LIDAR_VIEW_IPAD },
  { id: 'stop_test', name: 'Stop Test', laptop: STOP_DISTANCCE_TEST_LAPTOP, ipad: STOP_DISTANCCE_TEST_IPAD },
];

/** Template applied to fresh dashboards — always one of the stored views. */
export const DEFAULT_TEMPLATE_ID = 'stop_test';

/** The default template object, resolved from the registry (falls back to the first). */
export const DEFAULT_TEMPLATE: LayoutTemplate =
  LAYOUT_TEMPLATES.find((t) => t.id === DEFAULT_TEMPLATE_ID) ?? LAYOUT_TEMPLATES[0];

// Default layouts are always derived from the default stored view, so the two
// can never drift apart — change DEFAULT_TEMPLATE_ID to switch the default.
export const IPAD_DEFAULT_LAYOUT: LayoutItem[] = DEFAULT_TEMPLATE.ipad;
export const LAPTOP_DEFAULT_LAYOUT: LayoutItem[] = DEFAULT_TEMPLATE.laptop;

const LAYOUT_PERSIST_VERSION = 27;

type PersistedLayoutState = Partial<LayoutStore> & { layout?: LayoutItem[] };

function initialBreakpoint(): LayoutBreakpoint {
  if (typeof window === 'undefined') return 'laptop';
  return layoutBreakpointFromWidth(window.innerWidth);
}

function initialActiveWidgetIds(breakpoint: LayoutBreakpoint): string[] {
  return layoutWidgetIds(
    breakpoint === 'laptop' ? LAPTOP_DEFAULT_LAYOUT : IPAD_DEFAULT_LAYOUT
  );
}

export const useLayoutStore = create<LayoutStore>()(
  persist(
    (set, get) => ({
      laptopLayout: LAPTOP_DEFAULT_LAYOUT,
      ipadLayout: IPAD_DEFAULT_LAYOUT,
      activeBreakpoint: initialBreakpoint(),
      activeWidgetIds: initialActiveWidgetIds(initialBreakpoint()),
      lockedIds: [],
      activeTemplateId: DEFAULT_TEMPLATE_ID,

      setActiveBreakpoint: (breakpoint) => {
        const { laptopLayout, ipadLayout } = get();
        set({
          activeBreakpoint: breakpoint,
          activeWidgetIds: layoutWidgetIds(
            breakpoint === 'laptop' ? laptopLayout : ipadLayout
          ),
        });
      },

      setLayout: (layout, breakpoint) => {
        const bp = breakpoint ?? get().activeBreakpoint;
        const patch =
          bp === 'laptop'
            ? { laptopLayout: layout, activeWidgetIds: layoutWidgetIds(layout) }
            : { ipadLayout: layout, activeWidgetIds: layoutWidgetIds(layout) };
        set(patch);
      },

      addWidget: (def, position) => {
        const { activeBreakpoint, laptopLayout, ipadLayout, activeWidgetIds } = get();
        if (activeWidgetIds.includes(def.id)) return;
        const layout = activeBreakpoint === 'laptop' ? laptopLayout : ipadLayout;
        const newItem: LayoutItem = {
          i: def.id,
          x: position?.x ?? 0,
          y: position?.y ?? Infinity, // react-grid-layout places at bottom when Infinity
          w: def.defaultSize.w,
          h: def.defaultSize.h,
          minW: def.defaultSize.minW,
          minH: def.defaultSize.minH,
          maxW: def.defaultSize.maxW,
          maxH: def.defaultSize.maxH,
        };
        const nextLayout = [...layout, newItem];
        set(
          activeBreakpoint === 'laptop'
            ? {
                laptopLayout: nextLayout,
                activeWidgetIds: [...activeWidgetIds, def.id],
              }
            : {
                ipadLayout: nextLayout,
                activeWidgetIds: [...activeWidgetIds, def.id],
              }
        );
      },

      removeWidget: (id) => {
        const { activeBreakpoint, laptopLayout, ipadLayout, activeWidgetIds } = get();
        const layout = activeBreakpoint === 'laptop' ? laptopLayout : ipadLayout;
        const nextLayout = layout.filter((l) => l.i !== id);
        set(
          activeBreakpoint === 'laptop'
            ? {
                laptopLayout: nextLayout,
                activeWidgetIds: activeWidgetIds.filter((x) => x !== id),
              }
            : {
                ipadLayout: nextLayout,
                activeWidgetIds: activeWidgetIds.filter((x) => x !== id),
              }
        );
      },

      toggleLock: (id) => {
        const { lockedIds } = get();
        set({
          lockedIds: lockedIds.includes(id)
            ? lockedIds.filter((x) => x !== id)
            : [...lockedIds, id],
        });
      },

      applyTemplate: (templateId) => {
        const template = LAYOUT_TEMPLATES.find((t) => t.id === templateId);
        if (!template) return;
        const { activeBreakpoint } = get();
        set({
          laptopLayout: template.laptop,
          ipadLayout: template.ipad,
          activeWidgetIds: layoutWidgetIds(
            activeBreakpoint === 'laptop' ? template.laptop : template.ipad
          ),
          lockedIds: [],
          activeTemplateId: templateId,
        });
      },

      reset: (breakpoint) => {
        const bp = breakpoint ?? get().activeBreakpoint;
        const defaultLayout =
          bp === 'laptop' ? LAPTOP_DEFAULT_LAYOUT : IPAD_DEFAULT_LAYOUT;
        set(
          bp === 'laptop'
            ? {
                laptopLayout: defaultLayout,
                activeWidgetIds: layoutWidgetIds(defaultLayout),
                lockedIds: [],
              }
            : {
                ipadLayout: defaultLayout,
                activeWidgetIds: layoutWidgetIds(defaultLayout),
                lockedIds: [],
              }
        );
      },
    }),
    {
      name: 'dashboard_layout_v26',
      version: LAYOUT_PERSIST_VERSION,
      migrate: (persistedState, version) => {
        if (version >= LAYOUT_PERSIST_VERSION) {
          return persistedState as LayoutStore;
        }

        const old = persistedState as PersistedLayoutState;
        if (old.layout && !old.laptopLayout) {
          return {
            laptopLayout: LAPTOP_DEFAULT_LAYOUT,
            ipadLayout: old.layout,
            lockedIds: old.lockedIds ?? [],
            activeBreakpoint: initialBreakpoint(),
            activeWidgetIds: initialActiveWidgetIds(initialBreakpoint()),
          } satisfies Partial<LayoutStore>;
        }

        return persistedState as LayoutStore;
      },
    }
  )
);

// Metrics store — live robot telemetry synced from the backend.

export interface MetricsStore extends MetricsState {
  setMetric: <K extends keyof MetricsState>(key: K, value: MetricsState[K]) => void;
  pushEvent: (level: 'info' | 'warning' | 'error', message: string) => void;
}

export const useMetricsStore = create<MetricsStore>((set) => ({
  // --- Initial values — real values arrive via useConnectionSync / MQTT ---
  connectionStatus: 'DISCONNECTED',
  networkMode: null,
  latencyMs: null,
  lastHeartbeat: null,
  missionStatus: 'IDLE',
  currentCommand: 'IDLE',
  commandAck: 'WAITING',
  commandSuccessRate: null,
  videoStreamStatus: 'OFFLINE',
  videoFps: null,
  videoDelayMs: null,
  aiModelStatus: 'OFFLINE',
  detections: [],
  batteryPercent: null,
  cpuPercent: null,
  memoryPercent: null,
  ros2BridgeStatus: null,
  mqttLinkStatus: 'DISCONNECTED',
  cameraSensorStatus: null,
  events: [],
  latencyHistory: [],
  cpuHistory: [],
  estopActive: false,
  estopIgnoreLatchUntil: 0,
  mode: 'manual',
  autoMode: false,
  autoRunning: false,
  latestGrid: null,
  frontDistance: null,
  leftDistance: null,
  rightDistance: null,
  safetyStatus: 'unknown',
  safetyGraceStatus: null,
  driveStatus: 'unknown',
  movementVec: { x: 0, y: 0 },
  cameraKeyboardVec: { x: 0, y: 0 },
  streamRunning: false,

  // --- Updaters ---
  setMetric: (key, value) => set({ [key]: value } as Partial<MetricsState>),

  /** Appends an event. */
  pushEvent: (level, message) =>
    set((s) => ({
      events: [
        ...s.events,
        { id: Date.now() + Math.random(), timestamp: new Date().toISOString(), level, message },
      ],
    })),
}));

// Widget picker overlay state.

export interface PickerStore {
  isOpen: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

export const usePickerStore = create<PickerStore>((set, get) => ({
  isOpen: false,
  setOpen: (isOpen) => set({ isOpen }),
  toggle: () => set({ isOpen: !get().isOpen }),
}));

// Auth store — placeholder until LoginPage is wired into routing.

export interface AuthStore {
  user: { email: string; name: string } | null;
  signIn: (email: string, name?: string) => void;
  signOut: () => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  signIn: (email, name) => set({ user: { email, name: name ?? email.split('@')[0] } }),
  signOut: () => set({ user: null }),
}));

// View routing — dashboard, AI agent, and controller pages.

type View = 'dashboard' | 'ai_agent' | 'controller';

export interface ViewStore {
  view: View;
  setView: (v: View) => void;
}

export const useViewStore = create<ViewStore>((set) => ({
  view: 'dashboard',
  setView: (view) => set({ view }),
}));

// Settings store — broker IP and connection state (via /api/connect).

export interface SettingsStore {
  isOpen: boolean;
  brokerIp: string;
  isConnected: boolean;
  /** Full MJPEG URL from the Pi — set when /api/status detects a running video_server. */
  videoStreamUrl: string;
  setOpen: (v: boolean) => void;
  setBrokerIp: (ip: string) => void;
  setConnected: (v: boolean) => void;
  setVideoStreamUrl: (url: string) => void;
}

export const DEFAULT_BROKER_HOST = 'raspberrypi.local';

export const useSettingsStore = create<SettingsStore>()(
  persist(
    (set) => ({
      isOpen: false,
      brokerIp: DEFAULT_BROKER_HOST,
      isConnected: false,
      videoStreamUrl: '',
      setOpen: (isOpen) => set({ isOpen }),
      setBrokerIp: (brokerIp) => set({ brokerIp }),
      setConnected: (isConnected) => set({ isConnected }),
      setVideoStreamUrl: (videoStreamUrl) => set({ videoStreamUrl }),
    }),
    {
      name: 'dashboard_settings_v2',
      // Only persist the broker IP — all other fields are transient session state.
      partialize: (s) => ({ brokerIp: s.brokerIp }),
    }
  )
);
