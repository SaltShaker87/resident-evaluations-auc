import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FileStack, Shuffle, Sparkles, Download, AlertTriangle, Ban, CalendarClock, Plus, X, Trash2,
} from 'lucide-react';
import {
  getAdvisors, getOllamaModels, getPacketDefaults, getPacketMeetings, openPacketMeeting,
  getPacketMeeting, setPacketCutoff, addPacketPresenters, removePacketPresenter,
  movePacketPresenter, addPacketResident, randomizePacketMeeting, setPacketGroup,
  setPacketPeriod, generatePackets, withholdPacket, downloadPacket, downloadPacketBatch,
} from '../api';

/**
 * Advisor packets for the CCC study.
 *
 * Pick a meeting, the advisors presenting at it, and each advisee's study group
 * (by hand, or once with "Randomize within each advisor"). Generate builds one packet
 * per resident in the background — reloading the page does not stop it — and the
 * downloads give one zip per advisor or one for the whole meeting. Nothing is sent to
 * anyone: the operator hands the files out. See CCC.md, "Advisor packets".
 */

const GROUPS = [
  { value: 'packet_first', label: 'Packet first' },
  { value: 'packet_delayed', label: 'Packet delayed' },
  { value: 'none', label: 'No packet' },
];
const GROUP_LABEL = Object.fromEntries(GROUPS.map((g) => [g.value, g.label]));

const STATUS_LABEL = {
  idle: 'Not generated',
  queued: 'Waiting',
  running: 'Generating',
  done: 'Ready',
  failed: 'Failed',
};

// Same wording as review_period.BASIS_DESCRIPTIONS on the server.
const BASIS_LABEL = {
  override: 'Dates set by hand',
  previous_packet: "Starts the day after the previous packet's cutoff",
  last_review: 'Starts the day after the last committee review',
  academic_year_start: 'Starts at the beginning of the academic year',
};

function formatDay(iso) {
  if (!iso) return '';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function oneLine(question) {
  const answer = window.prompt(question);
  if (answer === null) return null;
  return answer.replace(/\s+/g, ' ').trim();
}

// ---- One resident ----

function PeriodCell({ row, onSave, busy }) {
  const shown = row.packet || row.preview || {};
  const [editing, setEditing] = useState(false);
  const [start, setStart] = useState(row.override_start || '');
  const [end, setEnd] = useState(row.override_end || '');

  if (row.preview?.error && !row.packet) {
    return <div className="packet-cell__error">{row.preview.error}</div>;
  }
  if (editing) {
    return (
      <form
        className="packet-period-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await onSave(start, end)) setEditing(false);
        }}
      >
        <label>From <input type="date" className="form-input" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>To <input type="date" className="form-input" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <div className="flex gap-sm">
          <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>Save</button>
          {(row.override_start || row.override_end) && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={async () => { if (await onSave('', '')) setEditing(false); }}
            >
              Use the usual dates
            </button>
          )}
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </form>
    );
  }
  const basis = row.packet
    ? BASIS_LABEL[row.packet.period_basis] || ''
    : row.preview?.basis_description;
  return (
    <div>
      <div className="packet-cell__main">
        {formatDay(shown.period_start)} to {formatDay(shown.period_end)}
      </div>
      {basis && <div className="packet-cell__sub">{basis}</div>}
      {!row.packet && (
        <button
          className="btn btn--ghost btn--sm packet-cell__link"
          onClick={() => setEditing(true)}
          disabled={busy || ['queued', 'running'].includes(row.status)}
        >
          <CalendarClock size={12} /> Change dates
        </button>
      )}
    </div>
  );
}

