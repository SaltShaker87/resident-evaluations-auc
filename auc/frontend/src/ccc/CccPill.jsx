/**
 * AUC — CCC drawer toggle.
 *
 * A floating pill, bottom-right of a resident page, only while a meeting is running.
 * Shows how many contributions have been logged for this resident so far.
 *
 * Sets two CSS custom properties while mounted: --ccc-pill-offset lifts the toast
 * container clear of the pill (the two are otherwise anchored to the same corner), and
 * --ccc-drawer-width steps the pill left when the drawer is open. Both are removed on
 * unmount, so nothing about the layout changes when no meeting is running.
 */

import React, { useEffect } from 'react';
import { ClipboardPen } from 'lucide-react';
import { useCcc } from './CccContext';

const DRAWER_WIDTH = '380px';
const PILL_CLEARANCE = '3.25rem';

export default function CccPill({ count, open, onToggle }) {
  const { queue } = useCcc();

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--ccc-pill-offset', PILL_CLEARANCE);
    document.body.classList.add('ccc-pill-on');
    return () => {
      root.style.removeProperty('--ccc-pill-offset');
      document.body.classList.remove('ccc-pill-on');
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (open) root.style.setProperty('--ccc-drawer-width', DRAWER_WIDTH);
    else root.style.removeProperty('--ccc-drawer-width');
    return () => root.style.removeProperty('--ccc-drawer-width');
  }, [open]);

  return (
    <button
      type="button"
      className={`ccc-pill${open ? ' ccc-pill--open' : ''}`}
      onClick={onToggle}
      title="Toggle the CCC log (Ctrl+Shift+L)"
    >
      <ClipboardPen size={15} />
      <span>{count} logged</span>
      {queue.pending > 0 && (
        <span className="ccc-pill__pending" title={`${queue.pending} write(s) waiting to save`}>
          {queue.pending}
        </span>
      )}
    </button>
  );
}
