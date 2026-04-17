const BASE = import.meta.env.VITE_API_URL || '/api';

async function fetchJSON(path, opts) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  health: () => fetchJSON('/health'),
  logs: (lines = 100) => fetchJSON(`/logs?lines=${lines}`),
  config: () => fetchJSON('/config'),
  updateConfig: (updates) =>
    fetchJSON('/config', { method: 'PUT', body: JSON.stringify({ updates }) }),
  contacts: (limit = 100) => fetchJSON(`/contacts?limit=${limit}`),
  contactDetail: (jid) => fetchJSON(`/contacts/${encodeURIComponent(jid)}`),
  audit: (hours = 24, limit = 100) => fetchJSON(`/audit?hours=${hours}&limit=${limit}`),
  analytics: () => fetchJSON('/analytics'),
  queue: (status, limit = 50) =>
    fetchJSON(`/queue?limit=${limit}${status ? `&status=${status}` : ''}`),
  escalations: (status) =>
    fetchJSON(`/escalations${status ? `?status=${status}` : ''}`),
  spreadsheets: () => fetchJSON('/spreadsheets'),
  spreadsheetData: (name, limit = 100, sheet = null) =>
    fetchJSON(`/spreadsheets/${encodeURIComponent(name)}?limit=${limit}${sheet ? `&sheet=${encodeURIComponent(sheet)}` : ''}`),
  knowledgeGraph: (limit = 100) => fetchJSON(`/knowledge-graph?limit=${limit}`),
  memory: () => fetchJSON('/memory'),
  memoryRead: (scope, filename) => fetchJSON(`/memory/read?scope=${encodeURIComponent(scope)}&filename=${encodeURIComponent(filename)}`),
  identity: () => fetchJSON('/identity'),
  updateIdentity: (filename, content) =>
    fetchJSON('/identity', { method: 'PUT', body: JSON.stringify({ filename, content }) }),
  campaigns: () => fetchJSON('/campaigns'),
  lessons: () => fetchJSON('/lessons'),
  cronJobs: () => fetchJSON('/cron'),
  groups: (limit = 500) => fetchJSON(`/groups?limit=${limit}`),
  groupDetail: (jid) => fetchJSON(`/groups/${encodeURIComponent(jid)}`),
  groupAnalysis: (jid) => fetchJSON(`/groups/${encodeURIComponent(jid)}/analysis`),
  syncGroups: () => fetchJSON('/groups/sync', { method: 'POST' }),
  whatsappStatus: () => fetchJSON('/whatsapp/status'),
  whatsappLogout: () => fetchJSON('/whatsapp/logout', { method: 'POST' }),
  broadcastContacts: () => fetchJSON('/broadcast/contacts'),
  sendBroadcast: (message, recipients) =>
    fetchJSON('/broadcast', { method: 'POST', body: JSON.stringify({ message, recipients }) }),
  models: () => fetchJSON('/models'),
  apps: () => fetchJSON('/apps'),
  restart: () => fetchJSON('/restart', { method: 'POST' }),
  authStatus: () => fetchJSON('/auth/status'),
  saveToken: (app_id, token, auth_type = 'token') =>
    fetchJSON('/auth/token', { method: 'POST', body: JSON.stringify({ app_id, token, auth_type }) }),
  deleteToken: (app_id) =>
    fetchJSON('/auth/token', { method: 'DELETE', body: JSON.stringify({ app_id }) }),
};
