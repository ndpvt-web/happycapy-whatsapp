const { kvGet, kvSet, kvDel } = require('../_lib/kv.js');
const { encrypt } = require('../_lib/crypto.js');

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Content-Type': 'application/json',
};

module.exports = async function handler(req, res) {
  if (req.method === 'OPTIONS') return res.status(200).json({});

  Object.entries(CORS).forEach(([k, v]) => res.setHeader(k, v));

  if (req.method === 'POST') {
    const { app_id, token, auth_type } = req.body || {};
    if (!app_id || !token) {
      return res.status(400).json({ error: 'app_id and token are required' });
    }

    try {
      const encrypted = encrypt(token);
      const meta = JSON.stringify({
        encrypted_token: encrypted,
        auth_type: auth_type || 'token',
        saved_at: new Date().toISOString(),
      });
      await kvSet(`auth:${app_id}`, meta);
      return res.status(200).json({ ok: true, app_id });
    } catch (err) {
      console.error('Token save error:', err);
      return res.status(500).json({ error: 'Failed to save token' });
    }
  }

  if (req.method === 'DELETE') {
    const { app_id } = req.body || {};
    if (!app_id) {
      return res.status(400).json({ error: 'app_id is required' });
    }

    try {
      await kvDel(`auth:${app_id}`);
      return res.status(200).json({ ok: true, app_id });
    } catch (err) {
      console.error('Token delete error:', err);
      return res.status(500).json({ error: 'Failed to delete token' });
    }
  }

  return res.status(405).json({ error: 'Method not allowed' });
}
