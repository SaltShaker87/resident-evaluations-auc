import React, { useState, useCallback, useEffect } from 'react';
import { Routes, Route, NavLink, Link } from 'react-router-dom';
import { Users, ClipboardList, Upload, Settings as SettingsIcon, LogOut } from 'lucide-react';
import ResidentList from './pages/ResidentList';
import ResidentDetail from './pages/ResidentDetail';
import FollowupDashboard from './pages/FollowupDashboard';
import MedhubImport from './pages/MedhubImport';
import SettingsPage from './pages/Settings';
import Login from './pages/Login';
import { getAuthStatus, logout } from './api';

function ToastContainer({ toasts }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className="toast">{t.message}</div>
      ))}
    </div>
  );
}

export default function App() {
  const [toasts, setToasts] = useState([]);
  const [auth, setAuth] = useState({ loading: true, setupRequired: false, authenticated: false });
  const [theme, setThemeState] = useState(
    () => localStorage.getItem('theme') || 'dark'
  );

  const setTheme = useCallback((newTheme) => {
    setThemeState(newTheme);
    localStorage.setItem('theme', newTheme);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    getAuthStatus()
      .then((s) => setAuth({
        loading: false,
        setupRequired: s.setup_required,
        authenticated: s.authenticated,
      }))
      .catch(() => setAuth({ loading: false, setupRequired: false, authenticated: false }));

    // Fired by api.js whenever any request comes back 401 (session expired)
    const onUnauthenticated = () =>
      setAuth((prev) => ({ ...prev, loading: false, authenticated: false }));
    window.addEventListener('auc:unauthenticated', onUnauthenticated);
    return () => window.removeEventListener('auc:unauthenticated', onUnauthenticated);
  }, []);

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // Even if the request fails, drop back to the login screen
    }
    setAuth({ loading: false, setupRequired: false, authenticated: false });
  };

  const showToast = useCallback((message) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  if (auth.loading) return null;

  if (!auth.authenticated) {
    return (
      <Login
        setupRequired={auth.setupRequired}
        onAuthenticated={() =>
          setAuth({ loading: false, setupRequired: false, authenticated: true })
        }
      />
    );
  }

  return (
    <div className="app-layout">
      <header className="app-header">
        <Link to="/" className="app-header__brand">
          <div className="app-header__logo">AUC</div>
          <div>
            <div className="app-header__title">AUC</div>
            <div className="app-header__subtitle">Assessments Under Curve</div>
          </div>
        </Link>
        <nav className="app-header__nav">
          <NavLink to="/" end className={({ isActive }) => isActive ? 'active' : ''}>
            <Users size={16} /> Residents
          </NavLink>
          <NavLink to="/followups" className={({ isActive }) => isActive ? 'active' : ''}>
            <ClipboardList size={16} /> Follow-Ups
          </NavLink>
          <NavLink to="/medhub" className={({ isActive }) => isActive ? 'active' : ''}>
            <Upload size={16} /> MedHub Import
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => isActive ? 'active' : ''}>
            <SettingsIcon size={16} /> Settings
          </NavLink>
          <button className="app-header__logout" onClick={handleLogout} title="Log out">
            <LogOut size={16} /> Log Out
          </button>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ResidentList showToast={showToast} />} />
          <Route path="/residents/:id" element={<ResidentDetail showToast={showToast} />} />
          <Route path="/followups" element={<FollowupDashboard showToast={showToast} />} />
          <Route path="/medhub" element={<MedhubImport showToast={showToast} />} />
          <Route path="/settings" element={<SettingsPage theme={theme} setTheme={setTheme} />} />
        </Routes>
      </main>
      <ToastContainer toasts={toasts} />
    </div>
  );
}
