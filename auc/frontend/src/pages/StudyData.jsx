import React, { useCallback, useEffect, useState } from 'react';
import { Download, FlaskConical, AlertTriangle } from 'lucide-react';
import { getCccStats, downloadCccCsv } from '../api';

/**
 * Study Data — the export side of the CCC meeting capture.
 *
 * Every file here is keyed by study_code and by nothing else: no name, no resident id.
 * The one thing that can change that is the "include free text" toggle, which is off by
 * default and warned about, because free-text answers are where identifiable narrative
 * hides. See CCC.md for the column-by-column codebook.
 */

const EXPORTS = [
  {
    key: 'resident-sessions',
    label: 'Resident sessions',
    desc: 'One row per resident per meeting: what the room produced unprompted, counts '
        + 'of what you contributed and what it changed, time on the resident, and time '
        + 'to your first contribution.',
  },
  {
    key: 'contributions',
    label: 'Contributions',
    desc: 'One row per contribution you logged, with its type, outcome, and how many '
        + 'seconds it took to retrieve.',
  },
  {
    key: 'action-items',
    label: 'Action item recall',
    desc: 'One row per prior action item checked in a later meeting: whether the room '
        + 'recalled it unprompted, and whether you had to surface it.',
  },
];

export default function StudyData({ showToast }) {
  const [stats, setStats] = useState(null);
  const [includeText, setIncludeText] = useState(
    () => localStorage.getItem('cccIncludeText') === '1'
  );
  const [busy, setBusy] = useState('');

  useEffect(() => {
    getCccStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  const toggleIncludeText = useCallback((next) => {
    setIncludeText(next);
    localStorage.setItem('cccIncludeText', next ? '1' : '0');
  }, []);

  const download = async (key) => {
    setBusy(key);
    try {
      await downloadCccCsv(key, includeText);
    } catch (err) {
      showToast?.(err.message || 'Download failed');
    } finally {
      setBusy('');
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1><FlaskConical size={20} /> Study Data</h1>
        <p className="text-muted">
          De-identified CSV exports of the CCC meeting capture. Residents appear only as
          study codes (R001, R002, …).
        </p>
      </div>

      <div className="card settings-card">
        <div className="settings-section-title">Totals to date</div>
        {stats ? (
          <div className="ccc-totals">
            <div><strong>{stats.sessions}</strong> meeting{stats.sessions === 1 ? '' : 's'}</div>
            <div><strong>{stats.resident_logs}</strong> resident log{stats.resident_logs === 1 ? '' : 's'}</div>
            <div><strong>{stats.residents_covered}</strong> resident{stats.residents_covered === 1 ? '' : 's'} covered</div>
            <div><strong>{stats.contributions}</strong> contribution{stats.contributions === 1 ? '' : 's'}</div>
            <div><strong>{stats.action_items}</strong> action item{stats.action_items === 1 ? '' : 's'}</div>
            <div><strong>{stats.action_item_checks}</strong> recall check{stats.action_item_checks === 1 ? '' : 's'}</div>
          </div>
        ) : (
          <div className="text-sm text-muted">Could not load totals.</div>
        )}
      </div>

      <div className="card settings-card" style={{ marginTop: '1rem' }}>
        <div className="settings-section-title">Free text</div>
        <div className="settings-row">
          <div>
            <div className="settings-row__label">Include free-text answers</div>
            <div className="settings-row__desc">
              Off by default. The exports carry only coded values — the free-text fields
              (what the room raised, contribution details, pushback notes, closing notes,
              action item text) are left out of the file entirely. Turn this on only if
              your analysis needs the narrative, and treat the result as identifiable.
            </div>
          </div>
          <div className="settings-row__control">
            <label className="ccc-switch">
              <input
                type="checkbox"
                checked={includeText}
                onChange={(e) => toggleIncludeText(e.target.checked)}
              />
              <span>{includeText ? 'Included' : 'Excluded'}</span>
            </label>
          </div>
        </div>
        {includeText && (
          <div className="alert alert--warning" style={{ marginTop: '0.75rem' }}>
            <AlertTriangle size={15} /> Downloads will contain free text written during
            meetings. Handle them like any other resident record.
          </div>
        )}
      </div>

      <div className="card settings-card" style={{ marginTop: '1rem' }}>
        <div className="settings-section-title">Exports</div>
        {EXPORTS.map((entry) => (
          <div className="settings-row" key={entry.key}>
            <div>
              <div className="settings-row__label">{entry.label}</div>
              <div className="settings-row__desc">{entry.desc}</div>
            </div>
            <div className="settings-row__control">
              <button
                className="btn btn--primary"
                onClick={() => download(entry.key)}
                disabled={busy === entry.key}
              >
                <Download size={15} /> {busy === entry.key ? 'Preparing…' : 'Download CSV'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
