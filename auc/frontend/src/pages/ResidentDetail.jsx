import React, { useState, useEffect, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Camera, Plus, Pencil, Trash2, Check,
  Sparkles, ChevronDown, ChevronUp, X, AlertCircle,
} from 'lucide-react';
import {
  getResident, updateResident, deleteResident, uploadPhoto,
  getNotes, createNote, updateNote, deleteNote,
  getResidentFollowups, createFollowup, resolveFollowup, unresolveFollowup, deleteFollowup,
  getSummaries, generateSummary, approveSummary, deleteSummary,
} from '../api';
import Avatar from '../components/Avatar';

const DOMAINS = [
  'Patient Care',
  'Medical Knowledge',
  'Systems-Based Practice',
  'Practice-Based Learning & Improvement',
  'Professionalism',
  'Interpersonal & Communication Skills',
];

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + (dateStr.includes('T') ? '' : 'T00:00:00'));
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ---- Quick Add Note ----

function QuickAddNote({ residentId, onAdded }) {
  const [content, setContent] = useState('');
  const [domain, setDomain] = useState('');
  const [sentiment, setSentiment] = useState('neutral');
  const [priority, setPriority] = useState('routine');
  const textRef = useRef();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim()) return;
    await createNote(residentId, {
      content: content.trim(),
      acgme_domain: domain || null,
      sentiment,
      priority,
    });
    setContent('');
    setDomain('');
    setSentiment('neutral');
    setPriority('routine');
    textRef.current?.focus();
    onAdded();
  };

  return (
    <div className="card" style={{ borderLeft: '3px solid var(--blue-500)' }}>
      <div className="card__title" style={{ marginBottom: '0.75rem' }}>Quick Add Note</div>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <textarea
            ref={textRef}
            className="form-textarea"
            placeholder="Type observation or feedback…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={3}
          />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>ACGME Domain</label>
            <select className="form-select" value={domain} onChange={(e) => setDomain(e.target.value)}>
              <option value="">— Select —</option>
              {DOMAINS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label>Sentiment</label>
            <select className="form-select" value={sentiment} onChange={(e) => setSentiment(e.target.value)}>
              <option value="strength">Strength</option>
              <option value="neutral">Neutral</option>
              <option value="concern">Concern</option>
            </select>
          </div>
          <div className="form-group">
            <label>Priority</label>
            <select className="form-select" value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="routine">Routine</option>
              <option value="important">Important</option>
              <option value="urgent">Urgent</option>
            </select>
          </div>
        </div>
        <button type="submit" className="btn btn--primary" disabled={!content.trim()}>
          <Plus size={15} /> Save Note
        </button>
      </form>
    </div>
  );
}

// ---- Follow-ups Section ----

