import React from 'react';
import { Sun, Moon } from 'lucide-react';

export default function AppearanceSection({ theme, setTheme }) {
  const isDark = theme === 'dark';

  return (
    <div className="card settings-card">
      <div className="settings-row">
        <div>
          <div className="settings-row__label">Theme</div>
          <div className="settings-row__desc">
            {isDark ? 'Dark' : 'Light'} mode is active
          </div>
        </div>
        <div className="settings-row__control">
          <Sun
            size={15}
            className={`theme-icon${!isDark ? ' theme-icon--active' : ''}`}
          />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={isDark}
              onChange={() => setTheme(isDark ? 'light' : 'dark')}
            />
            <span className="toggle-switch__slider" />
          </label>
          <Moon
            size={15}
            className={`theme-icon${isDark ? ' theme-icon--active' : ''}`}
          />
        </div>
      </div>
    </div>
  );
}
