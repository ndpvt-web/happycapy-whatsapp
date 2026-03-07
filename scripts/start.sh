#!/bin/bash
# HappyCapy WhatsApp Skill - Launch Script
# Starts the Python orchestrator which manages all services.

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Verify bridge is compiled
if [ ! -f "$SKILL_DIR/bridge/dist/index.js" ]; then
    echo "Bridge not compiled. Running setup first..."
    bash "$SKILL_DIR/scripts/setup.sh"
fi

echo "Starting HappyCapy WhatsApp..."
cd "$SKILL_DIR"
exec python3 -m src.main
