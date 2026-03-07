#!/bin/bash
# HappyCapy WhatsApp Skill - Setup Script
# Installs all dependencies and compiles TypeScript bridge.

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BRIDGE_DIR="$SKILL_DIR/bridge"

echo "=== HappyCapy WhatsApp Setup ==="
echo "Skill directory: $SKILL_DIR"
echo ""

# 1. Install Node.js bridge dependencies (including devDependencies for TypeScript)
echo "[1/3] Installing bridge dependencies..."
cd "$BRIDGE_DIR"
npm install --include=dev --no-audit --no-fund 2>&1
echo "  Done."

# 2. Compile TypeScript
echo "[2/3] Compiling TypeScript bridge..."
node node_modules/typescript/bin/tsc 2>&1
echo "  Done. Output: $BRIDGE_DIR/dist/"

# 3. Install Python dependencies
echo "[3/3] Installing Python dependencies..."
pip install --break-system-packages --quiet qrcode pillow websockets httpx 2>&1 || \
pip3 install --break-system-packages --quiet qrcode pillow websockets httpx 2>&1 || \
pip install --quiet qrcode pillow websockets httpx 2>&1
echo "  Done."

# Create data directory
DATA_DIR="$HOME/.happycapy-whatsapp"
mkdir -p "$DATA_DIR/whatsapp-auth"
mkdir -p "$DATA_DIR/media"
mkdir -p "$DATA_DIR/logs"
echo ""
echo "Data directory: $DATA_DIR"

echo ""
echo "=== Setup Complete ==="
echo "Run 'bash scripts/start.sh' to launch."
