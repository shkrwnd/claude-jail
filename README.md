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
│ CLI Wrapper (aws/gh/git..) │                 │                               │
│   ↓                        │                 │  1. Verify shared token       │
│ agent-exec                 │                 │  2. Auth backend (pluggable)  │
│   ↓                        │  localhost TCP  │  3. Fetch temp credentials    │
│ exec_client.py ────────────┼────────────────▶│  4. Execute real binary       │
│                            │                 │  5. Return stdout/stderr      │
│ Never sees:                │◀────────────────┤                               │
│  - credentials             │  {stdout,stderr │ Reads: server.env             │
│  - approval tokens         │   exit_code}    │ (auth backend credentials)    │
│  - auth backend config     │                 │                               │
└──────────────────────────┘                 └──────────────────────────────┘
```

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

`docker-compose.override.yml` is Docker Compose's standard mechanism for local
customization: compose merges it on top of the base `docker-compose.yml`, so
the shared config stays in git while your machine-specific mounts live in the
override (gitignored — your local paths are never committed). `start.sh`
passes both files to compose automatically.

By default the container mounts one folder as `/workspace`. To give Claude
Code access to your folders, add volume lines to the override file. Each line
means `host-path:container-path:mode`:

```yaml
services:
  claude:
    volumes:
      - /path/to/project:/workspace/project:rw        # rw: Claude can edit
      - /path/to/another-repo:/workspace/another-repo:rw
      - /path/to/docs:/workspace/docs:ro              # ro: read-only
```

- **host path** (left) — the real folder on your machine
- **container path** (right) — where it appears inside the jail; always under `/workspace/`
- **mode** — `rw` lets Claude modify files, `ro` makes them read-only

Inside the container, Claude sees all your folders under `/workspace/`.
The execution server reads the same override file to map container paths
back to host paths — so when Claude runs e.g. `git` in `/workspace/project`,
the real command executes in `/path/to/project` on the host. No other
configuration is needed.

After editing any config, re-run `./start.sh` to apply it.

## Inside the Container

What Claude actually gets is a deliberately minimal environment:

- **A non-root user** (`agent`, UID 1000) in a hardened container: all capabilities dropped, `no-new-privileges`, seccomp profile, memory/pid limits. No sudo, no way to escalate.
- **Your workspace folders** under `/workspace/` — the only host filesystem it can touch, with exactly the read/write access you granted per folder.
- **Claude Code + everyday dev tools** (node, python3) that run locally and freely — editing files, running tests, local builds all work at full speed with no interception.
- **Wrappers instead of real CLIs**: `/opt/agent/bin` (root-owned, read-only) sits first on `PATH`, so `aws`, `az`, `gh`, `git`, `kubectl`, `terraform`, `psql` invoke thin shims that forward the command to the host. Claude Code needs no special configuration — interception is transparent, commands behave as if they ran locally.
- **No secrets**: no cloud credentials, no SSH keys, no auth backend config anywhere in the image or environment. The only sensitive value is the exec token, which grants exactly one thing — the right to *ask* the host to run a command.

The intent: **bypassing the sandbox must gain nothing.** If the agent deletes the wrappers or opens the TCP connection itself, it just reaches the same execution server, which authorizes on the host using policy the container cannot see or influence. There are no credentials inside to steal and no auth code inside to tamper with. Out of the box you get policy-checked, audit-logged tool execution with temporary credentials — without configuring Claude Code at all.

## How It Works

Each wrapper is a small script that delegates to `agent-exec`, which forwards the command to the host via localhost TCP (`exec_client.py`). The container knows nothing about authorization — it only speaks the execution protocol: send `{ tool, args, reason, repo, branch }`, receive `{ stdout, stderr, exit_code }`.

The execution server on the host:

1. Verifies the shared secret (`X-Exec-Token`, generated into `deploy/exec.token`)
2. Maps the container workspace path back to the host path
3. Authorizes via the pluggable auth backend
4. On approval: fetches temporary credentials and executes the real binary
5. On denial: returns an error message; the command never executes

### Auth Backends

The default backend is `allow_all` — every command is approved and runs with the host environment, no policy checks and no credential brokering. Fine for local development; for anything real, choose an authorization backend via `AUTH_BACKEND` in `deploy/server.env`. The container has no say in this, and switching backends never requires rebuilding the image:

| Backend | Description |
|---------|-------------|
| `allow_all` | Approve everything — development and CI only (default) |
| `webhook` | Generic HTTP webhook — any custom approval service |

The server fails at startup with a clear error if the chosen backend is missing required credentials.

### Writing a Custom Backend

A backend is one class with one required method: `authorize()` receives the
tool name, the full command string, and the raw args, and returns an
`AuthDecision` — `"approved"` means the host executes the real binary,
anything else means the container gets an error and nothing runs. Backends
live only on the host, so the agent can neither read nor influence your
policy.

Here is a complete backend that enforces a static allow/deny list
(`server/auth_backends/base.py` defines the interface). It ships in the repo
as `server/auth_backends/static_policy.py`, so you can try it immediately:

```python
# server/auth_backends/static_policy.py
from server.auth_backends.base import AuthBackend, AuthDecision

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

