/**
 * AUC — CCC drawer host.
 *
 * Mounted once from App.jsx. It works out whether we are on a resident page from the
 * route rather than from props, which is why ResidentDetail.jsx needs no changes at all:
 * the pill and drawer are position:fixed, so they look exactly as if they lived on the
 * page, but no existing component is touched.
 *
 * Renders nothing unless a meeting is running. That is the hard guarantee — with no
 * session, the resident page is byte-for-byte what it was before this feature existed.
 *
 * Owns:
 *  - get-or-create of each resident's log (opened_at) as soon as the page is visited
 *  - the retrieval clock
 *  - the per-resident contribution list, so the pill can show a count
 *
 * State is cached per session+resident in a ref, because this component stays mounted for
 * the whole meeting while the route changes underneath it. Without the cache, revisiting
 * a resident would leave the previously loaded log on screen and file contributions
 * against the wrong person.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useMatch } from 'react-router-dom';
import { openCccResidentLog } from '../api';
import { useCcc } from './CccContext';
import * as queue from './cccQueue';
import * as clock from './cccClock';
import CccPill from './CccPill';
import CccDrawer from './CccDrawer';

export default function CccDrawerHost({ showToast }) {
  const { session, drawerOpen, setDrawerOpen, toggleDrawer, setCount } = useCcc();
  const match = useMatch('/residents/:id');
  const residentId = match?.params?.id || null;
  const sessionId = session?.id || null;

  const [log, setLog] = useState(null);
  const [contributions, setContributions] = useState([]);

  // key -> { log, contributions }
  const cacheRef = useRef(new Map());
  // Keys whose create is in flight, so StrictMode's double-invoked effect fires one POST.
  const inflightRef = useRef(new Set());

  const key = sessionId && residentId ? `${sessionId}:${residentId}` : null;

  useEffect(() => {
    if (!key) {
      setLog(null);
      setContributions([]);
      return;
    }

    // Start the clock for this resident. Set-once, so the double effect cannot reset it.
    clock.start(residentId);

    const cached = cacheRef.current.get(key);
    if (cached) {
      setLog(cached.log);
      setContributions(cached.contributions);
      return;
    }

    if (inflightRef.current.has(key)) return;
    inflightRef.current.add(key);

    const remember = (row, contribs) => {
      cacheRef.current.set(key, { log: row, contributions: contribs });
      // Only paint if the user is still on this resident.
      if (`${sessionId}:${residentId}` === key) {
        setLog(row);
        setContributions(contribs);
      }
    };

    openCccResidentLog(sessionId, residentId)
      .then((row) => {
        inflightRef.current.delete(key);
        remember(row, row.contributions || []);
      })
      .catch(() => {
        inflightRef.current.delete(key);
        // Backend unreachable. Carry on with a client-side id and queue the create, so the
        // meeting is never blocked by the network. If the server later answers with a
        // different canonical id, the queue rewrites the pending writes onto it.
        const clientId = queue.enqueuePost(
          '/ccc/resident-logs',
          { session_id: sessionId, resident_id: residentId },
          { remap: true },
        );
        remember(
          { id: clientId, session_id: sessionId, resident_id: residentId, offline: true },
          [],
        );
      });

    // No cleanup that clears the clock or aborts the create: StrictMode's synthetic
    // unmount would otherwise wipe t0 and fire a second POST.
  }, [key, sessionId, residentId]);

  // Restart the clock when the drawer is opened, so "seconds to find it" is measured from
  // when the operator started looking rather than from when the page happened to load.
  const wasOpen = useRef(drawerOpen);
  useEffect(() => {
    if (drawerOpen && !wasOpen.current && residentId) clock.restart(residentId);
    wasOpen.current = drawerOpen;
  }, [drawerOpen, residentId]);

  useEffect(() => {
    if (residentId) setCount(residentId, contributions.length);
  }, [residentId, contributions.length, setCount]);

  const handleContributionsChange = useCallback((next) => {
    setContributions(next);
    if (key) {
      const entry = cacheRef.current.get(key);
      if (entry) cacheRef.current.set(key, { ...entry, contributions: next });
    }
  }, [key]);

  const handleDone = useCallback(() => {
    if (log?.id) queue.enqueuePost(`/ccc/resident-logs/${log.id}/close`, {});
    if (residentId) clock.clear(residentId);
    setDrawerOpen(false);
    showToast?.('Resident closed out');
  }, [log, residentId, setDrawerOpen, showToast]);

  // Drop the cache when the meeting ends, so a later meeting starts clean.
  useEffect(() => {
    if (!sessionId) {
      cacheRef.current.clear();
      inflightRef.current.clear();
    }
  }, [sessionId]);

  if (!session || !residentId) return null;

  return (
    <>
      <CccPill
        count={contributions.length}
        open={drawerOpen}
        onToggle={toggleDrawer}
      />
      {drawerOpen && (
        <CccDrawer
          session={session}
          residentId={residentId}
          log={log}
          contributions={contributions}
          onContributionsChange={handleContributionsChange}
          onDone={handleDone}
          onClose={() => setDrawerOpen(false)}
        />
      )}
    </>
  );
}
