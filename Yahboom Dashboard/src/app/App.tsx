// App root — view routing and dark/light theme on <html>.

import { useEffect, useState } from 'react';
import { useViewStore } from './store';
import type { ViewStore } from './store';
import { Dashboard } from './components/Dashboard';
import { AiAgentPage } from './components/AiAgentPage';
import { ControllerPage } from './components/ControllerPage';
import { SettingsPanel } from './components/SettingsPanel';

export default function App() {
  const [darkMode, setDarkMode] = useState(true);

  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) root.classList.add('dark');
    else root.classList.remove('dark');
  }, [darkMode]);

  const toggleDark = () => setDarkMode((v) => !v);

  const view = useViewStore((s: ViewStore) => s.view);
  return (
    <>
      {view === 'ai_agent'
        ? <AiAgentPage darkMode={darkMode} toggleDark={toggleDark} />
        : view === 'controller'
          ? <ControllerPage darkMode={darkMode} toggleDark={toggleDark} />
          : <Dashboard darkMode={darkMode} toggleDark={toggleDark} />}
      <SettingsPanel />
    </>
  );
}