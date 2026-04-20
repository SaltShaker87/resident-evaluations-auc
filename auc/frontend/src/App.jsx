import React, { useState, useCallback } from 'react';
import { Routes, Route, NavLink, Link } from 'react-router-dom';
import { Users, ClipboardList } from 'lucide-react';
import ResidentList from './pages/ResidentList';
import ResidentDetail from './pages/ResidentDetail';
import FollowupDashboard from './pages/FollowupDashboard';

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

  const showToast = useCallback((message) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

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
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ResidentList showToast={showToast} />} />
          <Route path="/residents/:id" element={<ResidentDetail showToast={showToast} />} />
          <Route path="/followups" element={<FollowupDashboard showToast={showToast} />} />
        </Routes>
      </main>
      <ToastContainer toasts={toasts} />
    </div>
  );
}