function FollowupsSection({ residentId, followups, onChanged }) {
  const [adding, setAdding] = useState(false);
  const [desc, setDesc] = useState('');
  const [priority, setPriority] = useState('routine');

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!desc.trim()) return;
    await createFollowup(residentId, { description: desc.trim(), priority });
    setDesc('');
    setPriority('routine');
    setAdding(false);
    onChanged();
  };

  const handleToggle = async (f) => {
    if (f.resolved) {
      await unresolveFollowup(f.id);
    } else {
      await resolveFollowup(f.id);
    }
    onChanged();
  };

  const open = followups.filter((f) => !f.resolved);
  const resolved = followups.filter((f) => f.resolved);

  return (
    <div className="section">
      <div className="section__header">
        <div className="section__title">
          Follow-Up Items {open.length > 0 && <span className="badge badge--alert" style={{ marginLeft: '0.5rem' }}>{open.length}</span>}
        </div>
        <button className="btn btn--secondary btn--sm" onClick={() => setAdding(!adding)}>
          {adding ? <><X size={14} /> Cancel</> : <><Plus size={14} /> Add</>}
        </button>
      </div>

      {adding && (
        <form onSubmit={handleAdd} className="card mb-md" style={{ background: 'var(--slate-50)' }}>
          <div className="form-group">
            <textarea
              className="form-textarea"
              placeholder="Describe the follow-up item…"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={2}
              autoFocus
            />
          </div>
          <div className="flex items-center gap-sm">
            <select className="form-select" style={{ width: 'auto' }} value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="routine">Routine</option>
              <option value="important">Important</option>
              <option value="urgent">Urgent</option>
            </select>
            <button type="submit" className="btn btn--primary btn--sm" disabled={!desc.trim()}>Save</button>
          </div>
        </form>
      )}

      {open.length === 0 && !adding && (
        <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>No open follow-up items.</div>
      )}

      <div>
        {open.map((f) => (
          <div key={f.id} className="followup-item">
            <div className="followup-checkbox" onClick={() => handleToggle(f)} />
            <div className="followup-item__text">
              {f.description}
              <span className={`tag tag--${f.priority}`} style={{ marginLeft: '0.5rem' }}>{f.priority}</span>
            </div>
            <button className="btn btn--ghost btn--sm" onClick={() => { deleteFollowup(f.id); onChanged(); }}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {resolved.length > 0 && (
          <details style={{ marginTop: '0.5rem' }}>
            <summary className="text-sm text-muted" style={{ cursor: 'pointer', padding: '0.35rem 0' }}>
              {resolved.length} resolved item{resolved.length !== 1 ? 's' : ''}
            </summary>
            {resolved.map((f) => (
              <div key={f.id} className="followup-item">
                <div className="followup-checkbox checked" onClick={() => handleToggle(f)}>
                  <Check size={12} color="white" />
                </div>
                <div className="followup-item__text resolved">{f.description}</div>
              </div>
            ))}
          </details>
        )}
      </div>
    </div>
  );
}

// ---- Notes Timeline ----

function NotesTimeline({ notes, onChanged }) {
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState('');

  const startEdit = (note) => {
    setEditingId(note.id);
    setEditText(note.content);
  };

  const saveEdit = async (noteId) => {
    await updateNote(noteId, { content: editText });
    setEditingId(null);
    onChanged();
  };

  const handleDelete = async (noteId) => {
    if (window.confirm('Delete this note?')) {
      await deleteNote(noteId);
      onChanged();
    }
  };

  return (
    <div className="section">
      <div className="section__header">
        <div className="section__title">Notes & Observations</div>
        <span className="text-sm text-muted">{notes.length} total</span>
      </div>
      {notes.length === 0 ? (
        <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>
          No notes yet. Use the Quick Add above to start documenting.
        </div>
      ) : (
        <div className="timeline">
          {notes.map((n) => (
            <div key={n.id} className="timeline-item">
              <div className="timeline-item__header">
                <span className="timeline-item__date">{formatDate(n.created_at)}</span>
                {n.acgme_domain && <span className="tag tag--domain">{n.acgme_domain}</span>}
                <span className={`tag tag--${n.sentiment}`}>{n.sentiment}</span>
                {n.priority !== 'routine' && <span className={`tag tag--${n.priority}`}>{n.priority}</span>}
              </div>
              {editingId === n.id ? (
                <div>
                  <textarea
                    className="form-textarea"
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    rows={3}
                  />
                  <div className="flex gap-sm mt-sm">
                    <button className="btn btn--primary btn--sm" onClick={() => saveEdit(n.id)}>Save</button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="timeline-item__content">{n.content}</div>
              )}
              <div className="timeline-item__actions">
                <button className="btn btn--ghost btn--sm" onClick={() => startEdit(n)}>
                  <Pencil size={13} /> Edit
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => handleDelete(n.id)}>
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- AI Summary Section ----

function SummarySection({ residentId, summaries, noteCount, onChanged, showToast }) {
  const [generating, setGenerating] = useState(false);
  const [draft, setDraft] = useState(null);
  const [editText, setEditText] = useState('');

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const result = await generateSummary(residentId);
      setDraft(result);
      setEditText(result.ai_draft);
    } catch (err) {
      showToast(err.message || 'Failed to generate summary');
    }
    setGenerating(false);
  };

  const handleApprove = async () => {
    await approveSummary(draft.id, editText);
    setDraft(null);
    setEditText('');
    showToast('Summary approved and saved');
    onChanged();
  };

  const handleDiscard = async () => {
    if (draft) {
      await deleteSummary(draft.id);
      setDraft(null);
      setEditText('');
    }
  };

  return (
    <div className="section">
      <div className="section__header">
        <div className="section__title">AI Summaries</div>
        <button
          className="btn btn--primary btn--sm"
          onClick={handleGenerate}
          disabled={generating || noteCount === 0}
        >
          {generating ? (
            <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
          ) : (
            <><Sparkles size={14} /> Generate Summary</>
          )}
        </button>
      </div>

      {noteCount === 0 && (
        <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>
          Add some notes first before generating a summary.
        </div>
      )}

      {draft && (
        <div className="summary-draft">
          <div className="summary-draft__label">AI-Generated Draft — Review & Edit</div>
          <textarea
            className="summary-editor"
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
          />
          <div className="flex gap-sm mt-md">
            <button className="btn btn--primary" onClick={handleApprove}>
              <Check size={15} /> Approve & Save
            </button>
            <button className="btn btn--secondary" onClick={handleDiscard}>Discard</button>
          </div>
        </div>
      )}

      {summaries.filter((s) => s.approved).map((s) => (
        <div key={s.id} className="summary-saved mt-md">
          <div className="summary-saved__label">Approved Summary</div>
          <div className="summary-saved__date">{formatDate(s.approved_at || s.created_at)}</div>
          <div className="summary-saved__text">{s.approved_text}</div>
          <div className="mt-sm">
            <button className="btn btn--ghost btn--sm" onClick={async () => { await deleteSummary(s.id); onChanged(); }}>
              <Trash2 size={13} /> Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---- Edit Resident Modal ----

function EditResidentModal({ resident, onClose, onSaved }) {
  const [firstName, setFirstName] = useState(resident.first_name);
  const [lastName, setLastName] = useState(resident.last_name);
  const [pgyYear, setPgyYear] = useState(resident.pgy_year);

  const handleSubmit = async (e) => {
    e.preventDefault();
    await updateResident(resident.id, { first_name: firstName, last_name: lastName, pgy_year: pgyYear });
    onSaved();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Edit Resident</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <div className="form-group">
              <label>First Name</label>
              <input className="form-input" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
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
          <div className="modal__actions">
            <button type="button" className="btn btn--secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary">Save Changes</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---- Main Detail Page ----

export default function ResidentDetail({ showToast }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const fileRef = useRef();

  const [resident, setResident] = useState(null);
  const [notes, setNotes] = useState([]);
  const [followups, setFollowups] = useState([]);
  const [summaries, setSummaries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showEdit, setShowEdit] = useState(false);

  const loadAll = async () => {
    try {
      const [r, n, f, s] = await Promise.all([
        getResident(id),
        getNotes(id),
        getResidentFollowups(id, true),
        getSummaries(id),
      ]);
      setResident(r);
      setNotes(n);
      setFollowups(f);
      setSummaries(s);
    } catch (err) {
      showToast('Failed to load resident data');
    }
    setLoading(false);
  };

  useEffect(() => { loadAll(); }, [id]);

  const handlePhotoUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadPhoto(id, file);
      showToast('Photo updated');
      loadAll();
    } catch {
      showToast('Failed to upload photo');
    }
  };

  const handleDelete = async () => {
    if (window.confirm(`Remove Dr. ${resident.first_name} ${resident.last_name} and all their data? This cannot be undone.`)) {
      await deleteResident(id);
      showToast('Resident removed');
      navigate('/');
    }
  };

  if (loading) {
    return <div className="loading-state"><div className="spinner" /><span>Loading…</span></div>;
  }

  if (!resident) {
    return (
      <div className="empty-state">
        <AlertCircle size={48} />
        <h3>Resident not found</h3>
        <Link to="/" className="btn btn--primary mt-md">Back to Residents</Link>
      </div>
    );
  }

  return (
    <>
      <Link to="/" className="back-link">
        <ArrowLeft size={16} /> All Residents
      </Link>

      <div className="detail-header">
        <div style={{ position: 'relative', cursor: 'pointer' }} onClick={() => fileRef.current?.click()}>
          <Avatar
            firstName={resident.first_name}
            lastName={resident.last_name}
            photoFilename={resident.photo_filename}
            size="lg"
          />
          <div style={{
            position: 'absolute', bottom: -2, right: -2,
            width: 24, height: 24, borderRadius: '50%',
            background: 'var(--blue-600)', color: 'white',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '2px solid white',
          }}>
            <Camera size={12} />
          </div>
          <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handlePhotoUpload} />
        </div>
        <div className="detail-header__info">
          <h1>Dr. {resident.first_name} {resident.last_name}</h1>
          <div className="detail-header__meta">
            <span className="tag tag--pgy">PGY-{resident.pgy_year}</span>
            <span className="text-sm text-muted">{resident.total_notes} note{resident.total_notes !== 1 ? 's' : ''}</span>
            {resident.open_followups > 0 && (
              <span className="badge badge--alert">{resident.open_followups} open follow-up{resident.open_followups !== 1 ? 's' : ''}</span>
            )}
          </div>
        </div>
        <div className="detail-header__actions">
          <button className="btn btn--secondary btn--sm" onClick={() => setShowEdit(true)}>
            <Pencil size={14} /> Edit
          </button>
          <button className="btn btn--danger btn--sm" onClick={handleDelete}>
            <Trash2 size={14} /> Remove
          </button>
        </div>
      </div>

      <QuickAddNote residentId={id} onAdded={() => { loadAll(); showToast('Note saved'); }} />

      <div style={{ marginTop: '2rem' }}>
        <FollowupsSection residentId={id} followups={followups} onChanged={loadAll} />
      </div>

      <NotesTimeline notes={notes} onChanged={loadAll} />

      <SummarySection
        residentId={id}
        summaries={summaries}
        noteCount={notes.length}
        onChanged={loadAll}
        showToast={showToast}
      />

      {showEdit && (
        <EditResidentModal
          resident={resident}
          onClose={() => setShowEdit(false)}
          onSaved={() => { setShowEdit(false); loadAll(); showToast('Resident updated'); }}
        />
      )}
    </>
  );
}
