#!/usr/bin/env bash
# start.sh — bring up the whole stack in the right order.
#
#   ./start.sh          start execution server + container, drop into a shell
#   ./start.sh stop     stop container and execution server
#   ./start.sh logs     tail the execution server log
#
# The server listens on TCP 127.0.0.1:$PORT; the container reaches it via
# host.docker.internal. (Unix sockets can't be reliably shared between a macOS
# host and containers — Docker Desktop limitation.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The override file holds the user's workspace mounts (auto-created from its
# template by create-configs.sh). With explicit -f, compose does not auto-load
# it, so pass it explicitly.
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml"
         -f "$ROOT/deploy/docker-compose.override.yml")
PORT="${EXEC_PORT:-8377}"
SERVER_LOG="/tmp/agent-exec-server.log"

log() { echo "[start] $*"; }

# Find the server by its command line — no pid file to go stale.
server_pids() { pgrep -f "[Pp]ython.* server/main.py" 2>/dev/null || true; }

server_running() { [[ -n "$(server_pids)" ]]; }

server_healthy() { curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

stop_server() {
    if server_running; then
        log "Stopping execution server (pid $(server_pids | tr '\n' ' '))..."
        pkill -f "[Pp]ython.* server/main.py" 2>/dev/null || true
    fi
}

# ── start steps ──────────────────────────────────────────────────────────────

start_execution_server() {
    if server_running; then
        log "Execution server already running (pid $(server_pids | tr '\n' ' '))."
        return
    fi

    log "Starting execution server..."
    (cd "$ROOT" && nohup python3 server/main.py --port "$PORT" > "$SERVER_LOG" 2>&1 &)

    # Wait up to 5s for the server to answer /health
    for _ in $(seq 1 20); do
        server_healthy && break
        sleep 0.25
    done
    if ! server_healthy; then
        log "ERROR: Server not responding on 127.0.0.1:$PORT. Log:"
        tail -20 "$SERVER_LOG"
        exit 1
    fi
    log "Execution server up (pid $(server_pids), port $PORT, log: $SERVER_LOG)."
}

start_container() {
    log "Building and starting container..."
    "${COMPOSE[@]}" up -d --build
}

open_container_shell() {
    log "Opening shell in container (run 'claude' inside; exit leaves stack running)."
    log "To stop everything later: ./start.sh stop"
    exec "${COMPOSE[@]}" exec claude bash
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_start() {
    start_execution_server
    start_container
    open_container_shell
}

cmd_stop() {
    log "Stopping container..."
    "${COMPOSE[@]}" down
    stop_server
    log "Done."
}

cmd_logs() {
    exec tail -f "$SERVER_LOG"
}

# Ensure all config files exist (incl. the override file COMPOSE references)
# before any command runs.
sh "$ROOT/deploy/create-configs.sh"

case "${1:-start}" in
    start) cmd_start ;;
    stop)  cmd_stop ;;
    logs)  cmd_logs ;;
    *)     echo "Usage: ./start.sh [start|stop|logs]"; exit 2 ;;
esac
