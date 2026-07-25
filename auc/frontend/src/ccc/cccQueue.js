/**
 * AUC — CCC write outbox.
 *
 * Everything the meeting drawer autosaves goes through here. The point is that a
 * dead backend, a flaky wifi hop, or an accidental page reload in the middle of a
 * committee meeting must never lose what was typed.
 *
 * How it holds together:
 *
 * - **Client-generated ids.** Callers mint the row id before POSTing (see newId), so
 *   a row's PATCH path is known before its POST has landed. That is what lets this be
 *   a plain FIFO instead of a dependency graph.
 * - **Strict FIFO, single flight.** One `flushing` flag is both the in-flight guard
 *   and the ordering guarantee, so a contribution's POST can never be overtaken by
 *   its own PATCHes.
 * - **Persisted on every mutation.** An op is in localStorage before it is attempted
 *   and stays there until it gets a 2xx, so a reload replays it. Safe because every
 *   CCC write is idempotent server-side.
 * - **Retry forever on network/5xx, drop on permanent 4xx.** A poison op must not
 *   block the queue behind it for the rest of the meeting.
 */

import { request } from '../api';

const STORAGE_KEY = 'auc:ccc:outbox:v1';
const BACKOFF_MS = [1000, 2000, 5000, 15000, 30000, 60000];

let ops = load();
let flushing = false;
let timer = null;
let lastError = null;
let started = false;

const listeners = new Set();

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ops));
  } catch {
    // Storage full or blocked. The in-memory queue still works for this tab; the
    // only thing lost is surviving a reload.
  }
}

export function getStatus() {
  return {
    pending: ops.length,
    flushing,
    lastError,
    online: typeof navigator === 'undefined' ? true : navigator.onLine,
  };
}

function notify() {
  const status = getStatus();
  listeners.forEach((fn) => fn(status));
}

export function subscribe(fn) {
  listeners.add(fn);
  fn(getStatus());
  return () => listeners.delete(fn);
}

/** 8-char id, matching the format the backend generates. */
export function newId() {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replace(/-/g, '').slice(0, 8);
  return Math.random().toString(36).slice(2, 10);
}

function schedule(delay = 0) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    flush();
  }, delay);
}

/**
 * Queue a POST. Returns immediately with the id the row will have, so callers can
 * build child paths (PATCHes) against it right away.
 *
 * `remap: true` means the server may answer with a different canonical id (the
 * get-or-create endpoints); when that happens every queued op referencing the old id
 * is rewritten.
 */
export function enqueuePost(path, body, { id = newId(), remap = false } = {}) {
  ops.push({
    method: 'POST',
    path,
    body: { ...body, id },
    key: `POST ${path} ${id}`,
    notBefore: 0,
    attempts: 0,
    remap,
  });
  persist();
  notify();
  schedule();
  return id;
}

/**
 * Queue a PATCH, merging field-wise into an already-queued PATCH for the same path
 * (last write per field wins). Holding a key down therefore produces one request,
 * not twenty.
 */
export function enqueuePatch(path, patch, { debounceMs = 0 } = {}) {
  const key = `PATCH ${path}`;
  // Index 0 may be mid-flight; never mutate that one.
  const startAt = flushing ? 1 : 0;
  const existing = ops.slice(startAt).find((op) => op.key === key);
  if (existing) {
    existing.body = { ...existing.body, ...patch };
    existing.notBefore = Date.now() + debounceMs;
    existing.attempts = 0;
  } else {
    ops.push({
      method: 'PATCH',
      path,
      body: patch,
      key,
      notBefore: Date.now() + debounceMs,
      attempts: 0,
    });
  }
  persist();
  notify();
  schedule(debounceMs);
}

/**
 * Rewrite queued ops after a get-or-create returned a different canonical id — the
 * offline case, where we invented an id locally and the server already had a row.
 *
 * Every reference is rewritten, including the creating op's own body.id: if that op were
 * ever retried, re-POSTing the stale id would create a second row, whereas the canonical
 * id makes the retry an idempotent no-op.
 */
export function remapId(fromId, toId) {
  if (!fromId || !toId || fromId === toId) return;
  ops.forEach((op) => {
    op.path = op.path.split(fromId).join(toId);
    op.key = op.key.split(fromId).join(toId);
    if (!op.body) return;
    if (op.body.id === fromId) op.body.id = toId;
    if (op.body.resident_log_id === fromId) op.body.resident_log_id = toId;
  });
  persist();
}

export function flushNow() {
  schedule(0);
}

/**
 * Wait for the queue to empty, up to a cap. Used before ending a meeting so the last
 * few taps aren't left stranded. Resolves false on timeout — the caller carries on
 * either way, because the ops are durable and will land later.
 */
export function drain(maxMs = 4000) {
  return new Promise((resolve) => {
    const deadline = Date.now() + maxMs;
    flushNow();
    const poll = setInterval(() => {
      if (!ops.length || Date.now() > deadline) {
        clearInterval(poll);
        resolve(ops.length === 0);
      }
    }, 150);
  });
}

async function flush() {
  if (flushing || !ops.length) return;
  flushing = true;
  notify();
  try {
    while (ops.length) {
      const op = ops[0];
      const wait = op.notBefore - Date.now();
      if (wait > 0) {
        schedule(wait);
        break;
      }
      try {
        const result = await request(op.path, {
          method: op.method,
          body: JSON.stringify(op.body),
        });
        if (op.remap && result?.id && result.id !== op.body.id) {
          remapId(op.body.id, result.id);
        }
        ops.shift();
        lastError = null;
        persist();
        notify();
      } catch (err) {
        const status = err.status;
        // 401 is treated as retryable, not permanent: api.js has already bounced the
        // app to the login screen, and once the operator signs back in the same write
        // succeeds on its next attempt. Nothing is lost and no extra plumbing is
        // needed to resume.
        if (status >= 400 && status < 500 && ![401, 408, 429].includes(status)) {
          // Permanently rejected. Drop it rather than blocking everything behind it.
          console.error('CCC write rejected, dropping:', op, err);
          ops.shift();
          lastError = err.message;
          persist();
          notify();
          continue;
        }
        // Network failure or 5xx: keep it forever and back off.
        op.attempts += 1;
        op.notBefore = Date.now() + BACKOFF_MS[Math.min(op.attempts - 1, BACKOFF_MS.length - 1)];
        lastError = err.message;
        persist();
        notify();
        schedule(op.notBefore - Date.now());
        break;
      }
    }
  } finally {
    flushing = false;
    notify();
  }
}

/** Idempotent — safe under React StrictMode's double-invoked effects. */
export function initQueue() {
  if (started) return;
  started = true;
  window.addEventListener('online', flushNow);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') flushNow();
  });
  flushNow();
}
