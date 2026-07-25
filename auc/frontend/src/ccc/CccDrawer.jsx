/**
 * AUC — CCC drawer.
 *
 * A fixed slide-over on the right. It overlays the page: nothing behind it reflows or
 * resizes, and it is unmounted (not translated off-screen) when closed, so it can never
 * contribute off-viewport overflow or a stray horizontal scrollbar.
 *
 * Field values are held here and mirrored to localStorage per log, which is what makes
 * "if the backend is unreachable, never lose what I typed" true even across a reload:
 * the local mirror renders, the outbox persists the writes, and the server row is only
 * used to seed the initial state.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { useCcc } from './CccContext';
import * as queue from './cccQueue';
import ActionItemsSection from './sections/ActionItemsSection';
import RoomSection from './sections/RoomSection';
import ContributionsSection from './sections/ContributionsSection';
import CloseOutSection from './sections/CloseOutSection';

const LOG_FIELDS = [
  'room_input_level', 'roles_spoke', 'referenced_written_eval', 'room_raised_notes',
  'group_read_shifted', 'pushback', 'pushback_note', 'closing_notes',
];

const mirrorKey = (logId) => `auc:ccc:log:${logId}`;

function toBool(v) {
  if (v === null || v === undefined || v === '') return null;
  return Boolean(v);
}

/** Server row (0/1 ints, JSON string) -> the shapes the form controls want. */
function fromRow(row) {
  if (!row) return {};
  let roles = [];
  if (Array.isArray(row.roles_spoke)) roles = row.roles_spoke;
  else if (typeof row.roles_spoke === 'string' && row.roles_spoke) {
    try { roles = JSON.parse(row.roles_spoke); } catch { roles = []; }
  }
  return {
    room_input_level: row.room_input_level ?? null,
    roles_spoke: roles,
    referenced_written_eval: toBool(row.referenced_written_eval),
    room_raised_notes: row.room_raised_notes ?? '',
    group_read_shifted: toBool(row.group_read_shifted),
    pushback: toBool(row.pushback),
    pushback_note: row.pushback_note ?? '',
    closing_notes: row.closing_notes ?? '',
  };
}

function readMirror(logId) {
  try {
    const raw = localStorage.getItem(mirrorKey(logId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export default function CccDrawer({
  session, residentId, log, contributions, onContributionsChange, onDone, onClose,
}) {
  const logId = log?.id || null;
  const { queue: queueStatus } = useCcc();
  const [values, setValues] = useState({});

  // Seed from the server row, then let anything already mirrored locally win — those are
  // edits that may not have reached the backend yet.
  useEffect(() => {
    if (!logId) {
      setValues({});
      return;
    }
    setValues({ ...fromRow(log), ...(readMirror(logId) || {}) });
  }, [logId, log]);

  // No debounce here: AutoText already settles before it calls back, and every other
  // control is a single tap that should hit the outbox immediately.
  const patch = useCallback((changes) => {
    setValues((prev) => {
      const next = { ...prev, ...changes };
      if (logId) {
        try {
          localStorage.setItem(
            mirrorKey(logId),
            JSON.stringify(Object.fromEntries(
              LOG_FIELDS.filter((f) => f in next).map((f) => [f, next[f]])
            )),
          );
        } catch { /* mirror is best-effort */ }
      }
      return next;
    });
    if (logId) {
      queue.enqueuePatch(`/ccc/resident-logs/${logId}`, changes);
    }
  }, [logId]);

  const pending = queueStatus.pending;

  return (
    <aside className="ccc-drawer" aria-label="CCC meeting log">
      <header className="ccc-drawer__header">
        <div>
          <div className="ccc-drawer__title">CCC log</div>
          {session.cycle_label && (
            <div className="ccc-drawer__sub">{session.cycle_label}</div>
          )}
        </div>
        <button
          type="button"
          className="ccc-drawer__close"
          onClick={onClose}
          title="Close (Ctrl+Shift+L)"
        >
          <X size={16} />
        </button>
      </header>

      {log?.offline && (
        <div className="ccc-drawer__offline">
          Backend unreachable — {pending} change{pending === 1 ? '' : 's'} held and retrying.
          Keep going, nothing is lost.
        </div>
      )}

      <ActionItemsSection residentId={residentId} sessionId={session.id} />

      <RoomSection values={values} patch={patch} />

      <ContributionsSection
        residentId={residentId}
        logId={logId}
        contributions={contributions}
        onContributionsChange={onContributionsChange}
      />

      <CloseOutSection
        residentId={residentId}
        sessionId={session.id}
        values={values}
        patch={patch}
        onDone={onDone}
      />
    </aside>
  );
}
