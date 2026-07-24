import React, { useState, useEffect, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Camera, Plus, Pencil, Trash2, Check,
  Sparkles, ChevronDown, ChevronUp, X, AlertCircle, FileDown,
} from 'lucide-react';
import {
  getResident, updateResident, deleteResident, uploadPhoto,
  getNotes, createNote, updateNote, deleteNote,
  getResidentFollowups, createFollowup, resolveFollowup, unresolveFollowup, deleteFollowup,
  getSummaries, generateSummaryStream, approveSummary, deleteSummary, getOllamaModels,
  exportSummaryPdf,
} from '../api';
import Avatar from '../components/Avatar';
import SummaryReport from '../components/SummaryReport';

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
  // SQLite stores datetime as 'YYYY-MM-DD HH:MM:SS' (space-separated); normalize to ISO
  const iso = dateStr.replace(' ', 'T');
  const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ---- Quick Add Note ----

const SOURCE_OPTIONS = [
  'CCC Meeting',
  'Direct Observation',
  'MedHub / Evaluation',
  'Colleague Report',
  'Other',
];

function todayStr() {
  return new Date().toISOString().split('T')[0];
}

function QuickAddNote({ residentId, onAdded }) {
  const [content, setContent] = useState('');
  const [domain, setDomain] = useState('');
  const [sentiment, setSentiment] = useState('neutral');
  const [priority, setPriority] = useState('routine');
  const [source, setSource] = useState('');
  const [noteDate, setNoteDate] = useState(todayStr);
  const textRef = useRef();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim()) return;
    await createNote(residentId, {
      content: content.trim(),
      acgme_domain: domain || null,
      sentiment,
      priority,
      source: source || null,
      note_date: noteDate,
    });
    setContent('');
    setDomain('');
    setSentiment('neutral');
    setPriority('routine');
    setSource('');
    setNoteDate(todayStr());
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
            <label>Source</label>
            <select className="form-select" value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">— Select —</option>
              {SOURCE_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
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
        <div className="form-row" style={{ marginTop: '0.5rem' }}>
          <div className="form-group" style={{ maxWidth: '200px' }}>
            <label>Date</label>
            <input
              type="date"
              className="form-input"
              value={noteDate}
              onChange={(e) => setNoteDate(e.target.value)}
            />
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
  const [followupDate, setFollowupDate] = useState(todayStr);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!desc.trim()) return;
    await createFollowup(residentId, { description: desc.trim(), priority, note_date: followupDate });
    setDesc('');
    setPriority('routine');
    setFollowupDate(todayStr());
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
            <input
              type="date"
              className="form-input"
              style={{ width: 'auto' }}
              value={followupDate}
              onChange={(e) => setFollowupDate(e.target.value)}
            />
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
              {f.created_at && <span className="text-sm text-muted" style={{ marginLeft: '0.5rem' }}>{formatDate(f.created_at)}</span>}
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
                <div className="followup-item__text resolved">
                  {f.description}
                  {f.created_at && <span className="text-sm text-muted" style={{ marginLeft: '0.5rem' }}>{formatDate(f.created_at)}</span>}
                </div>
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
                {n.source && <span className="tag tag--source">{n.source}</span>}
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

const DRAFT_DISCLAIMER =
  'Suggested levels are drafts for the Clinical Competency Committee to discuss — ' +
  'not final determinations.';

// Approved summaries are stored as JSON now; anything older is a markdown blob.
function parseStructuredSummary(text) {
  if (!text || !text.trimStart().startsWith('{')) return null;
  try {
    const parsed = JSON.parse(text);
    return parsed?.schema === 'auc.summary.v1' ? parsed : null;
  } catch (_e) {
    return null;
  }
}

// Parse an SSE frame ("event: name\ndata: {...}") into { event, data }.
function parseSseFrame(frame) {
  let event = 'message';
  const dataLines = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch (_e) {
    return null;
  }
}

