import React, { useState } from 'react';
import { Lock, KeyRound, ShieldCheck, AlertCircle } from 'lucide-react';
import { setupPassword, login, recoverPassword } from '../api';

function ErrorBox({ message }) {
  if (!message) return null;
  return (
    <div className="login-error">
      <AlertCircle size={15} /> {message}
    </div>
  );
}

/**
 * Shown exactly once after setup or recovery — the only time the
 * recovery key is ever visible.
 */
function RecoveryKeyNotice({ recoveryKey, onDone }) {
  const [acknowledged, setAcknowledged] = useState(false);

  return (
    <>
      <div className="login-card__icon login-card__icon--green">
        <ShieldCheck size={22} />
      </div>
      <h1>Save Your Recovery Key</h1>
      <p>
        If you ever forget your password, this key is the only way to reset it
        yourself. Write it down and keep it somewhere safe — it will{' '}
        <strong>not be shown again</strong>.
      </p>
      <div className="recovery-key">{recoveryKey}</div>
      <label className="login-checkbox">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        I have written down my recovery key
      </label>
      <button
        className="btn btn--primary btn--lg login-submit"
        disabled={!acknowledged}
        onClick={onDone}
      >
        Continue to AUC
      </button>
    </>
  );
}

export default function Login({ setupRequired, onAuthenticated }) {
  // modes: 'setup' | 'login' | 'recover' | 'show-key'
  const [mode, setMode] = useState(setupRequired ? 'setup' : 'login');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [recoveryKeyInput, setRecoveryKeyInput] = useState('');
  const [newRecoveryKey, setNewRecoveryKey] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const switchMode = (next) => {
    setMode(next);
    setPassword('');
    setConfirm('');
    setRecoveryKeyInput('');
    setError('');
  };

  const handleSetup = async (e) => {
    e.preventDefault();
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await setupPassword(password);
      setNewRecoveryKey(res.recovery_key);
      setMode('show-key');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await login(password);
      onAuthenticated();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleRecover = async (e) => {
    e.preventDefault();
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await recoverPassword(recoveryKeyInput, password);
      setNewRecoveryKey(res.recovery_key);
      setMode('show-key');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <div className="card login-card">
        {mode === 'show-key' && (
          <RecoveryKeyNotice recoveryKey={newRecoveryKey} onDone={onAuthenticated} />
        )}

        {mode === 'setup' && (
          <>
            <div className="login-card__icon">
              <Lock size={22} />
            </div>
            <h1>Welcome to AUC</h1>
            <p>
              Before you start, create a password. Everyone who uses this app
              will need it to see or edit resident information.
            </p>
            <form onSubmit={handleSetup}>
              <div className="form-group">
                <label>Password (at least 8 characters)</label>
                <input
                  type="password"
                  className="form-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Confirm password</label>
                <input
                  type="password"
                  className="form-input"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <ErrorBox message={error} />
              <button
                type="submit"
                className="btn btn--primary btn--lg login-submit"
                disabled={busy || password.length < 8 || !confirm}
              >
                {busy ? 'Setting up…' : 'Create Password'}
              </button>
            </form>
          </>
        )}

        {mode === 'login' && (
          <>
            <div className="login-card__icon">
              <Lock size={22} />
            </div>
            <h1>AUC</h1>
            <p>Enter the password to continue.</p>
            <form onSubmit={handleLogin}>
              <div className="form-group">
                <label>Password</label>
                <input
                  type="password"
                  className="form-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoFocus
                />
              </div>
              <ErrorBox message={error} />
              <button
                type="submit"
                className="btn btn--primary btn--lg login-submit"
                disabled={busy || !password}
              >
                {busy ? 'Signing in…' : 'Sign In'}
              </button>
            </form>
            <button className="login-link" onClick={() => switchMode('recover')}>
              Forgot password?
            </button>
          </>
        )}

        {mode === 'recover' && (
          <>
            <div className="login-card__icon">
              <KeyRound size={22} />
            </div>
            <h1>Reset Password</h1>
            <p>
              Enter the recovery key you wrote down when you first set up AUC,
              then choose a new password.
            </p>
            <form onSubmit={handleRecover}>
              <div className="form-group">
                <label>Recovery key</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="XXXX-XXXX-XXXX-XXXX"
                  value={recoveryKeyInput}
                  onChange={(e) => setRecoveryKeyInput(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>New password (at least 8 characters)</label>
                <input
                  type="password"
                  className="form-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label>Confirm new password</label>
                <input
                  type="password"
                  className="form-input"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <ErrorBox message={error} />
              <button
                type="submit"
                className="btn btn--primary btn--lg login-submit"
                disabled={busy || !recoveryKeyInput || password.length < 8 || !confirm}
              >
                {busy ? 'Resetting…' : 'Reset Password'}
              </button>
            </form>
            <button className="login-link" onClick={() => switchMode('login')}>
              Back to sign in
            </button>
            <p className="login-hint">
              Lost the recovery key too? Whoever manages the computer running
              AUC can reset the password — see SECURITY.md in the app folder.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
