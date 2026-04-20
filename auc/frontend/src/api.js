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
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

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
  if (!res.ok) throw new Error('Photo upload failed');
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

export const generateSummary = (residentId) =>
  request(`/residents/${residentId}/generate-summary`, { method: 'POST' });

export const approveSummary = (summaryId, approvedText) =>
  request(`/summaries/${summaryId}/approve`, {
    method: 'PUT',
    body: JSON.stringify({ approved_text: approvedText }),
  });

export const deleteSummary = (id) =>
  request(`/summaries/${id}`, { method: 'DELETE' });
