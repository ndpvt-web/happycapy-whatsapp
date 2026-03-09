"""QR code HTTP server for WhatsApp Web authentication.

Serves an auto-refreshing web page that displays the QR code as a PNG image.
Theorem T1: QR must be served as web page with base64 PNG (from P1 + P6 + P12).
Theorem T2: QR server must auto-refresh every 2s via JavaScript (from P2 + P6).

Security:
- Theorem T_QRPIN: No CORS on /qr endpoint (P_QRAUTH + P_CORS).
  QR code is a session credential. Wildcard CORS would allow any web page
  to programmatically steal it and hijack the WhatsApp session.
"""

import base64
import io
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import qrcode
except ImportError:
    qrcode = None


class QRState:
    """Thread-safe QR code state."""

    def __init__(self):
        self._lock = threading.Lock()
        self._qr_string: str = ""
        self._connected: bool = False
        self._qr_png_b64: str = ""

    def update_qr(self, qr_string: str) -> None:
        with self._lock:
            self._qr_string = qr_string
            self._connected = False
            if qrcode and qr_string:
                img = qrcode.make(qr_string, box_size=8, border=2)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                self._qr_png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            else:
                self._qr_png_b64 = ""

    def set_connected(self) -> None:
        with self._lock:
            self._connected = True
            self._qr_string = ""
            self._qr_png_b64 = ""

    def set_disconnected(self) -> None:
        """Mark WhatsApp as disconnected. QR data is preserved if available."""
        with self._lock:
            self._connected = False

    def get_state(self) -> dict:
        with self._lock:
            return {
                "qr": self._qr_png_b64,
                "connected": self._connected,
                "has_qr": bool(self._qr_string),
            }


# Module-level state shared between server and bridge manager
qr_state = QRState()

# Embedded HTML page (Theorem T2: auto-refresh via JavaScript)
QR_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HappyCapy WhatsApp - QR Authentication</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #111b21;
    color: #e9edef;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
  }
  .container {
    text-align: center;
    padding: 2rem;
    max-width: 500px;
  }
  h1 {
    font-size: 1.5rem;
    margin-bottom: 0.5rem;
    color: #00a884;
  }
  .subtitle {
    font-size: 0.9rem;
    color: #8696a0;
    margin-bottom: 2rem;
  }
  .qr-box {
    background: white;
    border-radius: 16px;
    padding: 24px;
    display: inline-block;
    margin-bottom: 1.5rem;
  }
  .qr-box img {
    display: block;
    max-width: 280px;
    height: auto;
  }
  .status {
    font-size: 1rem;
    padding: 0.75rem 1.5rem;
    border-radius: 8px;
    margin-bottom: 1rem;
  }
  .status.waiting {
    background: rgba(0, 168, 132, 0.15);
    color: #00a884;
  }
  .status.connected {
    background: rgba(0, 168, 132, 0.3);
    color: #00e676;
    font-weight: bold;
  }
  .status.no-qr {
    background: rgba(134, 150, 160, 0.15);
    color: #8696a0;
  }
  .instructions {
    font-size: 0.85rem;
    color: #8696a0;
    line-height: 1.6;
    text-align: left;
    margin-top: 1rem;
  }
  .instructions ol {
    padding-left: 1.5rem;
  }
</style>
</head>
<body>
<div class="container">
  <h1>HappyCapy WhatsApp</h1>
  <p class="subtitle">Scan QR code to connect your WhatsApp</p>

  <div id="qr-container">
    <div id="qr-box" class="qr-box" style="display:none;">
      <img id="qr-img" alt="QR Code" />
    </div>
    <div id="status" class="status no-qr">Waiting for QR code...</div>
  </div>

  <div class="instructions">
    <ol>
      <li>Open WhatsApp on your phone</li>
      <li>Go to <strong>Settings &gt; Linked Devices</strong></li>
      <li>Tap <strong>Link a Device</strong></li>
      <li>Point your phone camera at this QR code</li>
    </ol>
  </div>
</div>

<script>
let pollInterval = null;

async function pollQR() {
  try {
    const resp = await fetch('/qr');
    const data = await resp.json();

    const qrBox = document.getElementById('qr-box');
    const qrImg = document.getElementById('qr-img');
    const status = document.getElementById('status');

    if (data.connected) {
      qrBox.style.display = 'none';
      status.className = 'status connected';
      status.textContent = 'Connected! WhatsApp is now linked.';
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    } else if (data.qr) {
      qrImg.src = 'data:image/png;base64,' + data.qr;
      qrBox.style.display = 'inline-block';
      status.className = 'status waiting';
      status.textContent = 'Scan this QR code with WhatsApp...';
    } else {
      qrBox.style.display = 'none';
      status.className = 'status no-qr';
      status.textContent = 'Waiting for QR code...';
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}

// Poll every 2 seconds (Theorem T2)
pollInterval = setInterval(pollQR, 2000);
pollQR();
</script>
</body>
</html>"""


class QRRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for QR page and API."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(QR_PAGE_HTML.encode("utf-8"))

        elif self.path == "/qr":
            # Theorem T_QRPIN: NO CORS header. QR is a session credential (P_QRAUTH).
            # Wildcard CORS would let any web page steal the QR and hijack the account.
            # Same-origin policy restricts access to the QR page's own origin only.
            state = qr_state.get_state()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode("utf-8"))

        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        else:
            self.send_response(404)
            self.end_headers()


def start_qr_server(port: int) -> HTTPServer:
    """Start the QR HTTP server in a background thread.

    Args:
        port: Port to listen on (Theorem T5: default 8765, exposed via export-port.sh)

    Returns:
        The HTTPServer instance (call .shutdown() to stop)
    """
    # Set SO_REUSEADDR before bind to avoid "Address already in use" on restart.
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", port), QRRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"QR server running on http://0.0.0.0:{port}")
    return server
