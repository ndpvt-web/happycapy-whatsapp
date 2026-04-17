const { kvKeys, kvGet } = require('../_lib/kv.js');

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Content-Type': 'application/json',
};

module.exports = async function handler(req, res) {
  if (req.method === 'OPTIONS') return res.status(200).json({});

  Object.entries(CORS).forEach(([k, v]) => res.setHeader(k, v));

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  // If KV is not configured yet, return empty status gracefully
  if (!process.env.KV_REST_API_URL || !process.env.KV_REST_API_TOKEN) {
    return res.status(200).json({});
  }

  try {
    const keys = await kvKeys('auth:*');
    const status = {};

    for (const key of keys || []) {
      const appId = key.replace('auth:', '');
      const raw = await kvGet(key);
      if (raw) {
        const meta = JSON.parse(raw);
        status[appId] = {
          connected: true,
          saved_at: meta.saved_at,
          auth_type: meta.auth_type,
        };
      }
    }

    return res.status(200).json(status);
  } catch (err) {
    console.error('Auth status error:', err);
    return res.status(200).json({});
  }
}