function SummarySection({ residentId, summaries, noteCount, onChanged, showToast }) {
  const [generating, setGenerating] = useState(false);
  const [report, setReport] = useState(null);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [summaryId, setSummaryId] = useState(null);
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [ollamaError, setOllamaError] = useState('');

  useEffect(() => {
    getOllamaModels().then((result) => {
      if (Array.isArray(result) && result.length > 0) {
        setModels(result);
        const saved = localStorage.getItem('defaultOllamaModel');
        setSelectedModel(saved && result.includes(saved) ? saved : result[0]);
        setOllamaError('');
      } else if (result?.error) {
        setOllamaError(result.error);
      } else {
        setOllamaError('Ollama is reachable but no models are installed.');
      }
    }).catch(() => {
      setOllamaError('Could not connect to Ollama — make sure it is running.');
    });
  }, []);

  const resetDraft = () => {
    setReport(null);
    setProgress({ completed: 0, total: 0 });
    setSummaryId(null);
  };

  const handleGenerate = async () => {
    setGenerating(true);
    resetDraft();

    try {
      const response = await generateSummaryStream(residentId, selectedModel || null);
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let failed = false;
      let finished = false;   // local: setSummaryId's state isn't readable in this closure

      const processFrame = (frame) => {
        const parsed = parseSseFrame(frame);
        if (!parsed) return;
        const { event, data } = parsed;

        if (event === 'start') {
          // The skeleton comes from the backend's ontology, so all 21 cards render
          // straight away and fill in place as results arrive.
          setReport({
            schema: 'auc.summary.v1',
            resident: data.resident,
            model: data.model,
            sections: data.sections,
          });
          setProgress({ completed: 0, total: data.total });
        } else if (event === 'subcompetency') {
          setReport((prev) => (prev ? {
            ...prev,
            sections: prev.sections.map((s) => (s.id === data.id ? data : s)),
          } : prev));
          setProgress({ completed: data.completed, total: data.total });
        } else if (event === 'done') {
          finished = true;
          setSummaryId(data.summary_id);
          setProgress({ completed: data.completed, total: data.total });
        } else if (event === 'error') {
          failed = true;
          showToast(data.message || 'Failed to generate summary');
          resetDraft();
        }
      };

      // SSE frames are separated by a blank line.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop();
        frames.forEach(processFrame);
      }
      if (buffer.trim()) processFrame(buffer);

      if (!failed && !finished) {
        // Stream ended without a done event — the run was cut short server-side.
        // The partial draft stays on screen but has no id, so Approve stays disabled.
        showToast('Generation ended early — the draft is incomplete.');
      }
    } catch (err) {
      showToast(err.message || 'Failed to generate summary');
      resetDraft();
    } finally {
      setGenerating(false);
    }
  };

  const handleApprove = async () => {
    if (!summaryId) { showToast('No summary ID — cannot approve'); return; }
    await approveSummary(summaryId, JSON.stringify(report));
    resetDraft();
    showToast('Summary approved and saved');
    onChanged();
  };

  const handleDiscard = async () => {
    if (summaryId) await deleteSummary(summaryId);
    resetDraft();
  };

  return (
    <div className="section">
      <div className="section__header">
        <div className="section__title">AI Summaries</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {ollamaError ? (
            <span className="text-sm text-muted">Ollama unavailable</span>
          ) : (
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.25rem 0.5rem', fontSize: '0.85rem' }}
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={generating}
            >
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          <button
            className="btn btn--primary btn--sm"
            onClick={handleGenerate}
            disabled={generating || noteCount === 0 || !!ollamaError}
          >
            {generating ? (
              <><div className="spinner" style={{ width: 14, height: 14 }} /> Generating…</>
            ) : (
              <><Sparkles size={14} /> Generate Summary</>
            )}
          </button>
        </div>
      </div>

      {ollamaError && (
        <div className="text-sm" style={{ padding: '0.5rem 0', color: 'var(--red-600, #dc2626)' }}>
          {ollamaError}
        </div>
      )}

      {noteCount === 0 && !ollamaError && (
        <div className="text-sm text-muted" style={{ padding: '0.5rem 0' }}>
          Add some notes first before generating a summary.
        </div>
      )}

      {report && (
        <div className="summary-draft">
          <div className="summary-draft__head">
            <div className="summary-draft__label">AI-Generated Draft — Review & Edit</div>
            {generating && progress.total > 0 && (
              <div className="summary-progress">
                <span className="summary-progress__count">
                  {progress.completed} of {progress.total}
                </span>
                <div className="summary-progress__bar">
                  <div
                    className="summary-progress__fill"
                    style={{ width: `${(progress.completed / progress.total) * 100}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          <div className="summary-disclaimer">
            <AlertCircle size={14} /> {DRAFT_DISCLAIMER}
          </div>

          <SummaryReport report={report} editable onChange={setReport} />

          {!generating && (
            <div className="flex gap-sm mt-md">
              <button className="btn btn--primary" onClick={handleApprove} disabled={!summaryId}>
                <Check size={15} /> Approve & Save
              </button>
              <button className="btn btn--secondary" onClick={handleDiscard}>Discard</button>
            </div>
          )}
        </div>
      )}

      {summaries.filter((s) => s.approved).map((s) => {
        const structured = parseStructuredSummary(s.approved_text);
        return (
          <div key={s.id} className="summary-saved mt-md">
            <div className="summary-saved__label">Approved Summary</div>
            <div className="summary-saved__date">{formatDate(s.approved_at || s.created_at)}</div>
            {structured ? (
              <>
                <div className="summary-disclaimer">
                  <AlertCircle size={14} /> {DRAFT_DISCLAIMER}
                </div>
                <SummaryReport report={structured} />
              </>
            ) : (
              // Summaries approved before the structured format was introduced.
              <div className="summary-saved__text">{s.approved_text}</div>
            )}
            <div className="mt-sm flex gap-sm">
              <button
                className="btn btn--secondary btn--sm"
                onClick={async () => {
                  try {
                    await exportSummaryPdf(residentId, s.id);
                  } catch (err) {
                    showToast(err.message || 'Could not export PDF');
                  }
                }}
              >
                <FileDown size={13} /> Export PDF
              </button>
              <button className="btn btn--ghost btn--sm" onClick={async () => { await deleteSummary(s.id); onChanged(); }}>
                <Trash2 size={13} /> Remove
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---- Edit Resident Modal ----

const TRACK_LABELS = { none: 'None', primary_care: 'Primary Care', fellowship: 'Fellowship', other: 'Other' };

function EditResidentModal({ resident, onClose, onSaved }) {
  const [firstName, setFirstName] = useState(resident.first_name);
  const [lastName, setLastName] = useState(resident.last_name);
  const [pgyYear, setPgyYear] = useState(resident.pgy_year);
  const [medicalSchool, setMedicalSchool] = useState(resident.medical_school ?? '');
  const [interests, setInterests] = useState(resident.interests ?? '');
  const [track, setTrack] = useState(resident.track ?? 'none');
  const [isPrelim, setIsPrelim] = useState(!!resident.is_prelim);
  const [prelimSpecialty, setPrelimSpecialty] = useState(resident.prelim_specialty ?? '');

  const handleSubmit = async (e) => {
    e.preventDefault();
    await updateResident(resident.id, {
      first_name: firstName,
      last_name: lastName,
      pgy_year: pgyYear,
      medical_school: medicalSchool.trim() || null,
      interests: interests.trim() || null,
      track,
      is_prelim: isPrelim,
      prelim_specialty: isPrelim ? (prelimSpecialty.trim() || null) : null,
    });
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
          <div className="form-group">
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
              <input type="checkbox" checked={isPrelim} onChange={(e) => setIsPrelim(e.target.checked)} />
              Prelim Resident
            </label>
          </div>
          {isPrelim && (
            <div className="form-group">
              <label>Prelim Specialty</label>
              <input className="form-input" value={prelimSpecialty} onChange={(e) => setPrelimSpecialty(e.target.value)} placeholder="e.g. Dermatology, Ophthalmology" />
            </div>
          )}
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
            <span className={`tag tag--pgy tag--pgy-${resident.pgy_year}`}>PGY-{resident.pgy_year}</span>
            {resident.is_prelim ? (
              <span className="tag tag--prelim">Prelim{resident.prelim_specialty ? ` · ${resident.prelim_specialty}` : ''}</span>
            ) : null}
            <span className="text-sm text-muted">{resident.total_notes} note{resident.total_notes !== 1 ? 's' : ''}</span>
            {resident.open_followups > 0 && (
              <span className="badge badge--alert">{resident.open_followups} open follow-up{resident.open_followups !== 1 ? 's' : ''}</span>
            )}
          </div>
          <div className="detail-header__profile">
            <div className="profile-field">
              <span className="profile-field__label">Medical School</span>
              <span className="profile-field__value">{resident.medical_school || 'None'}</span>
            </div>
            <div className="profile-field">
              <span className="profile-field__label">Track</span>
              <span className="profile-field__value">{TRACK_LABELS[resident.track] ?? 'None'}</span>
            </div>
            <div className="profile-field">
              <span className="profile-field__label">Interests</span>
              <span className="profile-field__value">{resident.interests || 'None'}</span>
            </div>
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
