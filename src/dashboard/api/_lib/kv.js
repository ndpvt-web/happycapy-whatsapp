// Upstash Redis KV via REST API (used by Vercel KV)
const KV_URL = process.env.KV_REST_API_URL;
const KV_TOKEN = process.env.KV_REST_API_TOKEN;

async function kvCommand(command, ...args) {
  if (!KV_URL || !KV_TOKEN) {
    throw new Error('KV_REST_API_URL and KV_REST_API_TOKEN must be set');
  }
  const body = [command, ...args];
  const res = await fetch(`${KV_URL}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${KV_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.result;
}

module.exports = {
  kvGet: (key) => kvCommand('GET', key),
  kvSet: (key, value) => kvCommand('SET', key, value),
  kvDel: (key) => kvCommand('DEL', key),
  kvKeys: (pattern) => kvCommand('KEYS', pattern),
  kvMGet: (...keys) => kvCommand('MGET', ...keys),
};
