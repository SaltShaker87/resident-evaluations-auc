import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Upload, FileText, CheckCircle, AlertCircle, RefreshCw, Info, X,
} from 'lucide-react';
import {
  parseMedhubCsv,
  importMedhubCsv,
  syncMedhubApi,
  getMedhubStatus,
  getResidentsForMatching,
} from '../api';

// Fields we want the user to map from their CSV columns
const MAPPING_FIELDS = [
  { key: 'resident_name', label: 'Resident Name', required: true },
  { key: 'evaluator',     label: 'Evaluator Name', required: false },
  { key: 'rotation',      label: 'Rotation',        required: false },
  { key: 'domain',        label: 'Competency Domain', required: false },
  { key: 'score',         label: 'Score',            required: false },
  { key: 'comments',      label: 'Comments',         required: false },
  { key: 'evaluation_date', label: 'Evaluation Date', required: false },
];

function formatDate(isoStr) {
  if (!isoStr) return 'Never';
  const d = new Date(isoStr);
  if (isNaN(d)) return isoStr;
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

export default function MedhubImport({ showToast }) {
  // Status bar state
  const [status, setStatus] = useState(null);

  // CSV import state machine: idle | parsing | parsed | importing | unmatched | done
  const [importStage, setImportStage] = useState('idle');
  const [csvFile, setCsvFile] = useState(null);
  const [csvHeaders, setCsvHeaders] = useState([]);
  const [csvRows, setCsvRows] = useState([]);
  const [csvPreview, setCsvPreview] = useState([]);
  const [mapping, setMapping] = useState({});
  const [importResult, setImportResult] = useState(null);
  const [unmatched, setUnmatched] = useState([]);
  const [manualMatches, setManualMatches] = useState({});
  const [residents, setResidents] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [parseError, setParseError] = useState(null);

  // API sync state
  const [isSyncing, setIsSyncing] = useState(false);

  const fileInputRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      const s = await getMedhubStatus();
      setStatus(s);
    } catch {
      // silently ignore if backend not running
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // -------------------------------------------------------------------------
  // CSV drag-and-drop
  // -------------------------------------------------------------------------

  const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = () => setIsDragging(false);
  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  };

  const processFile = async (file) => {
    setCsvFile(file);
    setParseError(null);
    setImportStage('parsing');
    try {
      const result = await parseMedhubCsv(file);
      setCsvHeaders(result.headers);
      setCsvPreview(result.preview);
      // Auto-detect mapping from common MedHub column name patterns
      const autoMap = {};
      const h = result.headers;
      const find = (...terms) => h.find(col =>
        terms.some(t => col.toLowerCase().includes(t.toLowerCase()))
      ) || '';
      autoMap.resident_name   = find('resident', 'trainee', 'student');
      autoMap.evaluator       = find('evaluator', 'faculty', 'attending', 'rater');
      autoMap.rotation        = find('rotation', 'service', 'clerkship');
      autoMap.domain          = find('competency', 'domain', 'milestone');
      autoMap.score           = find('score', 'rating', 'grade', 'level');
      autoMap.comments        = find('comment', 'feedback', 'narrative', 'text');
      autoMap.evaluation_date = find('date', 'eval_date', 'completed');
      setMapping(autoMap);
      setImportStage('parsed');
    } catch (err) {
      setParseError(err.message);
      setImportStage('idle');
    }
  };

  // -------------------------------------------------------------------------
  // Import with mapping
  // -------------------------------------------------------------------------

  const runImport = async (rows, currentMapping, pendingManual = {}) => {
    setImportStage('importing');
    try {
      const result = await importMedhubCsv(rows, currentMapping, pendingManual);
      setImportResult(result);
      if (result.unmatched?.length > 0 && Object.keys(pendingManual).length === 0) {
        const res = await getResidentsForMatching();
        setResidents(res);
        setUnmatched(result.unmatched);
        const initMatches = {};
        result.unmatched.forEach(u => { initMatches[u.csv_name] = ''; });
        setManualMatches(initMatches);
        setCsvRows(rows); // save for re-use in finalize
        setImportStage('unmatched');
      } else {
        setImportStage('done');
        loadStatus();
      }
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
      setImportStage('parsed');
    }
  };

  const handleConfirmMapping = async () => {
    // Parse all rows from the file client-side, then import
    setImportStage('importing');
    try {
      const rows = await parseCsvFile(csvFile);
      if (!rows.length) {
        showToast('CSV appears to have no data rows.');
        setImportStage('parsed');
        return;
      }
      await runImport(rows, mapping, {});
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
      setImportStage('parsed');
    }
  };

  const handleFinalizeUnmatched = () => {
    const resolved = {};
    Object.entries(manualMatches).forEach(([name, id]) => {
      if (id) resolved[name] = id;
    });
    runImport(csvRows, mapping, resolved);
  };

  const resetImport = () => {
    setImportStage('idle');
    setCsvFile(null);
    setCsvHeaders([]);
    setCsvRows([]);
    setCsvPreview([]);
    setMapping({});
    setImportResult(null);
    setUnmatched([]);
    setManualMatches({});
    setParseError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // -------------------------------------------------------------------------
  // API sync
  // -------------------------------------------------------------------------

  const handleApiSync = async () => {
    setIsSyncing(true);
    try {
      const result = await syncMedhubApi();
      if (!result.configured) {
        showToast(result.message);
      } else {
        showToast(`Synced: ${result.imported} imported, ${result.skipped_duplicates} duplicates skipped`);
        loadStatus();
      }
    } catch (err) {
      showToast(`Sync failed: ${err.message}`);
    } finally {
      setIsSyncing(false);
    }
  };

  // -------------------------------------------------------------------------
  // Render helpers
  // -------------------------------------------------------------------------

  const MappingSelect = ({ fieldKey, label, required }) => (
    <div className="form-group" style={{ marginBottom: '0.5rem' }}>
      <label style={{ fontSize: '0.8rem', fontWeight: 500 }}>
        {label}{required && <span style={{ color: 'var(--red-500)', marginLeft: 2 }}>*</span>}
      </label>
      <select
        className="form-select"
        value={mapping[fieldKey] || ''}
        onChange={e => setMapping(m => ({ ...m, [fieldKey]: e.target.value }))}
      >
        <option value="">— skip —</option>
        {csvHeaders.map(h => (
          <option key={h} value={h}>{h}</option>
        ))}
      </select>
    </div>
  );

  return (
    <div className="page-content">
      {/* ------------------------------------------------------------------ */}
      {/* Status bar                                                           */}
      {/* ------------------------------------------------------------------ */}
      <div className="section__header" style={{ marginBottom: '1.5rem' }}>
        <h1 className="section__title">MedHub Import</h1>
        {status && (
          <div className="flex items-center gap-sm text-sm text-muted">
            <span>{status.total_evaluations} evaluations in database</span>
            <span>·</span>
            <span>Last CSV import: {formatDate(status.last_csv_import)}</span>
            <span>·</span>
            <span>Last API sync: {formatDate(status.last_api_sync)}</span>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gap: '1.5rem' }}>

        {/* ---------------------------------------------------------------- */}
        {/* Section 1 — CSV Import                                            */}
        {/* ---------------------------------------------------------------- */}
        <div className="card">
          <div className="section__header" style={{ marginBottom: '1rem' }}>
            <h2 className="section__title" style={{ fontSize: '1rem' }}>
              <FileText size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
              CSV Import
            </h2>
          </div>

          {/* IDLE — drop zone */}
          {(importStage === 'idle' || importStage === 'parsing') && (
            <>
              {parseError && (
                <div className="alert alert--danger" style={{ marginBottom: '1rem' }}>
                  <AlertCircle size={14} /> {parseError}
                </div>
              )}
              <div
                className={`upload-zone${isDragging ? ' upload-zone--active' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload size={32} style={{ color: 'var(--slate-400)', marginBottom: '0.75rem' }} />
                <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>
                  {importStage === 'parsing' ? 'Reading file…' : 'Drag & drop a CSV file here'}
                </p>
                <p className="text-sm text-muted">or click to browse</p>
                <p className="text-sm text-muted" style={{ marginTop: '0.5rem' }}>
                  Accepts .csv files exported from MedHub
                </p>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv"
                style={{ display: 'none' }}
                onChange={e => { if (e.target.files?.[0]) processFile(e.target.files[0]); }}
              />
            </>
          )}

          {/* PARSED — preview + column mapping */}
          {importStage === 'parsed' && (
            <>
              <div className="flex items-center justify-between" style={{ marginBottom: '0.75rem' }}>
                <p className="text-sm text-muted">
                  <strong>{csvFile?.name}</strong> — showing first {csvPreview.length} rows
                </p>
                <button className="btn btn--ghost btn--sm" onClick={resetImport}>
                  <X size={14} /> Change file
                </button>
              </div>

              {/* Preview table */}
              <div className="table-scroll" style={{ marginBottom: '1.5rem' }}>
                <table className="data-table">
                  <thead>
                    <tr>{csvHeaders.map(h => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {csvPreview.map((row, i) => (
                      <tr key={i}>
                        {csvHeaders.map(h => <td key={h}>{row[h] ?? ''}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Column mapping */}
              <h3 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                Map CSV columns to database fields
              </h3>
              <div className="mapping-grid">
                {MAPPING_FIELDS.map(f => (
                  <MappingSelect key={f.key} fieldKey={f.key} label={f.label} required={f.required} />
                ))}
              </div>
              {!mapping.resident_name && (
                <p className="text-sm" style={{ color: 'var(--amber-600)', marginTop: '0.5rem' }}>
                  <AlertCircle size={13} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                  Resident Name column is required before importing.
                </p>
              )}
              <div className="modal__actions" style={{ marginTop: '1.25rem', justifyContent: 'flex-end' }}>
                <button className="btn btn--secondary" onClick={resetImport}>Cancel</button>
                <button
                  className="btn btn--primary"
                  disabled={!mapping.resident_name}
                  onClick={handleConfirmMapping}
                >
                  Import
                </button>
              </div>
            </>
          )}

          {/* IMPORTING — spinner */}
          {importStage === 'importing' && (
            <div className="loading-state">
              <div className="spinner" />
              <p className="text-muted">Importing evaluations…</p>
            </div>
          )}

          {/* UNMATCHED — manual resolution */}
          {importStage === 'unmatched' && (
            <>
              <div className="alert alert--warning" style={{ marginBottom: '1rem' }}>
                <AlertCircle size={14} />
                <strong>{unmatched.length} resident name{unmatched.length !== 1 ? 's' : ''} in the CSV
                could not be automatically matched.</strong> Match them below or skip.
              </div>
              <table className="data-table unmatched-table" style={{ marginBottom: '1.25rem' }}>
                <thead>
                  <tr>
                    <th>Name in CSV</th>
                    <th>Match to resident</th>
                  </tr>
                </thead>
                <tbody>
                  {unmatched.map(u => (
                    <tr key={u.csv_name + u.row_index}>
                      <td><strong>{u.csv_name || <em className="text-muted">blank</em>}</strong></td>
                      <td>
                        <select
                          className="form-select"
                          value={manualMatches[u.csv_name] || ''}
                          onChange={e => setManualMatches(m => ({ ...m, [u.csv_name]: e.target.value }))}
                        >
                          <option value="">— skip this row —</option>
                          {residents.map(r => (
                            <option key={r.id} value={r.id}>
                              {r.first_name} {r.last_name} (PGY-{r.pgy_year})
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="modal__actions" style={{ justifyContent: 'flex-end' }}>
                <button className="btn btn--secondary" onClick={resetImport}>Start over</button>
                <button className="btn btn--primary" onClick={handleFinalizeUnmatched}>
                  Finalize import
                </button>
              </div>
            </>
          )}

          {/* DONE — summary */}
          {importStage === 'done' && importResult && (
            <div className="import-summary">
              <CheckCircle size={24} className="import-summary__icon" />
              <h3 className="import-summary__title">Import complete</h3>
              <div className="import-summary__counts">
                <div className="import-summary__count import-summary__count--success">
                  <span className="import-summary__number">{importResult.imported}</span>
                  <span className="import-summary__label">records imported</span>
                </div>
                <div className="import-summary__count">
                  <span className="import-summary__number">{importResult.skipped_duplicates}</span>
                  <span className="import-summary__label">duplicates skipped</span>
                </div>
                {importResult.unmatched?.length > 0 && (
                  <div className="import-summary__count import-summary__count--warn">
                    <span className="import-summary__number">{importResult.unmatched.length}</span>
                    <span className="import-summary__label">rows skipped (unmatched)</span>
                  </div>
                )}
              </div>
              <button className="btn btn--secondary" style={{ marginTop: '1rem' }} onClick={resetImport}>
                Import another file
              </button>
            </div>
          )}
        </div>

        {/* ---------------------------------------------------------------- */}
        {/* Section 2 — API Sync                                              */}
        {/* ---------------------------------------------------------------- */}
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
            <div className="flex items-center gap-sm">
              <button
                className="btn btn--primary"
                onClick={handleApiSync}
                disabled={isSyncing}
              >
                {isSyncing
                  ? <><div className="spinner spinner--sm" /> Syncing…</>
                  : <><RefreshCw size={14} /> Sync from MedHub API</>
                }
              </button>
              {status?.last_api_sync && (
                <span className="text-sm text-muted">
                  Last synced {formatDate(status.last_api_sync)}
                </span>
              )}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Client-side CSV parsing (for full-file import)
// ---------------------------------------------------------------------------
function parseCsvFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try { resolve(parseCsvText(e.target.result)); }
      catch (err) { reject(err); }
    };
    reader.onerror = () => reject(new Error('Could not read file'));
    reader.readAsText(file);
  });
}

function parseCsvText(text) {
  // RFC 4180-compatible CSV parser (handles quoted fields with commas/newlines)
  const lines = [];
  let field = '', row = [], inQuote = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];
    if (inQuote) {
      if (ch === '"' && next === '"') { field += '"'; i++; }
      else if (ch === '"') { inQuote = false; }
      else { field += ch; }
    } else {
      if (ch === '"') { inQuote = true; }
      else if (ch === ',') { row.push(field); field = ''; }
      else if (ch === '\n' || (ch === '\r' && next === '\n')) {
        row.push(field); field = '';
        if (row.some(Boolean)) lines.push(row);
        row = [];
        if (ch === '\r') i++;
      } else if (ch === '\r') {
        row.push(field); field = '';
        if (row.some(Boolean)) lines.push(row);
        row = [];
      } else { field += ch; }
    }
  }
  if (field || row.length) { row.push(field); if (row.some(Boolean)) lines.push(row); }
  if (lines.length < 2) return [];
  const headers = lines[0].map(h => h.trim().replace(/^\uFEFF/, ''));
  return lines.slice(1).map(cols => {
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (cols[i] ?? '').trim(); });
    return obj;
  });
}
