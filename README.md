# Claude Jail

[![CI](https://github.com/shkrwnd/claude-jail/actions/workflows/ci.yml/badge.svg)](https://github.com/shkrwnd/claude-jail/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

![Claude Jail demo — safe commands pass, destructive commands are denied by host-side policy](docs/demo.gif)

A sandbox for running Claude Code and other AI coding agents safely in Docker. Docker keeps an agent *in* — it doesn't stop it from running `terraform destroy` or `git push --force` with your credentials. Claude Jail adds the missing half: authorization, human approval workflows, temporary credentials, and audit logging for every CLI command the agent runs (`aws`, `gh`, `git`, `kubectl`, `terraform`, `psql`, ...).

## Why Docker Isolation Alone Is Insufficient

Docker containers provide process and filesystem isolation, but they do not provide:

- **Authorization** — a container can execute any CLI tool without policy checks
- **Approval workflows** — no mechanism to require human review before destructive actions
- **Temporary credentials** — static credentials mounted into containers persist beyond need and can be exfiltrated
- **Audit logging** — no centralized record of what an agent executed, when, and why

This project bridges the gap by intercepting tool calls at the CLI level, routing them through a pluggable authorization backend, and executing commands on the host where credentials live. No secrets ever enter the container.

## Architecture

```
CONTAINER (untrusted)                         HOST (trusted)
┌──────────────────────────┐                 ┌──────────────────────────────┐
│ Claude Code                │                 │ Execution Server              │
│   ↓                        │                 │  (server/main.py)             │
│ CLI Wrapper (aws/gh/git..) │                 │  1. Verify shared token       │
│   ↓                        │  localhost TCP  │  2. Auth backend (pluggable)  │
│ agent-exec ────────────────┼────────────────▶│  3. Fetch temp credentials    │
│                            │                 │  4. Execute real binary       │
│ Never sees: credentials,   │◀────────────────┤  5. Return stdout/stderr      │
│ approval tokens, policy    │  {stdout,stderr │                               │
│                            │   exit_code}    │ Reads: server.env             │
└──────────────────────────┘                 └──────────────────────────────┘
```

The intent: **bypassing the sandbox must gain nothing.** If the agent deletes the wrappers or opens the TCP connection itself, it just reaches the same execution server, which authorizes on the host using policy the container cannot see or influence. Details in [docs/architecture.md](docs/architecture.md).

## Quick Start

```bash
./start.sh        # starts the execution server, builds/starts the container, opens a shell
./start.sh stop   # tears everything down
./start.sh logs   # tail the execution server log
```

On first run the script creates all config files, starts the execution server on the host, builds/starts the container, and drops you into a container shell — just run `claude` there. It works exactly as it does locally: without an `ANTHROPIC_API_KEY` it prompts for browser login (the token persists in the `claude` volume), and any call to `aws`, `az`, `gh`, `git`, `kubectl`, `terraform`, or `psql` is automatically intercepted and routed through the execution server.

## Configuration

All config lives in `deploy/`, auto-created on first run and never committed. Defaults live in the committed `deploy/*.defaults.env` files — don't edit those, override them in the files below.

| File | Contains |
|---|---|
| `deploy/docker-compose.override.yml` | Your workspace folders (see below) |
| `deploy/container.env` | Optional `ANTHROPIC_API_KEY` (otherwise browser login on first run) |
| `deploy/server.env` | Auth backend credentials — never enters the container. Default: `allow_all` (dev only) |

### Workspace folders (the override file)

By default the container mounts one folder as `/workspace`. To give Claude
Code access to your folders, add volume lines to the gitignored
`deploy/docker-compose.override.yml`. Each line means
`host-path:container-path:mode`:

```yaml
services:
  claude:
    volumes:
      - /path/to/project:/workspace/project:rw        # rw: Claude can edit
      - /path/to/docs:/workspace/docs:ro              # ro: read-only
```

Inside the container, Claude sees your folders under `/workspace/`. The
execution server reads the same file to map container paths back to host
paths — when Claude runs `git` in `/workspace/project`, the real command
executes in `/path/to/project` on the host. No other configuration is
needed. After editing any config, re-run `./start.sh` to apply it.

## Auth Backends

The default backend is `allow_all` — every command is approved and runs with the host environment, no policy checks and no credential brokering. Fine for local development; for anything real, choose an authorization backend via `AUTH_BACKEND` in `deploy/server.env`. The container has no say in this, and switching backends never requires rebuilding the image:

| Backend | Description |
|---------|-------------|
| `allow_all` | Approve everything — development and CI only (default) |
| `webhook` | Generic HTTP webhook — any custom approval service |
| `server.auth_backends.static_policy.StaticPolicyBackend` | Static allow/deny list — the demo policy |
| any dotted path | Your own backend class — see below |

The server fails at startup with a clear error if the chosen backend is missing required credentials.

**Extending:** a custom backend is one Python class selected by its dotted path — no registry edit, no rebuild. Adding a wrapper for another CLI is a three-line shim. Both are walked through in [docs/extending.md](docs/extending.md), along with how backends inject temporary per-action credentials.

## Security

- **Credential isolation** — the container image contains only the wrappers and the protocol client; auth backend selection and credentials live in `deploy/server.env` on the host, never mounted into the container
- **Request authentication** — the server only accepts requests carrying the shared secret from `deploy/exec.token`; other processes or containers reaching `127.0.0.1:8377` are rejected
- **Egress filtering** — the container lives on an `internal: true` Docker network with no direct internet access; all outbound HTTPS goes through a tinyproxy allowlist (`deploy/sidecar/allowlist`). Exfiltrating stolen tokens to arbitrary domains is blocked
- **Loopback only** — the execution server binds to 127.0.0.1; the container reaches it via a socat relay in the sidecar. Never exposed to the network
- **Container hardening** — `cap_drop: ALL`, `no-new-privileges`, seccomp profile, memory/pid limits, non-root user (UID 1000)
- **Never mount** `~/.aws`, `~/.ssh`, `~/.kube`, `~/.docker`, `~/.config/gh` — all credentials are managed by the execution server on the host

Threat model and known gaps: [SECURITY.md](SECURITY.md); planned mitigations in [TODO.md](TODO.md).

## Documentation

- [docs/architecture.md](docs/architecture.md) — trust model, request lifecycle, execution protocol, design decisions
- [docs/extending.md](docs/extending.md) — custom auth backends, new CLI wrappers, temporary credentials
- [SECURITY.md](SECURITY.md) — threat model and reporting
- [CONTRIBUTING.md](CONTRIBUTING.md) — ground rules and running tests

## Testing

```bash
PYTHONPATH=. python3 -m unittest discover tests -v
```
