/**
 * Section C — what the operator contributed, and what it changed.
 *
 * Each card is created the moment "+ Log contribution" is tapped, with a client-side id,
 * so the retrieval clock is read at that instant and every later tap is a PATCH against
 * a row that already exists. That is why the card can be filled in out of order (type
 * now, outcome three minutes later) with nothing to submit.
 *
 * Saved cards collapse to one line to keep the drawer scannable; tapping one reopens it.
 * A mis-tapped card is voided rather than deleted, so the study keeps the audit trail.
 */

import React, { useCallback, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { CONTRIBUTION_TYPES, OUTCOMES, TODO_STATUSES, labelFor } from '../cccConstants';
import { AutoText, ChoiceRow, Field, Section } from '../CccFields';
import * as queue from '../cccQueue';
import * as clock from '../cccClock';

function summarise(card) {
  const parts = [];
  if (card.contribution_type) parts.push(labelFor(CONTRIBUTION_TYPES, card.contribution_type));
  if (card.todo_status) parts.push(labelFor(TODO_STATUSES, card.todo_status));
  if (card.detail) parts.push(card.detail);
  if (card.outcome) parts.push(`→ ${labelFor(OUTCOMES, card.outcome)}`);
  return parts.length ? parts.join(' · ') : 'Empty contribution';
}

export default function ContributionsSection({
  residentId, logId, contributions, onContributionsChange,
}) {
  const [expandedId, setExpandedId] = useState(null);

  const add = useCallback(() => {
    if (!logId) return;
    // Read the clock at the moment of logging, then restart it, so the next
    // contribution is timed from this one.
    const retrievalSeconds = clock.elapsedAndRestart(residentId);
    const id = queue.enqueuePost('/ccc/contributions', {
      resident_log_id: logId,
      retrieval_seconds: retrievalSeconds,
    });
    const card = {
      id,
      resident_log_id: logId,
      retrieval_seconds: retrievalSeconds,
      contribution_type: null,
      todo_status: null,
      detail: '',
      outcome: null,
    };
    onContributionsChange([...contributions, card]);
    setExpandedId(id);
  }, [logId, residentId, contributions, onContributionsChange]);

  const patch = useCallback((id, changes) => {
    onContributionsChange(
      contributions.map((card) => (card.id === id ? { ...card, ...changes } : card))
    );
    queue.enqueuePatch(`/ccc/contributions/${id}`, changes);
  }, [contributions, onContributionsChange]);

  const discard = useCallback((id) => {
    onContributionsChange(contributions.filter((card) => card.id !== id));
    queue.enqueuePatch(`/ccc/contributions/${id}`, { voided: true });
    setExpandedId((prev) => (prev === id ? null : prev));
  }, [contributions, onContributionsChange]);

  return (
    <Section title="My contributions">
      <div className="ccc-cards">
        {contributions.map((card) => {
          const expanded = expandedId === card.id;
          if (!expanded) {
            return (
              <button
                key={card.id}
                type="button"
                className="ccc-card ccc-card--collapsed"
                onClick={() => setExpandedId(card.id)}
              >
                {summarise(card)}
              </button>
            );
          }
          return (
            <div key={card.id} className="ccc-card">
              <Field label="Type">
                <ChoiceRow
                  options={CONTRIBUTION_TYPES}
                  value={card.contribution_type ?? null}
                  onChange={(v) => patch(card.id, {
                    contribution_type: v,
                    // todo_status is only meaningful for a surfaced prior to-do; the
                    // backend enforces this too, but clearing it here keeps the UI honest.
                    ...(v === 'todo_surfaced' ? {} : { todo_status: null }),
                  })}
                />
              </Field>

              {card.contribution_type === 'todo_surfaced' && (
                <Field label="To-do status">
                  <ChoiceRow
                    options={TODO_STATUSES}
                    value={card.todo_status ?? null}
                    onChange={(v) => patch(card.id, { todo_status: v })}
                  />
                </Field>
              )}

              <Field label="Detail">
                <AutoText
                  value={card.detail}
                  placeholder="One line"
                  onSave={(text) => patch(card.id, { detail: text })}
                />
              </Field>

              <Field label="Outcome">
                <ChoiceRow
                  options={OUTCOMES}
                  value={card.outcome ?? null}
                  onChange={(v) => patch(card.id, { outcome: v })}
                />
              </Field>

              <div className="ccc-card__footer">
                {card.retrieval_seconds != null && (
                  <span className="ccc-card__meta">{card.retrieval_seconds}s to find</span>
                )}
                <button
                  type="button"
                  className="ccc-mini ccc-mini--danger"
                  onClick={() => discard(card.id)}
                  title="Discard this card"
                >
                  <Trash2 size={12} /> Discard
                </button>
                <button
                  type="button"
                  className="ccc-mini"
                  onClick={() => setExpandedId(null)}
                >
                  Collapse
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <button
        type="button"
        className="ccc-add"
        onClick={add}
        disabled={!logId}
      >
        <Plus size={14} /> Log contribution
      </button>
    </Section>
  );
}
