const { kvGet } = require('../_lib/kv.js');
const { decrypt } = require('../_lib/crypto.js');

module.exports = async function handler(req, res) {
  res.setHeader('Content-Type', 'application/json');

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  // Bot authentication via shared secret
  const authHeader = req.headers.authorization || '';
  const botKey = process.env.BOT_API_KEY;
  if (!botKey || authHeader !== `Bearer ${botKey}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const { app_id } = req.query;
  if (!app_id) {
    return res.status(400).json({ error: 'app_id is required' });
  }

  try {
    const raw = await kvGet(`auth:${app_id}`);
    if (!raw) {
      return res.status(404).json({ error: 'Token not found', app_id });
    }

    const meta = JSON.parse(raw);
    const token = decrypt(meta.encrypted_token);

    return res.status(200).json({
      app_id,
      token,
      auth_type: meta.auth_type,
      saved_at: meta.saved_at,
    });
  } catch (err) {
    console.error('Token fetch error:', err);
    return res.status(500).json({ error: 'Failed to fetch token' });
  }
}
