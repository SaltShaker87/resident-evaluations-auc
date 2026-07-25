/**
 * AUC — CCC session state.
 *
 * The active meeting is needed by three widely separated places: the banner at the top
 * of the app shell, the pill and drawer that overlay a resident page, and the Study
 * Data page. Drawer open/closed also has to survive navigating between residents. That
 * is read-many/subscribe-many mutable state, which is what React context is for — the
 * app's existing `auc:unauthenticated` window event is the right tool for a rare
 * one-way notification, not for this.
 *
 * cccQueue stays a plain module with its own subscribe(), because it must keep working
 * across unmounts and outside React entirely. This provider just mirrors its status in.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import {
  getActiveCccSession, startCccSession, closeCccSession,
} from '../api';
import * as queue from './cccQueue';
import * as clock from './cccClock';

const DRAWER_KEY = 'auc:ccc:drawerOpen';

const CccContext = createContext(null);

export function useCcc() {
  const value = useContext(CccContext);
  if (!value) throw new Error('useCcc must be used inside <CccProvider>');
  return value;
}

export function CccProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  // sessionStorage, not localStorage: the drawer should survive route changes and a
  // reload, but a new tab tomorrow shouldn't come up with the drawer already open.
  const [drawerOpen, setDrawerOpenState] = useState(
    () => sessionStorage.getItem(DRAWER_KEY) === '1'
  );
  const [queueStatus, setQueueStatus] = useState(queue.getStatus);
  // Per-resident contribution counts, so the pill can show a number without every
  // consumer refetching.
  const [counts, setCounts] = useState({});

  useEffect(() => {
    queue.initQueue();
    return queue.subscribe(setQueueStatus);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const active = await getActiveCccSession();
      setSession(active || null);
    } catch {
      // Offline or not signed in yet. Leave whatever we had; the banner just won't
      // appear, and nothing else in the app depends on this.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const setDrawerOpen = useCallback((open) => {
    setDrawerOpenState(open);
    sessionStorage.setItem(DRAWER_KEY, open ? '1' : '0');
  }, []);

  const toggleDrawer = useCallback(() => {
    setDrawerOpenState((prev) => {
      const next = !prev;
      sessionStorage.setItem(DRAWER_KEY, next ? '1' : '0');
      return next;
    });
  }, []);

  const startSession = useCallback(async (meetingDate, cycleLabel) => {
    const created = await startCccSession(meetingDate, cycleLabel);
    setSession(created);
    setDrawerOpen(true);
    return created;
  }, [setDrawerOpen]);

  const endSession = useCallback(async () => {
    if (!session) return;
    // Land the last few taps before closing, so nothing is stranded mid-sentence.
    await queue.drain();
    try {
      await closeCccSession(session.id);
    } finally {
      setSession(null);
      setDrawerOpen(false);
      setCounts({});
      clock.clearAll();
    }
  }, [session, setDrawerOpen]);

  const setCount = useCallback((residentId, n) => {
    setCounts((prev) => (prev[residentId] === n ? prev : { ...prev, [residentId]: n }));
  }, []);

  // One global listener for the whole app. No-ops when no meeting is running.
  useEffect(() => {
    if (!session) return undefined;
    const onKeyDown = (e) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'L' || e.key === 'l')) {
        e.preventDefault();
        toggleDrawer();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [session, toggleDrawer]);

  const value = useMemo(() => ({
    session,
    loading,
    refresh,
    startSession,
    endSession,
    drawerOpen,
    setDrawerOpen,
    toggleDrawer,
    counts,
    setCount,
    queue: queueStatus,
  }), [session, loading, refresh, startSession, endSession, drawerOpen,
       setDrawerOpen, toggleDrawer, counts, setCount, queueStatus]);

  return <CccContext.Provider value={value}>{children}</CccContext.Provider>;
}
