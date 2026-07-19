#!/usr/bin/env sh
# create-configs.sh — creates missing user config files in deploy/.
# Never overwrites existing files.
#
# User files hold only overrides and secrets; defaults live in the committed
# *.defaults.env files and evolve with the repo (no manual merging needed).
#
# Called by start.sh; can also be run standalone before `docker compose up`:
#   sh deploy/create-configs.sh
set -e

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"

log() { echo "[create-configs] $*"; }

create_from_template() {
  name="$1"; template="$2"; note="$3"
  if [ ! -f "$DEPLOY_DIR/$name" ]; then
    cp "$DEPLOY_DIR/$template" "$DEPLOY_DIR/$name"
    log "Created deploy/$name from template. $note"
  fi
}

create_from_template "docker-compose.override.yml" "docker-compose.override.example.yml" \
  "Add folders there to make them available to Claude under /workspace/."

if [ ! -f "$DEPLOY_DIR/container.env" ]; then
  cat > "$DEPLOY_DIR/container.env" <<'EOF'
# Your container overrides (gitignored). Defaults: deploy/container.defaults.env
# Anything here is readable by the agent — no auth backend credentials.

# Claude Code API key (optional). Leave unset to log in via the browser on
# first run instead (token persists in the claude volume across rebuilds).
# ANTHROPIC_API_KEY=sk-ant-...

# Host folder mounted as /workspace (default: <repo root>/workspace)
# WORKSPACE_DIR=/Users/you/projects/myproject
EOF
  log "Created deploy/container.env. ANTHROPIC_API_KEY is optional — without it you'll log in via browser on first 'claude' run."
fi

if [ ! -f "$DEPLOY_DIR/server.env" ]; then
  cat > "$DEPLOY_DIR/server.env" <<'EOF'
# Your execution-server overrides and secrets (gitignored).
# Defaults: deploy/server.defaults.env — set only what differs.
#
# The default backend is allow_all (approves everything — dev only).
# To use Outhora, uncomment and fill in:
# AUTH_BACKEND=outhora
# OUTHORA_AGENT_ID=agent_...
# OUTHORA_AGENT_SECRET=...
# OUTHORA_DEPT_ID=...
#
# Or a generic webhook:
# AUTH_BACKEND=webhook
# AUTH_WEBHOOK_URL=https://your-approval-service.example.com
# AUTH_WEBHOOK_TOKEN=...
EOF
  log "Created deploy/server.env. Fill in auth backend credentials to move beyond allow_all."
fi

# Shared secret between the execution server and the container. Every /execute
# request must present it — stops other processes/containers from driving the
# server. Dotenv format: compose injects it into the container as EXEC_TOKEN,
# and the server loads the same file.
if [ ! -f "$DEPLOY_DIR/exec.token" ]; then
  umask 077
  echo "EXEC_TOKEN=$(openssl rand -hex 32)" > "$DEPLOY_DIR/exec.token"
  log "Generated deploy/exec.token (shared secret for the execution server)."
fi
