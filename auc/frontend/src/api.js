/**
 * AUC — API client
 * All fetch calls to the backend live here.
 */

const BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (res.status === 401 && !path.startsWith('/auth/')) {
    // Session expired or logged out elsewhere — send the app back to the login screen
    window.dispatchEvent(new Event('auc:unauthenticated'));
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

// Auth
export const getAuthStatus = () => request('/auth/status');

export const setupPassword = (password) =>
  request('/auth/setup', { method: 'POST', body: JSON.stringify({ password }) });

export const login = (password) =>
  request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) });

export const logout = () => request('/auth/logout', { method: 'POST' });

export const recoverPassword = (recoveryKey, newPassword) =>
  request('/auth/recover', {
    method: 'POST',
    body: JSON.stringify({ recovery_key: recoveryKey, new_password: newPassword }),
  });

export const changePassword = (currentPassword, newPassword) =>
  request('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });

// Residents
export const getResidents = (activeOnly = true) =>
  request(`/residents?active_only=${activeOnly}`);

export const getResident = (id) => request(`/residents/${id}`);

export const createResident = (data) =>
  request('/residents', { method: 'POST', body: JSON.stringify(data) });

export const bulkImportResidents = (residents) =>
  request('/residents/bulk', { method: 'POST', body: JSON.stringify({ residents }) });

export const updateResident = (id, data) =>
  request(`/residents/${id}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteResident = (id) =>
  request(`/residents/${id}`, { method: 'DELETE' });

export const uploadPhoto = async (residentId, file) => {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/residents/${residentId}/photo`, {
    method: 'POST',
    body: formData,
  });
  if (res.status === 401) window.dispatchEvent(new Event('auc:unauthenticated'));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Photo upload failed' }));
    throw new Error(err.detail || 'Photo upload failed');
  }
  return res.json();
};

export const getPhotoUrl = (filename) =>
  filename ? `${BASE}/photos/${filename}` : null;

// ACGME Domains
export const getDomains = () => request('/domains');

// Notes
export const getNotes = (residentId, domain = null) => {
  const q = domain ? `?domain=${encodeURIComponent(domain)}` : '';
  return request(`/residents/${residentId}/notes${q}`);
};

export const createNote = (residentId, data) =>
  request(`/residents/${residentId}/notes`, { method: 'POST', body: JSON.stringify(data) });

export const updateNote = (noteId, data) =>
  request(`/notes/${noteId}`, { method: 'PUT', body: JSON.stringify(data) });

export const deleteNote = (noteId) =>
  request(`/notes/${noteId}`, { method: 'DELETE' });

// Follow-ups
export const getAllFollowups = (resolved = false) =>
  request(`/followups?resolved=${resolved}`);

export const getResidentFollowups = (residentId, includeResolved = false) =>
  request(`/residents/${residentId}/followups?include_resolved=${includeResolved}`);

export const createFollowup = (residentId, data) =>
  request(`/residents/${residentId}/followups`, { method: 'POST', body: JSON.stringify(data) });

export const resolveFollowup = (id) =>
  request(`/followups/${id}/resolve`, { method: 'PUT' });

export const unresolveFollowup = (id) =>
  request(`/followups/${id}/unresolve`, { method: 'PUT' });

export const deleteFollowup = (id) =>
  request(`/followups/${id}`, { method: 'DELETE' });

// Summaries
export const getSummaries = (residentId) =>
  request(`/residents/${residentId}/summaries`);

// Returns raw Response for streaming — caller reads body as NDJSON stream
export const generateSummary = (residentId, model = null) => {
  const qs = model ? `?model=${encodeURIComponent(model)}` : '';
  return fetch(`${BASE}/residents/${residentId}/generate-summary${qs}`, { method: 'POST' });
};

export const approveSummary = (summaryId, approvedText) =>
  request(`/summaries/${summaryId}/approve`, {
    method: 'PUT',
    body: JSON.stringify({ approved_text: approvedText }),
  });

export const deleteSummary = (id) =>
  request(`/summaries/${id}`, { method: 'DELETE' });

// Ollama
export const getOllamaModels = () => request('/ollama/models');

// MedHub Import
export const parseMedhubCsv = async (file) => {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE}/medhub/parse-csv`, { method: 'POST', body: formData });
  if (res.status === 401) window.dispatchEvent(new Event('auc:unauthenticated'));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Parse failed' }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
};

export const importMedhubCsv = (rows, mapping, manualMatches = {}) =>
  request('/medhub/import', {
    method: 'POST',
    body: JSON.stringify({ rows, mapping, manual_matches: manualMatches }),
  });

export const syncMedhubApi = () => request('/medhub/sync', { method: 'POST' });

export const getMedhubStatus = () => request('/medhub/status');

export const getResidentsForMatching = () => request('/residents/list-for-matching');

// Resident advancement (snapshot + undo)
export const executeAdvancement = (plan) =>
  request('/advancement/execute', { method: 'POST', body: JSON.stringify(plan) });

export const getActiveSnapshot = () => request('/advancement/active');

export const restoreSnapshot = (snapshotId) =>
  request(`/advancement/${snapshotId}/restore`, { method: 'POST' });

export const dismissSnapshot = (snapshotId) =>
  request(`/advancement/${snapshotId}/dismiss`, { method: 'POST' });

// Data management
export const downloadBackup = async () => {
  const res = await fetch(`${BASE}/backup`);
  if (res.status === 401) window.dispatchEvent(new Event('auc:unauthenticated'));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Backup failed' }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : 'auc-backup.db';
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};
