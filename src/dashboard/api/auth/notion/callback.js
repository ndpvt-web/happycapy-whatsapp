const { createHmac } = require('crypto');
const { kvSet } = require('../../_lib/kv.js');
const { encrypt } = require('../../_lib/crypto.js');

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { code, state, error: oauthError } = req.query;
  const dashboardUrl = process.env.DASHBOARD_URL || 'https://aegis-hq.vercel.app';

  // Handle OAuth errors (user denied, etc.)
  if (oauthError) {
    return res.writeHead(302, {
      Location: `${dashboardUrl}/#/apps?auth_error=${encodeURIComponent(oauthError)}`,
    }).end();
  }

  if (!code || !state) {
    return res.writeHead(302, {
      Location: `${dashboardUrl}/#/apps?auth_error=missing_code`,
    }).end();
  }

  // Validate CSRF state
  const stateSecret = process.env.OAUTH_STATE_SECRET;
  if (stateSecret) {
    const [ts, hmac] = state.split('.');
    const expected = createHmac('sha256', stateSecret).update(ts).digest('hex').slice(0, 16);
    const age = Date.now() - parseInt(ts, 10);
    if (hmac !== expected || age > 600000) { // 10 min max
      return res.writeHead(302, {
        Location: `${dashboardUrl}/#/apps?auth_error=invalid_state`,
      }).end();
    }
  }

  const clientId = process.env.NOTION_CLIENT_ID;
  const clientSecret = process.env.NOTION_CLIENT_SECRET;
  const redirectUri = `${dashboardUrl}/api/auth/notion/callback`;

  if (!clientId || !clientSecret) {
    return res.writeHead(302, {
      Location: `${dashboardUrl}/#/apps?auth_error=server_config`,
    }).end();
  }

  try {
    // Exchange authorization code for access token
    const basicAuth = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
    const tokenRes = await fetch('https://api.notion.com/v1/oauth/token', {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${basicAuth}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        grant_type: 'authorization_code',
        code: code,
        redirect_uri: redirectUri,
      }),
    });

    const tokenData = await tokenRes.json();

    if (!tokenRes.ok || !tokenData.access_token) {
      console.error('Notion token exchange failed:', tokenData);
      return res.writeHead(302, {
        Location: `${dashboardUrl}/#/apps?auth_error=token_exchange`,
      }).end();
    }

    // Store encrypted token in KV
    const encrypted = encrypt(tokenData.access_token);
    const meta = JSON.stringify({
      encrypted_token: encrypted,
      auth_type: 'oauth',
      workspace_name: tokenData.workspace_name || '',
      workspace_icon: tokenData.workspace_icon || '',
      bot_id: tokenData.bot_id || '',
      saved_at: new Date().toISOString(),
    });
    await kvSet('auth:notion', meta);

    // Redirect back to dashboard with success
    return res.writeHead(302, {
      Location: `${dashboardUrl}/#/apps?auth_success=notion`,
    }).end();
  } catch (err) {
    console.error('Notion OAuth callback error:', err);
    return res.writeHead(302, {
      Location: `${dashboardUrl}/#/apps?auth_error=server_error`,
    }).end();
  }
};
