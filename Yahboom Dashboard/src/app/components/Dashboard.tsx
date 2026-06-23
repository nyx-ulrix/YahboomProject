// Dashboard view: backend sync hooks, keyboard shortcuts, and widget grid.
import { useEffect } from 'react';
import { TopBar } from './TopBar';
import { DashboardGrid } from './DashboardGrid';
import { WidgetPickerOverlay } from './WidgetPickerOverlay';
import {
  useClientAutoPilot, useConnectionSync, useDriveStatusPoll, useGlobalShortcuts,
  useGridStatusPoll, useKeyboardMovement, useSafetyStatusPoll,
} from '../hooks';
import { useMetricsStore } from '../store';

export function Dashboard({ darkMode, toggleDark }: { darkMode: boolean; toggleDark: () => void }) {
  useConnectionSync();
  useSafetyStatusPoll();
  useGridStatusPoll();
  useDriveStatusPoll();
  useClientAutoPilot();
  useKeyboardMovement();
  useGlobalShortcuts();

  // Hint user about the picker shortcut on first mount
  useEffect(() => {
    const seen = sessionStorage.getItem('picker_hint');
    if (!seen) {
      useMetricsStore.getState().pushEvent('info', 'Tip: press P to open the widget picker, X to emergency-stop.');
      sessionStorage.setItem('picker_hint', '1');
    }
  }, []);

  return (
    <div className="min-h-screen w-full p-4 sm:p-6"
      style={{ background: 'var(--bg-app)' }}>
      <div className="app-shell ambient-glow w-full mx-auto p-4 sm:p-6 flex flex-col gap-5"
        style={{ maxWidth: 1600 }}>
        <TopBar darkMode={darkMode} toggleDark={toggleDark} />

        <div>
          <h2>Mission Console</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 2 }}>
            Real-time telemetry, video, and command surface for the edge robot fleet
          </p>
        </div>

        <DashboardGrid />
      </div>
      <WidgetPickerOverlay />
    </div>
  );
}
