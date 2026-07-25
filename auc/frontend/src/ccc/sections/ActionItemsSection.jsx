/**
 * Section A — open action items from prior meetings.
 *
 * This is the recall experiment. Two one-tap buttons per item record whether the room
 * remembered the item on its own, and whether the operator had to surface it. Both can
 * be true; the backend ORs the flags onto a single check row per item per session, so
 * tapping one then the other records both rather than overwriting.
 *
 * Renders nothing when there are no open items, so the drawer starts with "The room" in
 * the common case.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Check, Ear, Search } from 'lucide-react';
import { getCccActionItems } from '../../api';
import { ITEM_STATUSES } from '../cccConstants';
import { Section } from '../CccFields';
import * as queue from '../cccQueue';

export default function ActionItemsSection({ residentId, sessionId }) {
  const [items, setItems] = useState([]);

  useEffect(() => {
    let cancelled = false;
    getCccActionItems(residentId, { status: 'open', sessionId })
      .then((rows) => { if (!cancelled) setItems(rows); })
      .catch(() => { if (!cancelled) setItems([]); });
    return () => { cancelled = true; };
  }, [residentId, sessionId]);

  // Optimistic: the tap has to feel instant mid-meeting, and the write is durable in
  // the outbox even if the backend is unreachable.
  const check = useCallback((item, flags) => {
    setItems((prev) => prev.map((row) => (row.id === item.id ? {
      ...row,
      recalled_by_room: flags.recalled_by_room ? 1 : (row.recalled_by_room || 0),
      surfaced_by_auc: flags.surfaced_by_auc ? 1 : (row.surfaced_by_auc || 0),
      checked_at: row.checked_at || new Date().toISOString(),
    } : row)));
    queue.enqueuePost('/ccc/action-item-checks', {
      action_item_id: item.id,
      session_id: sessionId,
      recalled_by_room: Boolean(flags.recalled_by_room),
      surfaced_by_auc: Boolean(flags.surfaced_by_auc),
    });
  }, [sessionId]);

  const setStatus = useCallback((item, status) => {
    setItems((prev) => prev.map((row) => (
      row.id === item.id ? { ...row, status } : row
    )));
    queue.enqueuePatch(`/ccc/action-items/${item.id}`, { status });
  }, []);

  if (!items.length) return null;

  return (
    <Section title="Open action items" hint="From previous meetings">
      <div className="ccc-items">
        {items.map((item) => {
          const checked = Boolean(item.recalled_by_room || item.surfaced_by_auc);
          const resolved = item.status && item.status !== 'open';
          return (
            <div
              key={item.id}
              className={`ccc-item${checked ? ' is-checked' : ''}${resolved ? ' is-resolved' : ''}`}
            >
              <div className="ccc-item__text">
                {checked && <Check size={13} className="ccc-item__tick" />}
                <span>{item.item_text || '(no text)'}</span>
                {item.owner && <span className="ccc-item__owner">{item.owner}</span>}
              </div>
              <div className="ccc-item__actions">
                <button
                  type="button"
                  className={`ccc-mini${item.recalled_by_room ? ' is-active' : ''}`}
                  onClick={() => check(item, { recalled_by_room: true })}
                >
                  <Ear size={12} /> Room remembered
                </button>
                <button
                  type="button"
                  className={`ccc-mini${item.surfaced_by_auc ? ' is-active' : ''}`}
                  onClick={() => check(item, { surfaced_by_auc: true })}
                >
                  <Search size={12} /> I surfaced it
                </button>
              </div>
              <div className="ccc-item__actions">
                {ITEM_STATUSES.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className={`ccc-mini ccc-mini--quiet${item.status === opt.value ? ' is-active' : ''}`}
                    onClick={() => setStatus(item, item.status === opt.value ? 'open' : opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
}
