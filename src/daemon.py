"""24/7 daemon mode with process supervision and auto-restart.

Runs the WhatsApp orchestrator as a background daemon with:
- PID file for tracking
- Automatic restart on crash with exponential backoff
- Log file rotation
- Health monitoring endpoint
- Graceful shutdown via SIGTERM
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("WHATSAPP_BASE_DIR", str(Path.home() / ".happycapy-whatsapp")))
PID_FILE = DATA_DIR / "daemon.pid"
LOG_FILE = DATA_DIR / "logs" / "daemon.log"
SKILL_DIR = Path(__file__).resolve().parent.parent

MAX_RESTARTS = 50  # Max restarts before giving up
INITIAL_BACKOFF = 3  # seconds
MAX_BACKOFF = 120  # seconds


def is_running() -> tuple[bool, int]:
    """Check if the daemon is already running. Returns (running, pid)."""
    if not PID_FILE.exists():
        return False, 0

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return True, pid
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False, 0


def write_pid(pid: int) -> None:
    """Write PID file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def remove_pid() -> None:
    """Remove PID file."""
    PID_FILE.unlink(missing_ok=True)


def log(msg: str) -> None:
    """Write to daemon log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(line.rstrip())


def rotate_log() -> None:
    """Rotate log if it exceeds 10MB."""
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 10 * 1024 * 1024:
        rotated = LOG_FILE.with_suffix(".log.1")
        if rotated.exists():
            rotated.unlink()
        LOG_FILE.rename(rotated)


def start_daemon() -> int:
    """Start the daemon in background. Returns the daemon PID."""
    running, pid = is_running()
    if running:
        print(f"Daemon already running (PID {pid})")
        return pid

    # Fork to background
    daemon_pid = os.fork()
    if daemon_pid > 0:
        # Parent: wait briefly and verify child started
        time.sleep(1)
        running, actual_pid = is_running()
        if running:
            print(f"Daemon started (PID {actual_pid})")
            return actual_pid
        else:
            print("Daemon failed to start. Check logs.")
            return 0

    # Child: become daemon
    os.setsid()

    # Redirect stdio
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    sys.stdin = open(os.devnull, "r")
    log_fd = open(LOG_FILE, "a")
    sys.stdout = log_fd
    sys.stderr = log_fd

    write_pid(os.getpid())
    log(f"Daemon started (PID {os.getpid()})")

    _supervise_loop()
    sys.exit(0)


def start_foreground() -> None:
    """Start in foreground (for debugging)."""
    running, pid = is_running()
    if running:
        print(f"Daemon already running (PID {pid}). Stop it first.")
        return

    write_pid(os.getpid())
    log(f"Foreground mode started (PID {os.getpid()})")

    try:
        _supervise_loop()
    finally:
        remove_pid()


def _inject_env_from_bashrc(env: dict) -> None:
    """Inject exported env vars from .bashrc that the daemon may not inherit."""
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        return
    try:
        for line in bashrc.read_text().splitlines():
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                # Parse: export KEY="value" or export KEY=value
                kv = line[7:].strip()  # strip "export "
                key, _, val = kv.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in env:
                    env[key] = val
    except Exception:
        pass  # Non-critical -- fail silently


def _supervise_loop() -> None:
    """Main supervision loop - restart orchestrator on crash."""
    restart_count = 0
    should_run = True

    def handle_signal(sig, frame):
        nonlocal should_run
        log(f"Received signal {sig}, shutting down...")
        should_run = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while should_run and restart_count < MAX_RESTARTS:
        rotate_log()

        log(f"Starting orchestrator (attempt {restart_count + 1})...")
        start_time = time.time()

        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            # Source critical tokens from .bashrc that may not be in daemon env
            _inject_env_from_bashrc(env)
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "src.main"],
                cwd=str(SKILL_DIR),
                stdout=subprocess.PIPE if LOG_FILE else None,
                stderr=subprocess.STDOUT if LOG_FILE else None,
                text=True,
                env=env,
            )

            # Forward output to log
            if proc.stdout:
                for line in proc.stdout:
                    log(f"[orchestrator] {line.rstrip()}")

            proc.wait()
            exit_code = proc.returncode

        except Exception as e:
            log(f"Failed to start orchestrator: {e}")
            exit_code = 1

        elapsed = time.time() - start_time

        if not should_run:
            log("Shutdown requested, not restarting.")
            break

        if exit_code == 0:
            log("Orchestrator exited cleanly.")
            break

        restart_count += 1

        # Reset restart count if process ran for >5 minutes (was stable)
        if elapsed > 300:
            restart_count = 1

        backoff = min(INITIAL_BACKOFF * (2 ** (restart_count - 1)), MAX_BACKOFF)
        log(f"Orchestrator crashed (exit {exit_code}, ran {elapsed:.0f}s). "
            f"Restart {restart_count}/{MAX_RESTARTS} in {backoff}s...")
        time.sleep(backoff)

    if restart_count >= MAX_RESTARTS:
        log(f"Max restarts ({MAX_RESTARTS}) reached. Giving up.")

    remove_pid()
    log("Daemon stopped.")


def stop_daemon() -> bool:
    """Stop the running daemon. Returns True if stopped."""
    running, pid = is_running()
    if not running:
        print("Daemon is not running.")
        return False

    print(f"Stopping daemon (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)

        # Wait up to 15 seconds for graceful shutdown
        for _ in range(30):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                remove_pid()
                print("Daemon stopped.")
                return True

        # Force kill
        print("Force killing...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
        remove_pid()
        print("Daemon killed.")
        return True
    except OSError as e:
        print(f"Error stopping daemon: {e}")
        remove_pid()
        return False


def status() -> dict:
    """Get daemon status."""
    running, pid = is_running()
    result = {
        "running": running,
        "pid": pid,
        "pid_file": str(PID_FILE),
        "log_file": str(LOG_FILE),
    }

    if LOG_FILE.exists():
        result["log_size_kb"] = LOG_FILE.stat().st_size // 1024
        # Get last 5 lines of log
        try:
            lines = LOG_FILE.read_text().strip().split("\n")
            result["recent_log"] = lines[-5:]
        except Exception:
            pass

    return result


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m src.daemon {start|stop|restart|status|foreground}")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "start":
        start_daemon()
    elif cmd == "stop":
        stop_daemon()
    elif cmd == "restart":
        stop_daemon()
        time.sleep(2)
        start_daemon()
    elif cmd == "status":
        s = status()
        if s["running"]:
            print(f"Running (PID {s['pid']})")
        else:
            print("Not running")
        if "recent_log" in s:
            print("\nRecent log:")
            for line in s["recent_log"]:
                print(f"  {line}")
    elif cmd == "foreground":
        start_foreground()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
