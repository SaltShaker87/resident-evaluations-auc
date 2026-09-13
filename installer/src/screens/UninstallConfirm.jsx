import { useState } from 'react';

export default function UninstallConfirm({ onConfirm, onCancel }) {
  const [deleteData, setDeleteData] = useState(false);
  const [deleteNemotron, setDeleteNemotron] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const needsType = deleteData || deleteNemotron;
  const canConfirm = !needsType || confirmText === 'DELETE';

  return (
    <div className="screen-content">
      <h1>Uninstall AUC</h1>
      <p className="lead">
        This removes the AUC application. Your data is kept unless you choose to delete it below.
      </p>

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={deleteData}
          onChange={(e) => setDeleteData(e.target.checked)}
        />
        <span>Also delete my data: residents, notes, summaries and photos</span>
      </label>

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={deleteNemotron}
          onChange={(e) => setDeleteNemotron(e.target.checked)}
        />
        <span>Also delete the downloaded Nemotron models</span>
      </label>

      {needsType && (
        <div className="field">
          <label className="field-label" htmlFor="delete-confirm">
            Type DELETE to confirm
          </label>
          <input
            id="delete-confirm"
            className="input"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            autoComplete="off"
          />
        </div>
      )}

      <div className="screen-actions screen-actions--split">
        <button type="button" className="btn btn--secondary" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn--danger"
          disabled={!canConfirm}
          onClick={() => onConfirm({ delete_data: deleteData, delete_nemotron_cache: deleteNemotron })}
        >
          Uninstall
        </button>
      </div>
    </div>
  );
}
