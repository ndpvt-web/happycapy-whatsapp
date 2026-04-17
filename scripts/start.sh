#!/bin/bash
# HappyCapy WhatsApp Skill - Launch Script
# Supports foreground mode and 24/7 daemon mode with process supervision.
#
# Usage:
#   bash start.sh              - Start in foreground (default)
#   bash start.sh daemon       - Start as background daemon (24/7)
#   bash start.sh stop         - Stop the running daemon
#   bash start.sh restart      - Restart the daemon
#   bash start.sh status       - Show daemon status

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Verify bridge is compiled
if [ ! -f "$SKILL_DIR/bridge/dist/index.js" ]; then
    echo "Bridge not compiled. Running setup first..."
    bash "$SKILL_DIR/scripts/setup.sh"
fi

cd "$SKILL_DIR"

CMD="${1:-foreground}"

case "$CMD" in
    daemon|start)
        echo "Starting HappyCapy WhatsApp in daemon mode (24/7)..."
        # Pre-clear ports to avoid orphan-bridge EADDRINUSE conflicts
        lsof -ti :3002 2>/dev/null | xargs kill -9 2>/dev/null || true
        lsof -ti :8765 2>/dev/null | xargs kill -9 2>/dev/null || true
        sleep 1
        python3 -m src.daemon start
        ;;
    stop)
        python3 -m src.daemon stop
        ;;
    restart)
        python3 -m src.daemon restart
        ;;
    status)
        python3 -m src.daemon status
        ;;
    foreground|"")
        echo "Starting HappyCapy WhatsApp in foreground..."
        exec python3 -m src.main
        ;;
    *)
        echo "Usage: $0 {foreground|daemon|stop|restart|status}"
        exit 1
        ;;
esac
