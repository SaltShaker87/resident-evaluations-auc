/**
 * AUC — "Start CCC meeting" header control.
 *
 * Hidden while a meeting is running (the banner's "End meeting" takes over). Opening it
 * reveals an inline popover rather than a modal, so starting a meeting is two clicks and
 * never blocks the page.
 *
 * The cycle label is prefilled from the most recent session, since it changes twice a
 * year and retyping "Fall 2026" every meeting is friction for no benefit.
 */

import React, { useEffect, useRef, useState } from 'react';
import { CalendarPlus } from 'lucide-react';
import { getCccSessions } from '../api';
import { useCcc } from './CccContext';

export default function CccStartButton() {
  const { session, startSession } = useCcc();
  const [open, setOpen] = useState(false);
  const [meetingDate, setMeetingDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [cycleLabel, setCycleLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const wrapRef = useRef(null);

  // Prefill the cycle from the last meeting, once, when the popover first opens.
  useEffect(() => {
    if (!open || cycleLabel) return;
    getCccSessions()
      .then((sessions) => {
        const previous = sessions.find((s) => s.cycle_label);
        if (previous) setCycleLabel(previous.cycle_label);
      })
      .catch(() => { /* prefill is a convenience, not a requirement */ });
  }, [open, cycleLabel]);

  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (session) return null;

  const handleStart = async () => {
    if (busy) return;                 // a double-clicked Start must not open two meetings
    setBusy(true);
    setError('');
    try {
      await startSession(meetingDate || null, cycleLabel.trim() || null);
      setOpen(false);
    } catch (err) {
      setError(err.message || 'Could not start the meeting');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ccc-start" ref={wrapRef}>
      <button
        type="button"
        className="ccc-start__trigger"
        onClick={() => setOpen((prev) => !prev)}
        title="Start a CCC meeting"
      >
        <CalendarPlus size={16} /> Start CCC
      </button>

      {open && (
        <div className="ccc-start__popover">
          <label className="ccc-start__field">
            <span>Meeting date</span>
            <input
              type="date"
              value={meetingDate}
              onChange={(e) => setMeetingDate(e.target.value)}
            />
          </label>
          <label className="ccc-start__field">
            <span>Cycle</span>
            <input
              type="text"
              placeholder="Fall 2026"
              value={cycleLabel}
              onChange={(e) => setCycleLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleStart(); }}
            />
          </label>
          {error && <div className="ccc-start__error">{error}</div>}
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={handleStart}
            disabled={busy}
          >
            {busy ? 'Starting…' : 'Start meeting'}
          </button>
        </div>
      )}
    </div>
  );
}
