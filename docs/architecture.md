# Architecture

Internals of Claude Jail: how a tool call travels from the container to the
host, and where the trust boundaries sit. Read the README first for the
user-facing overview; this document is for people extending or auditing the
system.

## Trust Model

Two zones with a hard boundary between them:

| Zone | Code | Trust | Contains |
|------|------|-------|----------|
| Container | `wrappers/` | Untrusted (agent-controlled) | CLI shims, `agent-exec`, `exec_client.py` — stdlib-only, auth-agnostic |
| Host | `server/` | Trusted | Execution server, auth backends, credentials, path mapping |

Rules that keep the boundary intact:

- `wrappers/` never imports anything from `server/`. The container ships only
  `wrappers/` (see `deploy/Dockerfile`).
- The container knows exactly one protocol: JSON over localhost TCP. It has
  no knowledge of which auth backend is configured, what policies exist, or
  what credentials the host holds.
- Bypassing the wrappers gains nothing: an agent that opens the TCP
  connection directly just reaches the same server, which authorizes on the
  host using policy the container cannot see or influence.
- The only sensitive value in the container is `EXEC_TOKEN`, which grants a
  single right: the right to *ask* the host to run a command.

## Request Lifecycle

What happens when Claude runs `git push` inside the container:

```
CONTAINER                                      HOST
─────────                                      ────
git push
  │  PATH resolves "git" to /opt/agent/bin/git (wrapper shim)
  ▼
wrappers/git
  │  exec agent-exec git push
  ▼
agent-exec
  │  collects context: cwd (repo), current branch, AGENT_REASON
  ▼
exec_client.py
  │  POST http://host.docker.internal:8377/execute
  │  headers: X-Exec-Token: <EXEC_TOKEN>
  │  body: { tool, args, reason, repo, branch }
  ▼─────────────────────────────────────────▶ server/main.py
                                               │  1. hmac.compare_digest token check
                                               │     (401 fail-closed on mismatch)
                                               ▼
                                             server/handler.py
                                               │  2. map /workspace/... → host path
                                               │  3. backend.authorize(...)
                                               │     denied → error, nothing runs
                                               │  4. env = backend.execution_env(...)
                                               │     (temp credentials, if any)
                                               │  5. subprocess.run(real binary)
  ◀─────────────────────────────────────────── │  6. { stdout, stderr, exit_code }
  │
  ▼
stdout/stderr printed, exit code propagated — the command behaves
as if it ran locally.
```

## The Execution Protocol

Single endpoint: `POST /execute` on `127.0.0.1:8377`.

Request body:

| Field | Meaning |
|-------|---------|
| `tool` | Wrapper name (`git`, `aws`, `kubectl`, ...) |
| `args` | Argument list, unmodified |
| `reason` | Optional free-text intent from `AGENT_REASON` |
| `repo` | Container cwd (`/workspace/...`) |
| `branch` | Current git branch, if any |

Response: `{ stdout, stderr, exit_code }`. Nothing else crosses the
boundary — no credentials, no policy details.

Authentication: the server only accepts requests carrying the shared secret
from `deploy/exec.token` (`X-Exec-Token` header, compared with
`hmac.compare_digest`). The token reaches the container via compose
`env_file`, and the server refuses to start without one. This stops other
local processes or containers from using the execution server.

`GET /health` is unauthenticated and returns `{"status": "ok"}`.

## Path Mapping

The container sees `/workspace/...`; the host must run commands in the real
directories. `server/handler.py` builds the mapping from
`deploy/docker-compose.override.yml` — the same file that defines the
mounts — by parsing lines of the form:

```yaml
- /host/path:/workspace/name:rw
```

Translation is longest-prefix: `/workspace/project/sub` maps through the
`/workspace/project` mount. A request for a container path with no mapping
fails loudly rather than executing in the wrong directory.

## Auth Backends

A backend is one class implementing `server/auth_backends/base.py`:

```python
class AuthBackend(ABC):
    def authorize(self, tool, command, args, reason="", repo="", branch="") -> AuthDecision: ...
    def execution_env(self, tool, decision) -> dict[str, str]:  # optional override
        return dict(os.environ)
```

- `authorize` returns `AuthDecision(status=...)`; only `"approved"` executes.
  Anything else returns the decision's `reason` to the container as an error.
- `execution_env` supplies the subprocess environment — override it to inject
  short-lived, action-scoped credentials instead of the host environment.

Backends are registered in `server/auth_backends/__init__.py` as lazy dotted
paths (`REGISTRY["name"] = "module.ClassName"`) and selected via
`AUTH_BACKEND` in `deploy/server.env`. The default is `allow_all`. At
startup, `server/main.py` validates that the selected backend's required env
vars (`_REQUIRED_ENV`) are present and exits with a clear error if not —
misconfiguration fails at boot, not at first request.

The README's "Writing a Custom Backend" section has a worked example.

## Configuration Layering

Two kinds of config files in `deploy/`:

- **Committed defaults** — `server.defaults.env`, `container.defaults.env`,
  `docker-compose.yml`. Never edited by users; updated by `git pull`.
- **Generated user files** — `server.env`, `container.env`,
  `docker-compose.override.yml`, `exec.token`. Created by
  `deploy/create-configs.sh` (run automatically by `start.sh`), gitignored,
  sparse: they contain only the user's overrides.

Precedence, first-set-wins on the server: real environment / CLI flags →
`server.env` → `server.defaults.env`. In the container, compose `env_file`
ordering achieves the same layering. Because user files hold only deltas,
new defaults added upstream take effect without any migration.

## Design Decisions

**Why TCP, not a Unix socket.** Docker Desktop (macOS/Windows) cannot
reliably share Unix sockets between host and container — bind-mounted
sockets hit stale inodes and `EOPNOTSUPP`. Loopback TCP with a shared-secret
token works identically everywhere. The server binds `127.0.0.1` only and is
never exposed to the network.

**Why execute on the host.** Credentials must never enter the container, so
the command that needs them must run where they live. The trade-off is that
host execution of agent-controlled workspaces is itself an escape vector
(e.g. malicious `.git/config`, git hooks, terraform providers) — this is a
known gap with a layered mitigation plan tracked in `TODO.md`.

**Why the image is auth-ignorant.** Keeping every line of auth logic out of
the container means switching or reconfiguring backends never requires an
image rebuild, and there is nothing inside for the agent to read or tamper
with. The import boundary (`wrappers/` never imports `server/`) is what
makes this guarantee checkable.

## Code Map

| Path | Role |
|------|------|
| `wrappers/<tool>` | Shell shims; delegate to `agent-exec` |
| `wrappers/agent-exec` | Collects context (cwd, branch, reason), calls the client |
| `wrappers/exec_client.py` | Protocol client — stdlib-only HTTP over TCP |
| `server/main.py` | HTTP listener, token auth, env layering, startup validation |
| `server/handler.py` | Path mapping → authorize → build env → run real binary |
| `server/auth_backends/base.py` | `AuthBackend` ABC + `AuthDecision` |
| `server/auth_backends/allow_all.py` | Approve everything (dev/CI default) |
| `server/auth_backends/webhook.py` | POST decisions to a custom approval service |
| `deploy/` | Dockerfile, compose files, layered env config, token |
| `start.sh` | Entry point: configs → server → container → shell |
