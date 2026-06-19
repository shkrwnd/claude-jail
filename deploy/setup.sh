#!/usr/bin/env sh
# setup.sh — auto-called by docker compose before the main service starts.
# Creates config files from their templates if they don't already exist.
set -e

DEPLOY_DIR="/config"         # mounted from deploy/
PROJECT_ROOT="/project-root" # mounted from repo root

# --- egress-config.json (lives in deploy/) ---
if [ ! -f "$DEPLOY_DIR/egress-config.json" ]; then
  cp "$DEPLOY_DIR/egress-config.example.json" "$DEPLOY_DIR/egress-config.json"
  echo "[setup] Created egress-config.json from template."
  echo "[setup] Edit deploy/egress-config.json to customize allowed domains."
else
  echo "[setup] egress-config.json already exists — skipping."
fi

# --- .env (lives at project root) ---
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  cp "$DEPLOY_DIR/.env.example" "$PROJECT_ROOT/.env"
  echo "[setup] Created .env from template. Fill in your API keys before starting."
else
  echo "[setup] .env already exists — skipping."
fi
