# Claude Jail

[![CI](https://github.com/shkrwnd/claude-jail/actions/workflows/ci.yml/badge.svg)](https://github.com/shkrwnd/claude-jail/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

![Claude Jail demo — safe commands pass, destructive commands are denied by host-side policy](docs/demo.gif)

A sandbox for running Claude Code and other AI coding agents safely in Docker. Docker keeps an agent *in* — it doesn't stop it from running `terraform destroy` or `git push --force` with your credentials. Claude Jail adds the missing half: authorization, credential isolation, and audit logging for every CLI command the agent runs (`aws`, `gh`, `git`, `kubectl`, `terraform`, `psql`, ...).

## Usage

Everything is managed through `start.sh`:

| Command | Description |
|---------|-------------|
| `./start.sh` | Start the execution server + container and drop into a shell |
| `./start.sh stop` | Stop the container and execution server |
| `./start.sh clean` | Stop + delete all project images, volumes, and orphan containers |
| `./start.sh logs` | Tail the execution server log |
| `./start.sh --help` | Show available commands |

Once inside the container, run `claude` to start Claude Code. All CLI commands (aws, git, kubectl, etc.) are intercepted by wrappers and routed to the host-side execution server for authorization. To add support for other CLI tools, please [open an issue or PR](https://github.com/shkrwnd/claude-jail/issues).

### Mounting your project folders

Add volume lines to the gitignored `deploy/docker-compose.override.yml` to give Claude Code access to your folders:

```yaml
services:
  claude:
    volumes:
      - /path/to/project:/workspace/project:rw        # rw: Claude can edit
      - /path/to/docs:/workspace/docs:ro              # ro: read-only
```

Inside the container, Claude sees your folders under `/workspace/`. The execution server maps container paths back to host paths automatically — no other configuration needed. Only folders explicitly listed in this file are accessible to Claude Code — nothing else from the host is mounted into the container.

### Egress allowlist

The container has no direct internet access. All outbound HTTPS goes through a domain-filtered proxy. Edit `deploy/sidecar/allowlist` to control which domains the container can reach:

```
# Currently allowed — one regex per line
^.*\.anthropic\.com(:[0-9]+)?$    # Claude API
^.*\.claude\.com(:[0-9]+)?$       # Claude platform
^sentry\.io(:[0-9]+)?$            # Error reporting
^pypi\.org(:[0-9]+)?$             # Python packages
^registry\.npmjs\.org(:[0-9]+)?$  # npm packages
^github\.com(:[0-9]+)?$           # GitHub
^api\.github\.com(:[0-9]+)?$      # GitHub API
```

After editing, restart with `./start.sh stop && ./start.sh` to apply.

### Authorization backend

By default all commands are approved (`allow_all`). To control what the agent can execute, set a policy in `deploy/server.env`:

```
AUTH_BACKEND=server.auth_backends.static_policy.StaticPolicyBackend
```

The included static policy backend uses simple allow/deny lists:

```python
ALLOWED = (
    "git status", "git log", "git diff", "git commit",
    "aws s3 ls",
    "kubectl get",
)
DENIED = (
    "git push --force",
    "terraform destroy",
    "kubectl delete",
)
```

Commands matching `DENIED` are rejected, commands matching `ALLOWED` pass through, everything else is denied (fail-closed). Edit `server/auth_backends/static_policy.py` to customize.

To authorize with your own backend (REST API, Slack bot, OPA, etc.), extend `AuthBackend`:

```python
from server.auth_backends.base import AuthBackend, AuthDecision

class MyBackend(AuthBackend):
    required_env = ("MY_API_KEY",)  # checked at startup

    def authorize(self, tool, command, args, reason="", repo="", branch=""):
        ok = call_my_api(tool, command)
        return AuthDecision(status="approved" if ok else "denied",
                            reason="Policy check failed")
```

Set `AUTH_BACKEND=my_module.MyBackend` in `deploy/server.env` and restart. See [docs/extending.md](docs/extending.md) for details.

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

## Configuration

All config lives in `deploy/`, auto-created on first run and never committed. Defaults live in the committed `deploy/*.defaults.env` files — don't edit those, override them in the files below.

| File | Contains |
|---|---|
| `deploy/docker-compose.override.yml` | Your workspace folders (see above) |
| `deploy/container.env` | Optional `ANTHROPIC_API_KEY` (otherwise browser login on first run) |
| `deploy/server.env` | Auth backend credentials — never enters the container. Default: `allow_all` (dev only) |
| `deploy/sidecar/allowlist` | Egress proxy domain allowlist (see above) |

## Auth Backends

The default backend is `allow_all` — every command is approved and runs with the host environment, no policy checks and no credential brokering. Fine for local development; for anything real, choose an authorization backend via `AUTH_BACKEND` in `deploy/server.env`. The container has no say in this, and switching backends never requires rebuilding the image:

| Backend | Description |
|---------|-------------|
| `allow_all` | Approve everything — development and CI only (default) |
| `webhook` | Generic HTTP webhook — any custom approval service |
| `server.auth_backends.static_policy.StaticPolicyBackend` | Static allow/deny list — the demo policy |
| any dotted path | Your own backend class — see [docs/extending.md](docs/extending.md) |

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
