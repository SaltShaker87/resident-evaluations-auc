import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Upload, FileText, CheckCircle, AlertCircle, RefreshCw, Info, X, EyeOff, Link2, History,
} from 'lucide-react';
import {
  previewMedhubReport,
  importMedhubReport,
  getMedhubImportLog,
  linkMedhubName,
  ignoreMedhubName,
  syncMedhubApi,
  getMedhubStatus,
} from '../api';
import MedhubQuestionMap from '../components/MedhubQuestionMap';

// SQLite's datetime('now') is UTC without a zone marker.
function formatTimestamp(sqliteUtc) {
  if (!sqliteUtc) return 'Never';
  const d = new Date(`${sqliteUtc.replace(' ', 'T')}Z`);
  if (isNaN(d)) return sqliteUtc;
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function formatDay(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return isNaN(d) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function Count({ value, label, tone }) {
  return (
    <div className={`import-summary__count${tone ? ` import-summary__count--${tone}` : ''}`}>
      <span className="import-summary__number">{value}</span>
      <span className="import-summary__label">{label}</span>
    </div>
  );
}

// The "Not in roster" list: link a name to a resident (saves that resident's
// MedHub name) or ignore it (remembered for later imports). Nothing is guessed.
function NotInRoster({ names, roster, onLink, onIgnore, busyName }) {
  const [choice, setChoice] = useState({});
  return (
    <div className="preview-block">
      <h3 className="preview-block__title">Not in roster</h3>
      <p className="text-sm text-muted preview-block__desc">
        These names have no resident with that MedHub name, so their evaluations are left
        out of this import. Link a name to the right resident, or ignore it — for example
        a resident who has graduated. Ignored names won&apos;t come up again.
      </p>
      <div className="table-scroll">
        <table className="data-table data-table--wrap">
          <thead>
            <tr><th>Name in MedHub</th><th>Rows</th><th>This is…</th><th /></tr>
          </thead>
          <tbody>
            {names.map(({ name, rows }) => (
              <tr key={name}>
                <td><strong>{name}</strong></td>
                <td>{rows}</td>
                <td>
                  <div className="flex gap-sm items-center">
                    <select
                      className="form-select"
                      aria-label={`Resident for ${name}`}
                      value={choice[name] || ''}
                      onChange={(e) => setChoice((c) => ({ ...c, [name]: e.target.value }))}
                    >
                      <option value="">Choose a resident…</option>
                      {roster.map((r) => (
                        <option key={r.id} value={r.id} disabled={!!r.medhub_name}>
                          {r.name} (PGY-{r.pgy_year}{r.status && r.status !== 'active' ? `, ${r.status}` : ''})
                          {r.medhub_name ? ` — already “${r.medhub_name}”` : ''}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn btn--secondary btn--sm"
                      disabled={!choice[name] || busyName === name}
                      onClick={() => onLink(name, choice[name])}
                    >
                      <Link2 size={13} /> Link
                    </button>
                  </div>
                </td>
                <td>
                  <button
                    className="btn btn--ghost btn--sm"
                    disabled={busyName === name}
                    onClick={() => onIgnore(name)}
                  >
                    <EyeOff size={13} /> Ignore
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Preview({ file, preview, onRefresh, onCancel, onSave, saving, showToast }) {
  const [busyName, setBusyName] = useState(null);
  const counts = preview.counts;

  const act = async (name, action, message) => {
    setBusyName(name);
    try {
      await action();
      showToast(message);
      await onRefresh();
    } catch (err) {
      showToast(err.message || 'Something went wrong');
    }
    setBusyName(null);
  };

  return (
    <>
      <div className="flex items-center justify-between" style={{ marginBottom: '0.75rem' }}>
        <div className="text-sm">
          <strong>{file.name}</strong>
          {preview.forms.map((f) => (
            <div key={f.name} className="text-muted">
              {f.name}{f.date_range ? ` · ${f.date_range}` : ''}
            </div>
          ))}
          {preview.generated && <div className="text-muted">Generated in MedHub {preview.generated}</div>}
        </div>
        <button className="btn btn--ghost btn--sm" onClick={onCancel}>
          <X size={14} /> Choose another file
        </button>
      </div>

      <div className="import-summary__counts preview-counts">
        <Count value={counts.new} label="new rows" tone="success" />
        <Count value={counts.duplicates} label="already imported" />
        {counts.changed > 0 && <Count value={counts.changed} label="changed in MedHub" tone="warn" />}
        {counts.unmatched_rows > 0 && <Count value={counts.unmatched_rows} label="rows not in roster" tone="warn" />}
        {counts.ignored_name_rows > 0 && <Count value={counts.ignored_name_rows} label="rows for ignored names" />}
        {counts.ignored_question_rows > 0 && <Count value={counts.ignored_question_rows} label="rows of ignored questions" />}
        {counts.unmapped_rows > 0 && <Count value={counts.unmapped_rows} label="rows waiting for question mapping" tone="warn" />}
      </div>

      {preview.unmapped_questions.length > 0 && (
        <div className="preview-block">
          <h3 className="preview-block__title">Map these questions</h3>
          <p className="text-sm text-muted preview-block__desc">
            AUC hasn&apos;t seen these questions before. Give each a type and a competency area.
            You can change them later in Settings.
          </p>
          <MedhubQuestionMap
            key={preview.unmapped_questions.map((q) => q.form_name + q.question).join('|')}
            items={preview.unmapped_questions}
            competencies={preview.competencies}
            onSaved={async () => { showToast('Questions mapped'); await onRefresh(); }}
          />
        </div>
      )}

      {preview.not_in_roster.length > 0 && (
        <NotInRoster
          names={preview.not_in_roster}
          roster={preview.roster}
          busyName={busyName}
          onLink={(name, residentId) => act(name, () => linkMedhubName(name, residentId), `Linked ${name}`)}
          onIgnore={(name) => act(name, () => ignoreMedhubName(name), `${name} will be ignored`)}
        />
      )}

      {preview.residents.length > 0 && (
        <div className="preview-block">
          <h3 className="preview-block__title">Residents in this report</h3>
          <div className="table-scroll">
            <table className="data-table data-table--wrap">
              <thead>
                <tr>
                  <th>Resident</th><th>New</th><th>Already imported</th><th>Changed</th>
                  <th>Blank comments</th><th>N/A comments</th>
                </tr>
              </thead>
              <tbody>
                {preview.residents.map((r) => (
                  <tr key={r.resident_id}>
                    <td>{r.resident_name} <span className="text-muted">({r.medhub_name})</span></td>
                    <td>{r.new}</td><td>{r.duplicates}</td><td>{r.changed}</td>
                    <td>{r.blank}</td><td>{r.na}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {preview.changed.length > 0 && (
        <div className="preview-block">
          <h3 className="preview-block__title">Changed in MedHub since the last import</h3>
          <p className="text-sm text-muted preview-block__desc">Saving replaces the old answer with the new one.</p>
          <div className="table-scroll">
            <table className="data-table data-table--wrap">
              <thead><tr><th>Resident</th><th>Evaluator</th><th>Rotation</th><th>Question</th><th>Was</th><th>Now</th></tr></thead>
              <tbody>
                {preview.changed.map((c) => (
                  <tr key={`${c.medhub_name}|${c.evaluator}|${c.rotation_start}|${c.question}`}>
                    <td>{c.medhub_name}</td><td>{c.evaluator}</td>
                    <td>{formatDay(c.rotation_start)} – {formatDay(c.rotation_end)}</td>
                    <td>{c.question}</td><td>{c.old_answer || '(blank)'}</td><td>{c.new_answer || '(blank)'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {preview.unrecognised_answers.length > 0 && (
        <div className="alert alert--warning preview-block">
          <AlertCircle size={14} />
          <div>
            <strong>Answers not on the milestone scale.</strong> They are saved, but get no number
            and stay out of graphs and averages:
            <ul className="preview-list">
              {preview.unrecognised_answers.map((u) => (
                <li key={`${u.form_name}|${u.question}|${u.answer}`}>
                  “{u.answer}” × {u.count} — {u.question}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {preview.ignored_names_in_file.length > 0 && (
        <p className="text-sm text-muted preview-block">
          Ignored names in this file: {preview.ignored_names_in_file.map((n) => `${n.name} (${n.rows})`).join(', ')}.
          Manage them in Settings.
        </p>
      )}

      {!preview.can_save && (
        <div className="alert alert--warning preview-block">
          <AlertCircle size={14} /> {preview.blocking_reason}
        </div>
      )}

      <div className="modal__actions" style={{ marginTop: '1.25rem', justifyContent: 'flex-end' }}>
        <button className="btn btn--secondary" onClick={onCancel}>Cancel</button>
        <button className="btn btn--primary" disabled={!preview.can_save || saving} onClick={onSave}>
          {saving ? 'Saving…' : `Save import (${plural(counts.new + counts.changed, 'row')})`}
        </button>
      </div>
    </>
  );
}

function ImportHistory({ log }) {
  return (
    <div className="card">
      <div className="section__header" style={{ marginBottom: '1rem' }}>
        <h2 className="section__title" style={{ fontSize: '1rem' }}>
          <History size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
          Import history
        </h2>
      </div>
      {log.length === 0 ? (
        <p className="text-sm text-muted">No imports yet.</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table data-table--wrap">
            <thead>
              <tr>
                <th>Imported</th><th>File</th><th>Report</th><th>New rows</th>
                <th>Duplicates skipped</th><th>Changed</th><th>Names not in roster</th>
              </tr>
            </thead>
            <tbody>
              {log.map((e) => (
                <tr key={e.id}>
                  <td>{formatTimestamp(e.imported_at)}</td>
                  <td>{e.file_name}</td>
                  <td>
                    {e.report_type}
                    {e.forms.map((f) => (
                      <div key={f.name} className="text-muted">{f.name}{f.date_range ? ` · ${f.date_range}` : ''}</div>
                    ))}
                  </td>
                  <td>{e.new_rows}</td>
                  <td>{e.duplicates_skipped}</td>
                  <td>{e.changed_rows}</td>
                  <td title={(e.details.not_in_roster || []).map((n) => n.name).join(', ')}>
                    {e.unmatched_names}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function MedhubImport({ showToast }) {
  const [status, setStatus] = useState(null);
  const [log, setLog] = useState([]);

  // idle | reading | preview | saving | done
  const [stage, setStage] = useState('idle');
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [readError, setReadError] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const fileInputRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const [s, l] = await Promise.all([getMedhubStatus(), getMedhubImportLog()]);
      setStatus(s);
      setLog(l);
    } catch {
      // silently ignore if backend not running
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const readFile = async (chosen) => {
    setFile(chosen);
    setReadError(null);
    setStage('reading');
    try {
      setPreview(await previewMedhubReport(chosen));
      setStage('preview');
    } catch (err) {
      setReadError(err.message);
      setStage('idle');
    }
  };

  const refreshPreview = async () => {
    try {
      setPreview(await previewMedhubReport(file));
    } catch (err) {
      showToast(err.message);
    }
  };

  const handleSave = async () => {
    setStage('saving');
    try {
      setResult(await importMedhubReport(file));
      setStage('done');
      loadStatus();
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
      setStage('preview');
      refreshPreview();
    }
  };

  const reset = () => {
    setStage('idle');
    setFile(null);
    setPreview(null);
    setResult(null);
    setReadError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) readFile(dropped);
  };

  const handleApiSync = async () => {
    setIsSyncing(true);
    try {
      const r = await syncMedhubApi();
      showToast(r.configured ? `Synced: ${r.imported} imported` : r.message);
      loadStatus();
    } catch (err) {
      showToast(`Sync failed: ${err.message}`);
    }
    setIsSyncing(false);
  };

  return (
    <div className="page-content">
      <div className="section__header" style={{ marginBottom: '1.5rem' }}>
        <h1 className="section__title">MedHub Import</h1>
        {status && (
          <div className="flex items-center gap-sm text-sm text-muted">
            <span>{plural(status.ratings, 'rating')}</span>
            <span>·</span>
            <span>{plural(status.comments, 'comment')}</span>
            <span>·</span>
            <span>Last import: {status.last_import ? formatTimestamp(status.last_import.imported_at) : 'Never'}</span>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gap: '1.5rem' }}>
        <div className="card">
          <div className="section__header" style={{ marginBottom: '1rem' }}>
            <h2 className="section__title" style={{ fontSize: '1rem' }}>
              <FileText size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
              Import an Evaluation Questions Summary
            </h2>
          </div>

          {(stage === 'idle' || stage === 'reading') && (
            <>
              <p className="text-sm text-muted" style={{ marginBottom: '1rem' }}>
                In MedHub, run the <strong>Evaluation Questions Summary</strong> report for one
                evaluation form and a date range, then save the page from your browser
                (File → Save Page As, as a web page). Overlapping date ranges are fine — nothing
                is imported twice. You will see what the file contains before anything is saved.
              </p>
              {readError && (
                <div className="alert alert--danger" style={{ marginBottom: '1rem' }}>
                  <AlertCircle size={14} /> {readError}
                </div>
              )}
              <div
                className={`upload-zone${isDragging ? ' upload-zone--active' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload size={32} style={{ color: 'var(--slate-400)', marginBottom: '0.75rem' }} />
                <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>
                  {stage === 'reading' ? 'Reading report…' : 'Drag & drop the saved report here'}
                </p>
                <p className="text-sm text-muted">or click to browse (.html)</p>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".html,.htm"
                style={{ display: 'none' }}
                onChange={(e) => { if (e.target.files?.[0]) readFile(e.target.files[0]); }}
              />
            </>
          )}

          {(stage === 'preview' || stage === 'saving') && preview && (
            <Preview
              file={file}
              preview={preview}
              saving={stage === 'saving'}
              onRefresh={refreshPreview}
              onCancel={reset}
              onSave={handleSave}
              showToast={showToast}
            />
          )}

          {stage === 'done' && result && (
            <div className="import-summary">
              <CheckCircle size={24} className="import-summary__icon" />
              <h3 className="import-summary__title">Import saved</h3>
              <div className="import-summary__counts">
                <Count value={result.counts.new} label="new rows" tone="success" />
                <Count value={result.counts.duplicates} label="duplicates skipped" />
                {result.counts.changed > 0 && <Count value={result.counts.changed} label="updated" />}
                {result.not_in_roster.length > 0 && (
                  <Count value={result.not_in_roster.length} label="names not in roster" tone="warn" />
                )}
              </div>
              <button className="btn btn--secondary" style={{ marginTop: '1rem' }} onClick={reset}>
                Import another file
              </button>
            </div>
          )}
        </div>

        <ImportHistory log={log} />

        <div className="card">
          <div className="section__header" style={{ marginBottom: '1rem' }}>
            <h2 className="section__title" style={{ fontSize: '1rem' }}>
              <RefreshCw size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
              MedHub API Sync
            </h2>
          </div>
          {status?.api_configured === false ? (
            <div className="api-unconfigured">
              <Info size={20} style={{ color: 'var(--slate-400)', flexShrink: 0 }} />
              <div>
                <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>MedHub API not configured</p>
                <p className="text-sm text-muted">
                  Open <code>auc/backend/config.py</code> and fill in{' '}
                  <code>MEDHUB_API_URL</code> and <code>MEDHUB_API_KEY</code>.
                  See <code>auc/backend/medhub_api.py</code> for detailed setup instructions.
                </p>
              </div>
            </div>
          ) : (
            <button className="btn btn--primary" onClick={handleApiSync} disabled={isSyncing}>
              {isSyncing
                ? <><div className="spinner spinner--sm" /> Syncing…</>
                : <><RefreshCw size={14} /> Sync from MedHub API</>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
