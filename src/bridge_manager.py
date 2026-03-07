"""Bridge process manager for the Node.js WhatsApp bridge.

Spawns, monitors, and auto-restarts the Baileys bridge process.
Captures QR codes and status from stdout (Theorem T1, T2).
"""

import os
import subprocess
import threading
import time
from pathlib import Path

from .qr_server import qr_state


class BridgeManager:
    """Manages the Node.js WhatsApp bridge process lifecycle."""

    def __init__(self, bridge_dir: str, port: int, auth_dir: str, token: str = "", rate_limit: int = 30):
        self.bridge_dir = Path(bridge_dir)
        self.port = port
        self.auth_dir = auth_dir
        self.token = token
        self.rate_limit = rate_limit
        self._process: subprocess.Popen | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._connected = False
        self._restart_count = 0
        self._max_restart_backoff = 60  # seconds

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        """Start the bridge in a background thread with auto-restart."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the bridge process."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                if self._process:
                    self._process.kill()
            self._process = None
        self._connected = False

    def _run_loop(self) -> None:
        """Main loop: start bridge, monitor, restart on crash."""
        while self._running:
            try:
                self._start_process()
                self._monitor_stdout()
            except Exception as e:
                print(f"Bridge error: {e}")

            self._connected = False
            self._process = None

            if not self._running:
                break

            # Exponential backoff on restart
            self._restart_count += 1
            backoff = min(5 * (2 ** (self._restart_count - 1)), self._max_restart_backoff)
            print(f"Bridge stopped. Restarting in {backoff}s (attempt {self._restart_count})...")
            time.sleep(backoff)

    def _start_process(self) -> None:
        """Start the Node.js bridge subprocess."""
        entry_point = self.bridge_dir / "dist" / "index.js"
        if not entry_point.exists():
            raise FileNotFoundError(f"Bridge not compiled: {entry_point}")

        env = os.environ.copy()
        env["BRIDGE_PORT"] = str(self.port)
        env["AUTH_DIR"] = self.auth_dir
        env["RATE_LIMIT_PER_MINUTE"] = str(self.rate_limit)
        if self.token:
            env["BRIDGE_TOKEN"] = self.token

        self._process = subprocess.Popen(
            ["node", str(entry_point)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
        )
        print(f"Bridge started (PID {self._process.pid}) on port {self.port}")

    def _monitor_stdout(self) -> None:
        """Read bridge stdout and capture QR/STATUS events."""
        if not self._process or not self._process.stdout:
            return

        for line in self._process.stdout:
            line = line.rstrip("\n")

            if line.startswith("QR:"):
                qr_string = line[3:]
                qr_state.update_qr(qr_string)
                print("QR code received (visible on QR server page)")

            elif line.startswith("STATUS:"):
                status = line[7:]
                if status == "connected":
                    self._connected = True
                    self._restart_count = 0  # Reset backoff on successful connection
                    qr_state.set_connected()
                    print("WhatsApp connected!")
                elif status == "disconnected":
                    self._connected = False
                    print("WhatsApp disconnected")

            else:
                # Forward other bridge output
                if line.strip():
                    print(f"[bridge] {line}")

        # Process ended
        if self._process:
            self._process.wait()
