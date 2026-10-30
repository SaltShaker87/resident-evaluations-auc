import React, { useState } from 'react';
import { GraduationCap, RotateCcw, AlertTriangle } from 'lucide-react';
import { restoreSnapshot, dismissSnapshot } from '../../api';
import AdvancementWizard from '../AdvancementWizard';

function formatSnapshotDate(dateStr) {
  if (!dateStr) return '';
  const iso = dateStr.replace(' ', 'T');
  const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00');
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

// The snapshot is loaded by the Settings page, which also marks this category
// in the list while an advancement can still be undone.
export default function AcademicYearSection({ snapshot, reloadSnapshot }) {
  const [showWizard, setShowWizard] = useState(false);
  const [undoText, setUndoText] = useState('');
  const [busy, setBusy] = useState(false);
  const [advanceMsg, setAdvanceMsg] = useState('');

  const handleUndo = async () => {
    if (!snapshot || undoText.trim() !== 'UNDO') return;
    setBusy(true);
    try {
      await restoreSnapshot(snapshot.id);
      setUndoText('');
      setAdvanceMsg('Last advancement was undone.');
    } catch {
      setAdvanceMsg('Failed to undo advancement.');
    }
    reloadSnapshot();
    setBusy(false);
  };

  const handleDismiss = async () => {
    if (!snapshot) return;
    setBusy(true);
    try {
      await dismissSnapshot(snapshot.id);
      setUndoText('');
      setAdvanceMsg('Undo snapshot dismissed.');
    } catch {
      setAdvanceMsg('Failed to dismiss snapshot.');
    }
    reloadSnapshot();
    setBusy(false);
  };

  return (
    <>
      {advanceMsg && (
        <div className="alert alert--warning">
          <AlertTriangle size={16} /> {advanceMsg}
        </div>
      )}

      <div className="card settings-card">
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
              <GraduationCap size={15} /> Advance Residents
            </button>
          </div>
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

      {showWizard && (
        <AdvancementWizard
          onClose={() => setShowWizard(false)}
          onComplete={() => {
            setShowWizard(false);
            reloadSnapshot();
            setAdvanceMsg('Residents advanced. You can undo below.');
          }}
        />
      )}
    </>
  );
}
