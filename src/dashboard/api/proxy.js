// Proxy /api/* requests to the live FastAPI backend.
// Set BACKEND_URL env var in Vercel to your backend's public URL.
// Example: https://aegis-api.fly.dev

export default async function handler(req, res) {
  const backendUrl = process.env.BACKEND_URL;
  if (!backendUrl) {
    return res.status(503).json({
      error: 'Backend not configured',
      hint: 'Set BACKEND_URL environment variable in Vercel project settings',
    });
  }

  const { path } = req.query;
  const target = `${backendUrl}/api/${path || ''}`;

  try {
    const headers = { 'Content-Type': 'application/json' };
    const fetchOpts = { method: req.method, headers };

    if (req.method !== 'GET' && req.method !== 'HEAD' && req.body) {
      fetchOpts.body = JSON.stringify(req.body);
    }

    const upstream = await fetch(target, fetchOpts);
    const contentType = upstream.headers.get('content-type') || 'application/json';
    const body = await upstream.text();

    res.setHeader('Content-Type', contentType);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.status(upstream.status).send(body);
  } catch (err) {
    res.status(502).json({ error: 'Backend unreachable', detail: err.message });
  }
}
