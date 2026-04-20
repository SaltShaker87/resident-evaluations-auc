import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Upload, X, Users } from 'lucide-react';
import { getResidents, createResident, bulkImportResidents } from '../api';
import Avatar from '../components/Avatar';

const TRACK_LABELS = { none: 'None', primary_care: 'Primary Care', fellowship: 'Fellowship', other: 'Other' };

function AddResidentModal({ onClose, onCreated }) {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [pgyYear, setPgyYear] = useState(1);
  const [medicalSchool, setMedicalSchool] = useState('');
  const [interests, setInterests] = useState('');
  const [track, setTrack] = useState('none');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!firstName.trim() || !lastName.trim()) return;
    await createResident({
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      pgy_year: pgyYear,
      medical_school: medicalSchool.trim() || null,
      interests: interests.trim() || null,
      track,
    });
    onCreated();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Add Resident</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <div className="form-group">
              <label>First Name</label>
              <input className="form-input" value={firstName} onChange={(e) => setFirstName(e.target.value)} autoFocus />
            </div>
            <div className="form-group">
              <label>Last Name</label>
              <input className="form-input" value={lastName} onChange={(e) => setLastName(e.target.value)} />
            </div>
          </div>
          <div className="form-group">
            <label>PGY Year</label>
            <select className="form-select" value={pgyYear} onChange={(e) => setPgyYear(Number(e.target.value))}>
              <option value={1}>PGY-1</option>
              <option value={2}>PGY-2</option>
              <option value={3}>PGY-3</option>
              <option value={4}>PGY-4</option>
            </select>
          </div>
          <div className="form-group">
            <label>Medical School</label>
            <input className="form-input" value={medicalSchool} onChange={(e) => setMedicalSchool(e.target.value)} placeholder="e.g. Johns Hopkins" />
          </div>
          <div className="form-group">
            <label>Track</label>
            <select className="form-select" value={track} onChange={(e) => setTrack(e.target.value)}>
              {Object.entries(TRACK_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Interests</label>
            <input className="form-input" value={interests} onChange={(e) => setInterests(e.target.value)} placeholder="e.g. cardiology, global health" />
          </div>
          <div className="modal__actions">
            <button type="button" className="btn btn--secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary">Add Resident</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function BulkImportModal({ onClose, onImported }) {
  const [text, setText] = useState('');
  const [pgyYear, setPgyYear] = useState(1);
  const [preview, setPreview] = useState([]);

  useEffect(() => {
    const lines = text.split('\n').filter((l) => l.trim());
    const parsed = lines.map((line) => {
      const parts = line.trim().replace(/,/g, '').split(/\s+/);
      // Try to parse "First Last" or "Last, First" or "Dr. First Last"
      const filtered = parts.filter((p) => !p.match(/^(dr\.?|md|do|phd|mbbs)$/i));
      if (filtered.length >= 2) {
        return { first_name: filtered[0], last_name: filtered.slice(1).join(' '), pgy_year: pgyYear };
      } else if (filtered.length === 1) {
        return { first_name: filtered[0], last_name: '', pgy_year: pgyYear };
      }
      return null;
    }).filter(Boolean);
    setPreview(parsed);
  }, [text, pgyYear]);

  const handleImport = async () => {
    if (!preview.length) return;
    await bulkImportResidents(preview);
    onImported();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Bulk Import Residents</h2>
        <p className="text-sm text-muted mb-md">Paste a list of names, one per line. You can set the PGY year for the whole batch.</p>
        <div className="form-group">
          <label>PGY Year for this batch</label>
          <select className="form-select" value={pgyYear} onChange={(e) => setPgyYear(Number(e.target.value))}>
            <option value={1}>PGY-1</option>
            <option value={2}>PGY-2</option>
            <option value={3}>PGY-3</option>
            <option value={4}>PGY-4</option>
          </select>
        </div>
        <div className="form-group">
          <label>Names (one per line)</label>
          <textarea
            className="form-textarea"
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Jane Smith\nJohn Doe\nAlex Johnson"}
          />
        </div>
        {preview.length > 0 && (
          <div className="text-sm text-muted mb-md">
            Preview: {preview.length} resident{preview.length !== 1 ? 's' : ''} will be imported
          </div>
        )}
        <div className="modal__actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={handleImport} disabled={!preview.length}>
            Import {preview.length} Resident{preview.length !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ResidentList({ showToast }) {
  const [residents, setResidents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [pgyFilter, setPgyFilter] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showBulk, setShowBulk] = useState(false);
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true);
    try {
      const data = await getResidents();
      setResidents(data);
    } catch (err) {
      showToast('Failed to load residents');
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const filtered = residents.filter((r) => {
    const matchesSearch = `${r.first_name} ${r.last_name}`.toLowerCase().includes(search.toLowerCase());
    const matchesPgy = pgyFilter === null || r.pgy_year === pgyFilter;
    return matchesSearch && matchesPgy;
  });

  const pgyYears = [...new Set(residents.map((r) => r.pgy_year))].sort();

  if (loading) {
    return <div className="loading-state"><div className="spinner" /><span>Loading residents…</span></div>;
  }

  return (
    <>
      <div className="page-header">
        <div className="flex items-center justify-between">
          <div>
            <h1>Residents</h1>
            <p>{residents.length} active resident{residents.length !== 1 ? 's' : ''}</p>
          </div>
          <div className="flex gap-sm">
            <button className="btn btn--secondary" onClick={() => setShowBulk(true)}>
              <Upload size={15} /> Bulk Import
            </button>
            <button className="btn btn--primary" onClick={() => setShowAdd(true)}>
              <Plus size={15} /> Add Resident
            </button>
          </div>
        </div>
      </div>

      <div className="search-bar">
        <Search size={16} className="search-bar__icon" />
        <input
          type="text"
          placeholder="Search by name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="filter-pills">
        <button
          className={`filter-pill ${pgyFilter === null ? 'active' : ''}`}
          onClick={() => setPgyFilter(null)}
        >
          All
        </button>
        {pgyYears.map((y) => (
          <button
            key={y}
            className={`filter-pill ${pgyFilter === y ? 'active' : ''}`}
            onClick={() => setPgyFilter(y)}
          >
            PGY-{y}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state__icon"><Users size={48} /></div>
          <h3>No residents found</h3>
          <p>{residents.length === 0 ? 'Add your first resident to get started.' : 'Try adjusting your search or filter.'}</p>
        </div>
      ) : (
        <div className="resident-grid">
          {filtered.map((r) => (
            <div
              key={r.id}
              className="card card--interactive resident-card"
              onClick={() => navigate(`/residents/${r.id}`)}
            >
              <Avatar
                firstName={r.first_name}
                lastName={r.last_name}
                photoFilename={r.photo_filename}
                size="md"
              />
              <div className="resident-card__info">
                <h3>Dr. {r.first_name} {r.last_name}</h3>
                <div className="resident-card__meta">
                  <span className={`tag tag--pgy tag--pgy-${r.pgy_year}`}>PGY-{r.pgy_year}</span>
                  {r.track && r.track !== 'none' && (
                    <span className="tag tag--track">{TRACK_LABELS[r.track] ?? r.track}</span>
                  )}
                  {r.total_notes > 0 && (
                    <span className="text-sm text-muted">{r.total_notes} note{r.total_notes !== 1 ? 's' : ''}</span>
                  )}
                  {r.open_followups > 0 && (
                    <span className="badge badge--alert">{r.open_followups}</span>
                  )}
                </div>
                {r.medical_school && (
                  <p className="text-sm text-muted resident-card__detail">{r.medical_school}</p>
                )}
                {r.interests && (
                  <p className="text-sm text-muted resident-card__detail resident-card__interests">{r.interests}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {showAdd && (
        <AddResidentModal
          onClose={() => setShowAdd(false)}
          onCreated={() => { setShowAdd(false); load(); showToast('Resident added'); }}
        />
      )}
      {showBulk && (
        <BulkImportModal
          onClose={() => setShowBulk(false)}
          onImported={() => { setShowBulk(false); load(); showToast('Residents imported'); }}
        />
      )}
    </>
  );
}
