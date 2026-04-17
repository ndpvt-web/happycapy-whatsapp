const BASE = '/api/proactive';

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

export const api = {
  stats:          () => fetchJSON(`${BASE}/stats`),
  students:       () => fetchJSON(`${BASE}/students`),
  studentFull:   (jid) => fetchJSON(`${BASE}/student/${encodeURIComponent(jid)}/full`),
  studentMastery:(jid) => fetchJSON(`${BASE}/student/${encodeURIComponent(jid)}/mastery`),
  mastery:        () => fetchJSON(`${BASE}/mastery`),
  affectSummary:  () => fetchJSON(`${BASE}/affect-summary`),
  effectiveness:  () => fetchJSON(`${BASE}/effectiveness`),
  overview:       () => fetchJSON(`${BASE}/overview`),
  calendar:       () => fetchJSON(`${BASE}/calendar`),
};
