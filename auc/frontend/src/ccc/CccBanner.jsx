/**
 * AUC — CCC session banner.
 *
 * A thin bar above the app header, present only while a meeting is running. Renders
 * null otherwise, which is what keeps the rest of the app pixel-identical when the
 * feature is idle: with no banner element, the `.ccc-banner ~ .app-header` rule that
 * pushes the header down cannot match.
 *
 * It also publishes its own height as --ccc-banner-offset on the root element (the
 * same idiom App.jsx uses for data-theme) so the drawer can sit below it.
 */

import React, { useEffect } from 'react';
import { Radio, CircleStop } from 'lucide-react';
import { useCcc } from './CccContext';

const BANNER_HEIGHT = '34px';

function formatDate(iso) {
  if (!iso) return '';
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export default function CccBanner() {
  const { session, endSession, queue } = useCcc();
  const active = Boolean(session);

  useEffect(() => {
    const root = document.documentElement;
    if (!active) {
      root.style.removeProperty('--ccc-banner-offset');
      return undefined;
    }
    root.style.setProperty('--ccc-banner-offset', BANNER_HEIGHT);
    return () => root.style.removeProperty('--ccc-banner-offset');
  }, [active]);

  if (!session) return null;

  const parts = ['CCC'];
  if (session.cycle_label) parts.push(session.cycle_label);
  if (session.meeting_date) parts.push(formatDate(session.meeting_date));

  return (
    <div className="ccc-banner">
      <span className="ccc-banner__label">
        <Radio size={13} /> {parts.join(' — ')}
      </span>
      <span className="flex items-center gap-sm">
        {queue.pending > 0 && (
          <span className="ccc-banner__pending" title="Unsaved changes are being retried">
            {queue.online ? `Saving ${queue.pending}…` : `Offline — ${queue.pending} queued`}
          </span>
        )}
        <button type="button" className="ccc-banner__end" onClick={endSession}>
          <CircleStop size={13} /> End meeting
        </button>
      </span>
    </div>
  );
}
