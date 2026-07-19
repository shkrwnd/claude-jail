# Agent container: Claude Code CLI + tool wrappers that forward to the
# host-side execution server.

FROM node:20-slim

# Install system dependencies needed by the wrappers and Claude Code
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code globally
RUN npm install -g @anthropic-ai/claude-code

# Install wrappers — read-only to prevent Claude from modifying them.
# This is ALL the container gets: shell shims + agent-exec + exec_client.py.
# No auth backends, no SDK, no credentials — the container only knows the
# execution protocol (JSON over localhost TCP). Auth is the host's problem.
COPY wrappers/ /opt/agent/bin/
RUN chmod +x /opt/agent/bin/* \
    && chmod -w /opt/agent/bin/* \
    && chown -R root:root /opt/agent/bin/

# Execution server endpoint — TCP to the host (overridable via docker-compose)
ENV EXEC_SERVER="host.docker.internal:8377"

# Wrappers must come first in PATH so they intercept tool calls
ENV PATH="/opt/agent/bin:${PATH}"

# Security: run as non-root with fixed UID/GID for consistent volume permissions.
# node:20-slim ships a "node" user already occupying UID/GID 1000 — remove it first.
RUN userdel -r node \
    && groupadd -g 1000 agent && useradd -u 1000 -g 1000 -m -s /bin/bash agent

# Global Claude Code instructions: teaches the agent to set AGENT_REASON and
# how to handle PENDING APPROVAL / DENIED responses, in every workspace.
# Copied into ~/.claude/CLAUDE.md at container start (see docker-compose.yml)
# because the persistent "claude" volume shadows anything baked in there.
COPY --chown=1000:1000 deploy/agent-claude.md /opt/agent/CLAUDE.md

USER agent

WORKDIR /workspace

# Required at runtime:
#   ANTHROPIC_API_KEY — passed via container.env (optional; browser login otherwise)
#   EXEC_SERVER       — host:port of the execution server on the host
