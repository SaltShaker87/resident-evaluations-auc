/**
 * AUC — CCC retrieval timer.
 *
 * Measures how long it took to find the thing that got said: the clock starts when
 * the drawer opens on a resident, and each logged contribution reports the seconds
 * since the clock last started, then restarts it. So the 2nd contribution measures
 * from the 1st, not from drawer open. No manual stopwatch.
 *
 * Cumulative time is not lost by this choice — it is always recoverable server-side
 * as (contribution.created_at - log.opened_at).
 *
 * Kept in sessionStorage so a mid-meeting page reload doesn't zero the clocks.
 * `start` is set-once per resident, so React StrictMode's double-invoked effects
 * can't reset it.
 */

const STORAGE_KEY = 'auc:ccc:clock:v1';

function load() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

let clocks = load();

function persist() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(clocks));
  } catch {
    // Non-fatal: the timer degrades to per-tab-lifetime only.
  }
}

/** Start this resident's clock if it isn't already running. Idempotent. */
export function start(residentId) {
  if (!residentId) return;
  if (clocks[residentId] == null) {
    clocks[residentId] = Date.now();
    persist();
  }
}

/** Start or restart. Used when the drawer is reopened. */
export function restart(residentId) {
  if (!residentId) return;
  clocks[residentId] = Date.now();
  persist();
}

/**
 * Seconds since this resident's clock started, then restart it so the next
 * contribution is timed from this one. Returns null if no clock is running, in which
 * case the backend leaves retrieval_seconds NULL rather than guessing.
 */
export function elapsedAndRestart(residentId) {
  const started = clocks[residentId];
  if (started == null) {
    restart(residentId);
    return null;
  }
  const seconds = Math.round((Date.now() - started) / 1000);
  clocks[residentId] = Date.now();
  persist();
  return seconds;
}

export function clear(residentId) {
  if (residentId in clocks) {
    delete clocks[residentId];
    persist();
  }
}

export function clearAll() {
  clocks = {};
  persist();
}
