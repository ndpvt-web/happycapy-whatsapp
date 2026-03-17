#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Babloo Dashboard Watchdog + Cloudflare Tunnel
# ═══════════════════════════════════════════════════════════
# Keeps the dashboard alive 24/7 with a Cloudflare Tunnel
# for reliable external access (bypasses broken Fly.io TLS).
#
# Usage: nohup bash babloo-watchdog.sh &
# ═══════════════════════════════════════════════════════════

# No set -e: health checks return non-zero by design

# ── Config ──
DASHBOARD_PORT=3456
SKILL_DIR="/home/node/.claude/skills/happycapy-whatsapp"
WHATSAPP_BASE_DIR="/home/node/.happycapy-whatsapp-2"
LOG_FILE="/tmp/babloo-watchdog.log"
PID_FILE="/tmp/babloo-dashboard.pid"
TUNNEL_PID_FILE="/tmp/babloo-tunnel.pid"
TUNNEL_LOG="/tmp/cloudflared-tunnel.log"
TUNNEL_URL_FILE="/tmp/babloo-tunnel-url.txt"
HEALTH_URL="http://localhost:${DASHBOARD_PORT}/api/health"
CHECK_INTERVAL=30        # seconds between health checks
EXPORT_INTERVAL=300      # re-export port every 5 minutes
MAX_RESTART_FAILURES=10  # give up after N consecutive failures

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

start_dashboard() {
    log "Starting dashboard on port ${DASHBOARD_PORT}..."

    # Kill any existing process on the port
    local existing_pid
    existing_pid=$(lsof -ti :"${DASHBOARD_PORT}" 2>/dev/null || true)
    if [ -n "$existing_pid" ]; then
        log "Killing existing process ${existing_pid} on port ${DASHBOARD_PORT}"
        kill "$existing_pid" 2>/dev/null || true
        sleep 2
        kill -9 "$existing_pid" 2>/dev/null || true
        sleep 1
    fi

    # Start uvicorn
    cd "$SKILL_DIR"
    WHATSAPP_BASE_DIR="$WHATSAPP_BASE_DIR" \
        nohup python3 -m uvicorn src.dashboard.api:app \
        --host 0.0.0.0 \
        --port "$DASHBOARD_PORT" \
        --log-level warning \
        --timeout-keep-alive 120 \
        >> /tmp/dashboard-babloo.log 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"
    log "Dashboard started with PID ${pid}"

    # Wait for it to be ready
    local retries=0
    while [ $retries -lt 15 ]; do
        if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
            log "Dashboard health check passed"
            return 0
        fi
        retries=$((retries + 1))
        sleep 1
    done

    log "ERROR: Dashboard failed to start after 15 seconds"
    return 1
}

export_port() {
    if [ -f /app/export-port.sh ]; then
        /app/export-port.sh "$DASHBOARD_PORT" >> "$LOG_FILE" 2>&1 || true
    fi
}

start_tunnel() {
    log "Starting Cloudflare Tunnel for port ${DASHBOARD_PORT}..."

    # Kill any existing tunnel
    if [ -f "$TUNNEL_PID_FILE" ]; then
        local old_pid
        old_pid=$(cat "$TUNNEL_PID_FILE")
        kill "$old_pid" 2>/dev/null || true
        sleep 1
        kill -9 "$old_pid" 2>/dev/null || true
    fi
    pkill -f "cloudflared.*tunnel.*${DASHBOARD_PORT}" 2>/dev/null || true
    sleep 1

    # Start cloudflared quick tunnel
    npx cloudflared tunnel --url "http://localhost:${DASHBOARD_PORT}" > "$TUNNEL_LOG" 2>&1 &
    local tpid=$!
    echo "$tpid" > "$TUNNEL_PID_FILE"
    log "Cloudflared started with PID ${tpid}"

    # Wait for tunnel URL
    local retries=0
    while [ $retries -lt 30 ]; do
        local url
        url=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            echo "$url" > "$TUNNEL_URL_FILE"
            log "Tunnel URL: ${url}"
            return 0
        fi
        retries=$((retries + 1))
        sleep 2
    done

    log "WARN: Could not get tunnel URL after 60 seconds"
    return 1
}

tunnel_alive() {
    if [ -f "$TUNNEL_PID_FILE" ]; then
        local pid
        pid=$(cat "$TUNNEL_PID_FILE")
        kill -0 "$pid" 2>/dev/null
        return $?
    fi
    return 1
}

health_check() {
    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    [ "$http_code" = "200" ]
}

pid_alive() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        kill -0 "$pid" 2>/dev/null
        return $?
    fi
    return 1
}

# ── Main Loop ──
log "═══ Babloo Watchdog starting ═══"
log "Port: ${DASHBOARD_PORT}, Skill: ${SKILL_DIR}"
log "Base dir: ${WHATSAPP_BASE_DIR}"

restart_failures=0
last_export_time=0

# Initial start
if ! health_check; then
    start_dashboard
    export_port
    last_export_time=$(date +%s)
else
    log "Dashboard already running and healthy"
fi

# Start tunnel if not running
if ! tunnel_alive; then
    start_tunnel
fi

while true; do
    sleep "$CHECK_INTERVAL"

    # Dashboard health check
    if ! health_check; then
        log "WARN: Health check failed"

        if ! pid_alive; then
            log "WARN: Dashboard process is dead"
        fi

        # Try to restart
        if start_dashboard; then
            export_port
            last_export_time=$(date +%s)
            restart_failures=0
            log "Dashboard recovered successfully"
        else
            restart_failures=$((restart_failures + 1))
            log "ERROR: Restart failed (${restart_failures}/${MAX_RESTART_FAILURES})"

            if [ "$restart_failures" -ge "$MAX_RESTART_FAILURES" ]; then
                log "FATAL: Too many restart failures, exiting watchdog"
                exit 1
            fi
        fi
    else
        restart_failures=0
    fi

    # Tunnel health check - restart if dead
    if ! tunnel_alive; then
        log "WARN: Cloudflare tunnel is dead, restarting..."
        start_tunnel
    fi

    # Periodic port re-export
    now=$(date +%s)
    if [ $((now - last_export_time)) -ge "$EXPORT_INTERVAL" ]; then
        log "Re-exporting port (periodic)"
        export_port
        last_export_time=$now
    fi
done