function EvidenceCell({ row, threshold }) {
  const counts = row.packet
    ? {
      n_comments: row.packet.n_comments,
      n_evaluators: row.packet.n_evaluators,
      n_rotations: row.packet.n_rotations,
      n_late: row.packet.n_late,
    }
    : row.preview?.counts;
  if (!counts) return null;
  const thin = counts.n_comments < threshold;
  return (
    <div>
      <div className="packet-cell__main">{plural(counts.n_comments, 'comment')}</div>
      <div className="packet-cell__sub">
        {plural(counts.n_evaluators, 'evaluator')}, {plural(counts.n_rotations, 'rotation')}
        {counts.n_late ? `, ${counts.n_late} evaluated late` : ''}
      </div>
      {thin && <span className="packet-tag packet-tag--warn">Few evaluations</span>}
    </div>
  );
}

function GroupCell({ row, onChange, busy }) {
  const randomized = Boolean(row.randomized_at);
  return (
    <div>
      <select
        className="form-select packet-group"
        value={row.study_group || ''}
        disabled={busy || !row.resident_id}
        onChange={(e) => onChange(e.target.value || null)}
        aria-label="Study group"
      >
        {!randomized && <option value="">Not set</option>}
        {GROUPS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
      </select>
      <div className="packet-cell__sub">
        {row.randomized_group ? 'Randomized' : row.study_group ? 'Set by hand' : ''}
        {row.added_late ? ' · added late' : ''}
      </div>
      {row.group_change_reason && (
        <div className="packet-cell__sub">
          Changed from {GROUP_LABEL[row.randomized_group] || 'its first group'}: {row.group_change_reason}
        </div>
      )}
    </div>
  );
}

function PacketCell({ row, onGenerate, onWithhold, onDownload, busy, cutoff }) {
  const p = row.packet;
  const working = ['queued', 'running'].includes(row.status);
  return (
    <div>
      <div className={`packet-status packet-status--${row.status}`}>
        {working && <div className="spinner spinner--sm" />}
        {STATUS_LABEL[row.status] || row.status}
        {row.status === 'running' && row.status_detail ? ` · ${row.status_detail}` : ''}
      </div>
      {row.status === 'failed' && row.status_detail && (
        <div className="packet-cell__error">{row.status_detail}</div>
      )}
      {p && (
        <div className="packet-cell__sub">
          Packet {p.id}
          {p.cutoff_date !== cutoff ? ` · built for cutoff ${formatDay(p.cutoff_date)}` : ''}
          {p.first_downloaded_at ? ' · downloaded' : ''}
        </div>
      )}
      {p?.withheld ? (
        <div className="packet-cell__sub packet-cell__withheld">
          <Ban size={12} /> Withheld: {p.withheld_reason}
        </div>
      ) : null}
      <div className="packet-actions">
        {p && (
          <button className="btn btn--secondary btn--sm" onClick={onDownload} disabled={busy}>
            <Download size={12} /> PDF
          </button>
        )}
        {p && !p.withheld && (
          <button className="btn btn--ghost btn--sm" onClick={onWithhold} disabled={busy}>
            <Ban size={12} /> Withhold
          </button>
        )}
        {!working && row.resident_id && (
          <button className="btn btn--ghost btn--sm" onClick={onGenerate} disabled={busy}>
            <Sparkles size={12} /> {p || row.status === 'failed' ? 'Generate again' : 'Generate'}
          </button>
        )}
      </div>
    </div>
  );
}

// ---- One presenting advisor ----

function AdvisorBlock({ meeting, presenter, threshold, busy, act, showToast }) {
  const [moving, setMoving] = useState(false);
  const [moveDate, setMoveDate] = useState('');
  const rows = presenter.residents;
  const ready = rows.filter((r) => r.packet && !r.packet.withheld).length;
  const canRemove = rows.every((r) => !r.randomized_at && !r.packet_id
                                      && !['queued', 'running'].includes(r.status));

  return (
    <section className="card packet-advisor">
      <div className="packet-advisor__head">
        <div className="packet-advisor__title">
          <h2>{presenter.advisor_name || 'Advisor removed from AUC'}</h2>
          <span className="packet-advisor__code">{presenter.advisor_code}</span>
          {!presenter.in_study && <span className="packet-tag">Outside study</span>}
          {presenter.randomized_at && presenter.randomization_seed && (
            <span className="packet-tag packet-tag--ok">Randomized</span>
          )}
        </div>
        <div className="packet-advisor__actions">
          <button
            className="btn btn--secondary btn--sm"
            disabled={busy || ready === 0}
            onClick={() => act(() => downloadPacketBatch(meeting.id, presenter.advisor_id))}
          >
            <Download size={13} /> Zip of {plural(ready, 'packet')}
          </button>
          <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setMoving(!moving)}>
            <CalendarClock size={13} /> Move to another date
          </button>
          {canRemove && (
            <button
              className="btn btn--ghost btn--sm"
              disabled={busy}
              title="Remove from this meeting"
              onClick={() => act(() => removePacketPresenter(meeting.id, presenter.advisor_id))}
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>

      {moving && (
        <form
          className="packet-move"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!moveDate) return;
            const moved = await act(() => movePacketPresenter(meeting.id, presenter.advisor_id, moveDate));
            if (moved) {
              showToast?.(`Moved to the meeting on ${formatDay(moveDate)}. Groups are unchanged.`);
              setMoving(false);
            }
          }}
        >
          <span>
            Move this advisor&rsquo;s presentation to the meeting on
          </span>
          <input type="date" className="form-input" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} />
          <button type="submit" className="btn btn--primary btn--sm" disabled={!moveDate || busy}>Move</button>
          <span className="packet-cell__sub">
            Every advisee keeps their group. Packets must be generated again for the new cutoff.
          </span>
        </form>
      )}

      {rows.length === 0 && (
        <div className="text-sm text-muted">This advisor has no active advisees.</div>
      )}

      {rows.length > 0 && (
        <div className="packet-table" role="table">
          <div className="packet-row packet-row--head" role="row">
            <div role="columnheader">Resident</div>
            <div role="columnheader">Review period</div>
            <div role="columnheader">Evidence</div>
            <div role="columnheader">Group</div>
            <div role="columnheader">Packet</div>
          </div>
          {rows.map((row) => (
            <div className="packet-row" role="row" key={row.id}>
              <div role="cell" data-label="Resident">
                <div className="packet-cell__main">
                  {row.first_name ? `${row.first_name} ${row.last_name}` : 'Removed from AUC'}
                </div>
                <div className="packet-cell__sub">
                  {row.packet?.pgy_year_at_cutoff || row.preview?.pgy_year_at_cutoff
                    ? `PGY-${row.packet?.pgy_year_at_cutoff || row.preview?.pgy_year_at_cutoff} on the cutoff date`
                    : ''}
                  {row.study_code ? ` · ${row.study_code}` : ''}
                </div>
                {row.preview?.pgy_note && <div className="packet-cell__sub">{row.preview.pgy_note}</div>}
              </div>
              <div role="cell" data-label="Review period">
                <PeriodCell
                  row={row}
                  busy={busy}
                  onSave={(start, end) => act(() => setPacketPeriod(row.id, start, end))}
                />
              </div>
              <div role="cell" data-label="Evidence">
                <EvidenceCell row={row} threshold={threshold} />
              </div>
              <div role="cell" data-label="Group">
                <GroupCell
                  row={row}
                  busy={busy}
                  onChange={(group) => {
                    let reason = null;
                    if (row.randomized_at) {
                      reason = oneLine(
                        'This group was set by randomization. In one line, why is it changing? '
                        + '(The original group is kept on record.)',
                      );
                      if (!reason) return;
                    }
                    act(() => setPacketGroup(row.id, group, reason));
                  }}
                />
              </div>
              <div role="cell" data-label="Packet">
                <PacketCell
                  row={row}
                  busy={busy}
                  cutoff={meeting.cutoff_date}
                  onGenerate={() => act(() => generatePackets(meeting.id, [row.id], localStorage.getItem('packetModel') || null))}
                  onDownload={() => act(() => downloadPacket(row.packet.id))}
                  onWithhold={() => {
                    const reason = oneLine('In one line, why is this packet withheld? It will be left out of every zip.');
                    if (reason) act(() => withholdPacket(row.packet.id, reason));
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {presenter.advisees_not_in_meeting.length > 0 && (
        <div className="packet-missing">
          <span>Advisees not in this meeting:</span>
          {presenter.advisees_not_in_meeting.map((r) => (
            <button
              key={r.id}
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => act(() => addPacketResident(meeting.id, r.id))}
              title={meeting.randomized_at ? 'Added after randomization: their group is a coin flip' : 'Add to this meeting'}
            >
              <Plus size={12} /> {r.first_name} {r.last_name}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// ---- The page ----

export default function Packets({ showToast }) {
  const [meetings, setMeetings] = useState([]);
  const [meetingId, setMeetingId] = useState(() => {
    try { return localStorage.getItem('packetMeeting') || ''; } catch { return ''; }
  });
  const [state, setState] = useState(null);
  const [advisors, setAdvisors] = useState([]);
  const [newDate, setNewDate] = useState('');
  const [newCutoff, setNewCutoff] = useState('');
  const [cutoffEdit, setCutoffEdit] = useState('');
  const [toAdd, setToAdd] = useState([]);
  const [models, setModels] = useState([]);
  const [model, setModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const loadMeetings = useCallback(() => getPacketMeetings().then(setMeetings).catch(() => {}), []);

  useEffect(() => {
    loadMeetings();
    getAdvisors().then(setAdvisors).catch(() => {});
    getOllamaModels().then((result) => {
      if (Array.isArray(result) && result.length) {
        setModels(result);
        const saved = localStorage.getItem('packetModel') || localStorage.getItem('defaultOllamaModel');
        setModel(saved && result.includes(saved) ? saved : result[0]);
      }
    }).catch(() => {});
  }, [loadMeetings]);

  useEffect(() => {
    if (model) localStorage.setItem('packetModel', model);
  }, [model]);

  const loadMeeting = useCallback(async (id) => {
    if (!id) { setState(null); return; }
    try {
      const result = await getPacketMeeting(id);
      setState(result);
      setCutoffEdit(result.meeting.cutoff_date);
    } catch (err) {
      setState(null);
      setError(err.message);
      localStorage.removeItem('packetMeeting');
    }
  }, []);

  useEffect(() => {
    loadMeeting(meetingId);
    if (meetingId) localStorage.setItem('packetMeeting', meetingId);
  }, [meetingId, loadMeeting]);

  // While anything is generating, keep the page current. The work happens on the
  // server, so leaving or reloading the page does not stop it.
  const working = Boolean(state && (state.status_counts.queued || state.status_counts.running));
  useEffect(() => {
    if (!working) return undefined;
    const timer = setInterval(() => loadMeeting(meetingId), 2000);
    return () => clearInterval(timer);
  }, [working, meetingId, loadMeeting]);

  useEffect(() => {
    if (!newDate) { setNewCutoff(''); return; }
    getPacketDefaults(newDate).then((d) => setNewCutoff(d.cutoff_date || '')).catch(() => {});
  }, [newDate]);

  // Runs an action; most return the new meeting state, the rest (downloads,
  // generate) are followed by a fresh read. Returns true on success.
  const act = useCallback(async (action) => {
    setBusy(true);
    setError('');
    try {
      const result = await action();
      if (result?.meeting) {
        setState(result);
        setCutoffEdit(result.meeting.cutoff_date);
        if (result.meeting.id !== meetingId) setMeetingId(result.meeting.id);
      } else {
        await loadMeeting(meetingId);
      }
      loadMeetings();
      return true;
    } catch (err) {
      setError(err.message || 'Something went wrong.');
      return false;
    } finally {
      setBusy(false);
    }
  }, [meetingId, loadMeeting, loadMeetings]);

  const meeting = state?.meeting;
  const presentingIds = useMemo(
    () => new Set((state?.presenters || []).map((p) => p.advisor_id)),
    [state],
  );
  const available = advisors.filter((a) => a.active && !presentingIds.has(a.id));
  const allRows = (state?.presenters || []).flatMap((p) => p.residents);
  const doneCount = allRows.filter((r) => r.status === 'done').length;
  const readyCount = allRows.filter((r) => r.packet && !r.packet.withheld).length;
  const threshold = state?.settings?.thin_evidence ?? 5;

  return (
    <div>
      <div className="page-header">
        <h1><FileStack size={20} /> Advisor Packets</h1>
        <p className="text-muted">
          AI drafts of each advisee&rsquo;s MedHub comments for one review period, as PDFs for the
          presenting advisors. Packets are unedited drafts and do not need approval. Nothing
          is sent to anyone: download the files and hand them out.
        </p>
      </div>

      {error && (
        <div className="alert alert--danger packet-error">
          <AlertTriangle size={15} /> <span>{error}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setError('')} aria-label="Dismiss"><X size={13} /></button>
        </div>
      )}

      <div className="card settings-card">
        <div className="settings-section-title">Meeting</div>
        <div className="packet-meeting-pick">
          {meetings.length > 0 && (
            <label className="period-picker__field">
              <span>Open a meeting</span>
              <select className="form-select" value={meetingId} onChange={(e) => setMeetingId(e.target.value)}>
                <option value="">Choose…</option>
                {meetings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {formatDay(m.meeting_date)} · {plural(m.residents, 'resident')}
                  </option>
                ))}
              </select>
            </label>
          )}
          <form
            className="packet-meeting-new"
            onSubmit={(e) => {
              e.preventDefault();
              if (newDate) act(() => openPacketMeeting(newDate, newCutoff || null)).then((ok) => ok && setNewDate(''));
            }}
          >
            <label className="period-picker__field">
              <span>New meeting on</span>
              <input type="date" className="form-input" value={newDate} onChange={(e) => setNewDate(e.target.value)} />
            </label>
            <label className="period-picker__field">
              <span>Cutoff date</span>
              <input type="date" className="form-input" value={newCutoff} onChange={(e) => setNewCutoff(e.target.value)} disabled={!newDate} />
            </label>
            <button type="submit" className="btn btn--primary" disabled={!newDate || busy}>
              <Plus size={14} /> Open
            </button>
          </form>
        </div>

        {meeting && (
          <div className="packet-meeting-info">
            <div>
              <div className="settings-row__label">Meeting on {formatDay(meeting.meeting_date)}</div>
              <div className="settings-row__desc">
                Each packet covers its resident&rsquo;s review period up to the cutoff date, normally
                {` ${state.settings.lead_days}`} days before the meeting.
              </div>
            </div>
            <form
              className="packet-cutoff"
              onSubmit={(e) => {
                e.preventDefault();
                if (cutoffEdit && cutoffEdit !== meeting.cutoff_date) {
                  act(() => setPacketCutoff(meeting.id, cutoffEdit));
                }
              }}
            >
              <label className="period-picker__field">
                <span>Cutoff date</span>
                <input type="date" className="form-input" value={cutoffEdit} onChange={(e) => setCutoffEdit(e.target.value)} />
              </label>
              {cutoffEdit !== meeting.cutoff_date && (
                <button type="submit" className="btn btn--secondary btn--sm" disabled={busy}>Save</button>
              )}
            </form>
          </div>
        )}
      </div>

      {meeting && state.staleness_warning && (
        <div className="alert alert--warning packet-error">
          <AlertTriangle size={15} /> <span>{state.staleness_warning}</span>
        </div>
      )}

      {meeting && (
        <div className="card settings-card packet-presenters">
          <div className="settings-section-title">Presenting advisors</div>
          {available.length === 0 ? (
            <div className="text-sm text-muted">
              {advisors.length === 0
                ? 'No advisors yet. Add them in Settings, under Advisors.'
                : 'Every active advisor is already presenting at this meeting.'}
            </div>
          ) : (
            <div className="packet-presenters__pick">
              {available.map((a) => (
                <label key={a.id} className="packet-check">
                  <input
                    type="checkbox"
                    checked={toAdd.includes(a.id)}
                    onChange={(e) => setToAdd(e.target.checked ? [...toAdd, a.id] : toAdd.filter((x) => x !== a.id))}
                  />
                  <span>{a.name}{a.in_study ? '' : ' (outside study)'}</span>
                </label>
              ))}
              <button
                className="btn btn--primary btn--sm"
                disabled={toAdd.length === 0 || busy}
                onClick={() => act(() => addPacketPresenters(meeting.id, toAdd)).then((ok) => ok && setToAdd([]))}
              >
                <Plus size={13} /> Add {toAdd.length > 1 ? `${toAdd.length} advisors` : 'advisor'}
              </button>
            </div>
          )}
          {meeting.randomized_at && (
            <div className="settings-row__desc" style={{ marginTop: '0.5rem' }}>
              This meeting has been randomized. An advisor added now gets their own even split.
            </div>
          )}
        </div>
      )}

      {meeting && state.presenters.length > 0 && (
        <div className="card settings-card packet-toolbar">
          <div className="packet-toolbar__group">
            <button
              className="btn btn--secondary"
              disabled={busy || Boolean(meeting.randomized_at)}
              onClick={() => {
                if (window.confirm(
                  'Randomize within each advisor? Each advisor\'s advisees are split evenly '
                  + 'between Packet first and Packet delayed. This can be done once only for '
                  + 'this meeting, and cannot be undone.',
                )) act(() => randomizePacketMeeting(meeting.id));
              }}
            >
              <Shuffle size={14} /> {meeting.randomized_at ? 'Randomized' : 'Randomize within each advisor'}
            </button>
            <div className="packet-toolbar__note">
              {meeting.randomized_at
                ? `Done ${formatDay(meeting.randomized_at)}. It cannot be run again; change a group by hand, with a reason.`
                : 'Optional. Once only. Or set every group by hand.'}
            </div>
          </div>
          <div className="packet-toolbar__group">
            {models.length > 0 && (
              <select className="form-select packet-model" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            )}
            <button
              className="btn btn--primary"
              disabled={busy || working}
              onClick={() => act(() => generatePackets(meeting.id, null, model || null))}
            >
              <Sparkles size={14} /> Generate all
            </button>
            <button
              className="btn btn--secondary"
              disabled={busy || readyCount === 0}
              onClick={() => act(() => downloadPacketBatch(meeting.id))}
            >
              <Download size={14} /> Zip of the whole meeting
            </button>
          </div>
          {(working || doneCount > 0) && (
            <div className="packet-progress">
              <div className="summary-progress__bar">
                <div className="summary-progress__fill" style={{ width: `${(doneCount / Math.max(allRows.length, 1)) * 100}%` }} />
              </div>
              <span>
                {doneCount} of {allRows.length} ready
                {state.status_counts.failed ? ` · ${state.status_counts.failed} failed` : ''}
                {working ? ' · generating one at a time; you can leave this page' : ''}
              </span>
            </div>
          )}
        </div>
      )}

      {meeting && state.presenters.map((presenter) => (
        <AdvisorBlock
          key={presenter.id}
          meeting={meeting}
          presenter={presenter}
          threshold={threshold}
          busy={busy}
          act={act}
          showToast={showToast}
        />
      ))}
    </div>
  );
}
