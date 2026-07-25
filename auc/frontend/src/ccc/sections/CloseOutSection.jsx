/**
 * Section D — close out this resident.
 *
 * Action items agreed in the room are created here; next cycle they come back in
 * Section A as the recall test. Each row is created on first keystroke with a client-side
 * id, so text and owner both autosave against an existing row.
 */

import React, { useCallback, useRef, useState } from 'react';
import { CheckCircle2, Plus } from 'lucide-react';
import { AutoText, Field, Section, YesNo } from '../CccFields';
import * as queue from '../cccQueue';

function NewItemRows({ residentId, sessionId }) {
  const [rows, setRows] = useState([]);
  // Which rows have been created server-side. A ref, not state, because the decision has
  // to be made outside the state updater — React may call an updater twice, and a write
  // fired from inside one would be enqueued twice.
  const createdRef = useRef(new Set());
  const valuesRef = useRef(new Map());

  const add = useCallback(() => {
    const id = queue.newId();
    valuesRef.current.set(id, { text: '', owner: '' });
    setRows((prev) => [...prev, { id, text: '', owner: '' }]);
  }, []);

  const save = useCallback((rowId, changes) => {
    const current = valuesRef.current.get(rowId) || { text: '', owner: '' };
    const next = { ...current, ...changes };
    valuesRef.current.set(rowId, next);
    setRows((prev) => prev.map((row) => (row.id === rowId ? { ...row, ...changes } : row)));

    // Nothing worth saving yet — an empty row is not an action item.
    if (!next.text.trim() && !next.owner.trim()) return;

    if (!createdRef.current.has(rowId)) {
      createdRef.current.add(rowId);
      // Create with the id we already handed out, so a double save cannot make two items.
      queue.enqueuePost('/ccc/action-items', {
        resident_id: residentId,
        session_id: sessionId,
        text: next.text,
        owner: next.owner,
      }, { id: rowId });
    } else {
      queue.enqueuePatch(`/ccc/action-items/${rowId}`, changes);
    }
  }, [residentId, sessionId]);

  return (
    <>
      {rows.map((row) => (
        <div key={row.id} className="ccc-itemrow">
          <AutoText
            value={row.text}
            placeholder="Action item"
            onSave={(text) => save(row.id, { text })}
          />
          <AutoText
            value={row.owner}
            placeholder="Owner"
            onSave={(owner) => save(row.id, { owner })}
          />
        </div>
      ))}
      <button type="button" className="ccc-add ccc-add--quiet" onClick={add}>
        <Plus size={14} /> Action item
      </button>
    </>
  );
}

export default function CloseOutSection({ residentId, sessionId, values, patch, onDone }) {
  return (
    <Section title="Close out">
      <Field label="Action items agreed">
        <NewItemRows residentId={residentId} sessionId={sessionId} />
      </Field>

      <Field label="Did the group's read shift?">
        <YesNo
          value={values.group_read_shifted ?? null}
          onChange={(v) => patch({ group_read_shifted: v })}
        />
      </Field>

      <Field label="Did the room push back on anything I raised?">
        <YesNo
          value={values.pushback ?? null}
          onChange={(v) => patch({ pushback: v })}
        />
      </Field>

      {values.pushback === true && (
        <Field>
          <AutoText
            value={values.pushback_note}
            placeholder="What they pushed back on"
            onSave={(text) => patch({ pushback_note: text })}
          />
        </Field>
      )}

      <Field label="Notes">
        <AutoText
          multiline
          rows={2}
          value={values.closing_notes}
          placeholder="Anything else worth keeping"
          onSave={(text) => patch({ closing_notes: text })}
        />
      </Field>

      <button type="button" className="ccc-done" onClick={onDone}>
        <CheckCircle2 size={15} /> Done with this resident
      </button>
    </Section>
  );
}
