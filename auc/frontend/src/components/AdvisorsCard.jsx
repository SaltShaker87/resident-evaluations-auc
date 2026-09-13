import React, { useState, useEffect } from 'react';
import { Plus, Pencil, Trash2 } from 'lucide-react';
import { getAdvisors, createAdvisor, updateAdvisor, deleteAdvisor } from '../api';

// The advisors list behind the "Advisor" dropdown on each resident page.
// Deactivating keeps existing assignments (shown as inactive); deleting sends
// those residents back to Unassigned.
export default function AdvisorsCard() {
  const [advisors, setAdvisors] = useState([]);
  const [newName, setNewName] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const [error, setError] = useState('');

  const load = () => getAdvisors().then(setAdvisors).catch(() => setError('Could not load advisors.'));

  useEffect(() => {
    load();
  }, []);

  const run = async (action) => {
    setError('');
    try {
      await action();
      await load();
      return true;
    } catch (err) {
      setError(err.message || 'Something went wrong.');
      return false;
    }
  };

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;
    if (await run(() => createAdvisor(newName.trim()))) setNewName('');
  };

  const handleRename = async (e, id) => {
    e.preventDefault();
    if (!editName.trim()) return;
    if (await run(() => updateAdvisor(id, { name: editName.trim() }))) setEditingId(null);
  };

  const handleDelete = (a) => {
    if (window.confirm(`Delete ${a.name}? Residents assigned to them will become Unassigned.`)) {
      run(() => deleteAdvisor(a.id));
    }
  };

  return (
    <div className="card settings-card" style={{ marginTop: '1rem' }}>
      <div className="settings-section-title">Advisors</div>

      <div className="settings-row__desc" style={{ paddingTop: '1rem' }}>
        Assign one to each resident from their page. Deactivating an advisor keeps their
        current assignments; deleting one sets those residents to Unassigned. Advisors are
        never used in AI summaries.
      </div>

      <form className="settings-row" onSubmit={handleAdd}>
        <input
          className="form-input"
          style={{ flex: 1 }}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Advisor name"
        />
        <button type="submit" className="btn btn--primary" disabled={!newName.trim()}>
          <Plus size={15} /> Add
        </button>
      </form>

      {error && (
        <div className="settings-row__desc settings-row__note settings-row__note--error">{error}</div>
      )}

      {advisors.length === 0 && (
        <div className="text-sm text-muted" style={{ paddingBottom: '0.5rem' }}>No advisors yet.</div>
      )}

      {advisors.map((a) => (editingId === a.id ? (
        <form key={a.id} className="settings-row" onSubmit={(e) => handleRename(e, a.id)}>
          <input
            className="form-input"
            style={{ flex: 1 }}
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            autoFocus
          />
          <div className="settings-row__control">
            <button type="submit" className="btn btn--primary btn--sm" disabled={!editName.trim()}>Save</button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : (
        <div key={a.id} className="settings-row">
          <div className="settings-row__label">
            {a.name}
            {!a.active && (
              <span className="tag tag--status tag--status-departed" style={{ marginLeft: '0.5rem' }}>Inactive</span>
            )}
          </div>
          <div className="settings-row__control">
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => { setEditingId(a.id); setEditName(a.name); }}
            >
              <Pencil size={13} /> Rename
            </button>
            <button
              className="btn btn--secondary btn--sm"
              onClick={() => run(() => updateAdvisor(a.id, { active: !a.active }))}
            >
              {a.active ? 'Deactivate' : 'Reactivate'}
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => handleDelete(a)} title="Delete">
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      )))}
    </div>
  );
}
