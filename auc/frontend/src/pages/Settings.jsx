import React, { useState, useEffect } from 'react';
import { Sun, Moon, GraduationCap, RotateCcw, AlertTriangle, Download, Check } from 'lucide-react';
import { getOllamaModels, getActiveSnapshot, restoreSnapshot, dismissSnapshot, downloadBackup } from '../api';
import AdvancementWizard from '../components/AdvancementWizard';

function formatSnapshotDate(dateStr) {
  if (!dateStr) return '';
  const iso = dateStr.replace(' ', 'T');
  const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00');
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export default function Settings({ theme, setTheme }) {
  const isDark = theme === 'dark';

  const [models, setModels] = useState([]);
  const [ollamaError, setOllamaError] = useState('');
  const [defaultModel, setDefaultModelState] = useState(
    () => localStorage.getItem('defaultOllamaModel') || ''
  );

  const [showWizard, setShowWizard] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [undoText, setUndoText] = useState('');
  const [busy, setBusy] = useState(false);
  const [advanceMsg, setAdvanceMsg] = useState('');

  const [backupBusy, setBackupBusy] = useState(false);
  const [backupDone, setBackupDone] = useState(false);
  const [backupError, setBackupError] = useState('');

  const handleBackup = async () => {
    setBackupBusy(true);
    setBackupError('');
    setBackupDone(false);
    try {
      await downloadBackup();
      setBackupDone(true);
      setTimeout(() => setBackupDone(false), 4000);
    } catch (err) {
      setBackupError(err.message || 'Download failed.');
    }
    setBackupBusy(false);
  };

  const loadSnapshot = () => {
    getActiveSnapshot().then(setSnapshot).catch(() => setSnapshot(null));
  };

  useEffect(() => {
    loadSnapshot();
  }, []);

  const handleUndo = async () => {
    if (!snapshot || undoText.trim() !== 'UNDO') return;
    setBusy(true);
    try {
      await restoreSnapshot(snapshot.id);
      setSnapshot(null);
      setUndoText('');
      setAdvanceMsg('Last advancement was undone.');
    } catch {
      setAdvanceMsg('Failed to undo advancement.');
    }
    setBusy(false);
  };

  const handleDismiss = async () => {
    if (!snapshot) return;
    setBusy(true);
    try {
      await dismissSnapshot(snapshot.id);
      setSnapshot(null);
      setUndoText('');
      setAdvanceMsg('Undo snapshot dismissed.');
    } catch {
      setAdvanceMsg('Failed to dismiss snapshot.');
    }
    setBusy(false);
  };

  useEffect(() => {
    getOllamaModels().then((result) => {
      if (Array.isArray(result) && result.length > 0) {
        setModels(result);
        setOllamaError('');
      } else if (result?.error) {
        setOllamaError('Ollama not reachable — make sure it\'s running.');
      } else {
        setOllamaError('Ollama is running but no models are installed.');
      }
    }).catch(() => {
      setOllamaError('Ollama not reachable — make sure it\'s running.');
    });
  }, []);

  const handleModelChange = (e) => {
    const val = e.target.value;
    setDefaultModelState(val);
    if (val) {
      localStorage.setItem('defaultOllamaModel', val);
    } else {
      localStorage.removeItem('defaultOllamaModel');
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
        <p>Manage your preferences</p>
      </div>

      <div className="card settings-card">
        <div className="settings-section-title">Appearance</div>

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

      <div className="card settings-card" style={{ marginTop: '1rem' }}>
        <div className="settings-section-title">AI Model</div>

        <div className="settings-row">
          <div>
            <div className="settings-row__label">Default Ollama model</div>
            <div className="settings-row__desc">
              Used when generating AI summaries on resident pages
            </div>
          </div>
          <div className="settings-row__control">
            {ollamaError ? (
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                {ollamaError}
              </span>
            ) : (
              <select
                className="form-select"
                style={{ width: 'auto', minWidth: '200px' }}
                value={defaultModel}
                onChange={handleModelChange}
                disabled={models.length === 0}
              >
                <option value="">Select a default model</option>
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      </div>

      <div className="card settings-card" style={{ marginTop: '1rem' }}>
        <div className="settings-section-title">Resident Advancement</div>

        {advanceMsg && (
          <div className="alert alert--warning mt-md mb-md">
            <AlertTriangle size={16} /> {advanceMsg}
          </div>
        )}

        <div className="settings-row">
          <div>
            <div className="settings-row__label">Advance residents to next year</div>
            <div className="settings-row__desc">
              Graduate PGY-3s, promote everyone else, and depart prelims. A snapshot is saved so you
              can undo.
            </div>
          </div>
          <div className="settings-row__control">
            <button className="btn btn--primary" onClick={() => { setAdvanceMsg(''); setShowWizard(true); }}>
              <GraduationCap size={15} /> Advance Residents to Next Year
            </button>
          </div>
        </div>

        {snapshot && (
          <div className="undo-panel">
            <div className="undo-panel__head">
              <RotateCcw size={16} />
              <div>
                <div className="undo-panel__title">Undo Last Advancement</div>
                <div className="settings-row__desc">
                  Performed {formatSnapshotDate(snapshot.created_at)}
                </div>
              </div>
            </div>
            {snapshot.summary && (
              <div className="undo-panel__summary">{snapshot.summary}</div>
            )}
            <div className="form-group" style={{ marginTop: '0.75rem', marginBottom: '0.5rem' }}>
              <label>Type UNDO to restore the previous state</label>
              <input
                className="form-input"
                value={undoText}
                onChange={(e) => setUndoText(e.target.value)}
                placeholder="UNDO"
              />
            </div>
            <div className="flex gap-sm">
              <button
                className="btn btn--danger"
                onClick={handleUndo}
                disabled={undoText.trim() !== 'UNDO' || busy}
              >
                {busy ? 'Working…' : 'Undo Advancement'}
              </button>
              <button className="btn btn--secondary" onClick={handleDismiss} disabled={busy}>
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="card settings-card" style={{ marginTop: '1rem' }}>
        <div className="settings-section-title">Data Management</div>

        <div className="settings-row">
          <div>
            <div className="settings-row__label">Download full backup (.zip)</div>
            <div className="settings-row__desc">
              Downloads a complete copy of your database and all resident photos as a
              single .zip. Keep it somewhere safe. (An automated daily backup can also
              run in the background — see BACKUPS.md.)
            </div>
          </div>
          <div className="settings-row__control" style={{ flexDirection: 'column', alignItems: 'flex-end', gap: '0.5rem' }}>
            <button className="btn btn--primary" onClick={handleBackup} disabled={backupBusy}>
              <Download size={15} /> {backupBusy ? 'Preparing…' : 'Download Full Backup (.zip)'}
            </button>
            {backupDone && (
              <span className="text-sm" style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--green-600)' }}>
                <Check size={15} /> Download started
              </span>
            )}
            {backupError && (
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>{backupError}</span>
            )}
          </div>
        </div>
      </div>

      {showWizard && (
        <AdvancementWizard
          onClose={() => setShowWizard(false)}
          onComplete={() => { setShowWizard(false); loadSnapshot(); setAdvanceMsg('Residents advanced. You can undo below.'); }}
        />
      )}
    </div>
  );
}
