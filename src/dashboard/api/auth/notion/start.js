const { createHmac } = require('crypto');

const NOTION_AUTH_URL = 'https://api.notion.com/v1/oauth/authorize';

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const clientId = process.env.NOTION_CLIENT_ID;
  const dashboardUrl = process.env.DASHBOARD_URL || 'https://aegis-hq.vercel.app';
  const stateSecret = process.env.OAUTH_STATE_SECRET;

  if (!clientId) {
    return res.status(500).json({ error: 'NOTION_CLIENT_ID not configured' });
  }

  // Generate CSRF state token (HMAC of timestamp + secret)
  const ts = Date.now().toString();
  const hmac = createHmac('sha256', stateSecret || 'fallback').update(ts).digest('hex').slice(0, 16);
  const state = `${ts}.${hmac}`;

  const redirectUri = `${dashboardUrl}/api/auth/notion/callback`;

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: 'code',
    owner: 'user',
    redirect_uri: redirectUri,
    state: state,
  });

  const authUrl = `${NOTION_AUTH_URL}?${params.toString()}`;
  res.writeHead(302, { Location: authUrl });
  res.end();
};