class StaticPolicyBackend(AuthBackend):
    def authorize(self, tool, command, args, reason="", repo="", branch="") -> AuthDecision:
        if any(command.startswith(prefix) for prefix in DENIED):
            return AuthDecision(status="denied", reason=f"{command!r} is on the deny list")
        if any(command.startswith(prefix) for prefix in ALLOWED):
            return AuthDecision(status="approved")
        return AuthDecision(status="denied", reason=f"{command!r} is not on the allow list")
```

How it works:

- `command` is the full string (e.g. `"git push --force origin main"`), so
  prefix matching gives you simple subcommand-level rules.
- Deny is checked **first**, and anything unlisted is denied — always fail
  closed, so a command you never thought about cannot slip through.
- The optional `execution_env(tool, decision)` method controls the
  environment of the executed command; override it to inject temporary
  credentials (the default passes the host environment through).

Select it in `deploy/server.env` by its dotted path — no registry edit needed:

```bash
# deploy/server.env
AUTH_BACKEND=server.auth_backends.static_policy.StaticPolicyBackend
```

(Any `AUTH_BACKEND` value containing a `.` is treated as a dotted import
path, so your backend can live in any importable package.) If your backend
needs configuration, declare it as a `required_env` class attribute —
`required_env = ("MY_API_KEY",)` — and the server refuses to start with a
clear error when it's missing, instead of failing on the first request.

Restart the server (`./start.sh stop && ./start.sh`) — no container rebuild
needed. A word of caution: prefix matching is a demonstration, not a security
boundary — e.g. `git -c core.sshCommand=... push` doesn't start with
`git push`, and `kubectl get` also matches `kubectl get secrets`. Robust
policies must inspect `args`, not just the command string.

### Adding a New Wrapper

A wrapper is a three-line shim. To intercept another CLI (say `docker`),
create `wrappers/docker`:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/agent-exec" "docker" "$@"
```

Rebuild the container (`./start.sh`) and every `docker` invocation is now
routed through the execution server — your auth backend sees it as
`tool="docker"` with the full argument list. The real binary must exist on
the **host** PATH (commands execute there, not in the container).

### Temporary Credentials

No static credentials are mounted into the container. On each authorized action, the backend can inject short-lived credentials as environment variables for that subprocess only. How much of this happens depends on the configured backend: the default `allow_all` injects nothing (commands use the host environment as-is); a production backend fetches credentials scoped to the approved action:

| Tool | Credentials |
|------|------------|
| aws | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` |
| gh | `GH_TOKEN`, `GITHUB_TOKEN` |
| git | None — uses SSH/HTTPS configured on the host |
| kubectl | `KUBECONFIG` pointing at a temporary kubeconfig file |
| terraform | AWS env vars + `TF_TOKEN_app_terraform_io` |
| psql | `PGPASSWORD`, `PGUSER`, `PGHOST`, `PGDATABASE` |

## Security

- **Credential isolation** — the container image contains only the wrappers and the protocol client; auth backend selection and credentials live in `deploy/server.env` on the host, never mounted into the container
- **Request authentication** — the server only accepts requests carrying the shared secret from `deploy/exec.token`; other processes or containers reaching `127.0.0.1:8377` are rejected
- **Loopback only** — the server binds to 127.0.0.1; the container reaches it via `host.docker.internal`. Never exposed to the network
- **Container hardening** — `cap_drop: ALL`, `no-new-privileges`, seccomp profile, memory/pid limits, non-root user (UID 1000)
- **Never mount** `~/.aws`, `~/.ssh`, `~/.kube`, `~/.docker`, `~/.config/gh` — all credentials are managed by the execution server on the host

Known gaps and planned mitigations are tracked in `TODO.md` (host-side execution hardening, egress enforcement).

## Testing

```bash
PYTHONPATH=. python3 -m unittest discover tests -v
```

## Project Structure

```
├── start.sh                   # One-command entry point: server + container + shell
├── wrappers/                  # CONTAINER SIDE — the only code shipped into the image
│   ├── agent-exec             # Dispatcher: forwards tool calls to the host
│   ├── exec_client.py         # Protocol client: localhost TCP (stdlib-only)
│   └── aws, az, gh, git, kubectl, terraform, psql   # CLI wrappers
├── server/                    # HOST SIDE — auth, credentials, execution
│   ├── main.py                # Execution server (localhost TCP listener, token auth)
│   ├── handler.py             # Map paths → authorize → build env → run real binary
│   ├── auth_backends/         # Pluggable authorization backends
│   │   ├── base.py            # AuthBackend ABC + AuthDecision
│   │   ├── allow_all.py       # Approve everything (dev/CI)
│   │   └── webhook.py         # Generic HTTP webhook
│   └── ...                    # Additional backend clients
├── deploy/                    # Docker/compose/env config
│   ├── docker-compose.yml     # Base compose config (hardening, mounts, env)
│   ├── Dockerfile             # Agent container image
│   ├── container.defaults.env # Committed container defaults (user overrides: container.env)
│   ├── server.defaults.env    # Committed server defaults (user overrides: server.env)
│   ├── docker-compose.override.example.yml  # Template for workspace mounts
│   └── create-configs.sh      # Auto-creates missing user config files
├── tests/
└── TODO.md                    # Known gaps: execution hardening, egress enforcement
```
