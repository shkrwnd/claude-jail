# Extending Claude Jail

How to add your own authorization policy, intercept more CLIs, and inject
temporary credentials. See [architecture.md](architecture.md) for the
internals these extension points plug into.

## Writing a Custom Backend

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

## Adding a New Wrapper

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

## Temporary Credentials

No static credentials are mounted into the container. On each authorized
action, the backend can inject short-lived credentials as environment
variables for that subprocess only — override `execution_env()` to do it.
How much of this happens depends on the configured backend: the default
`allow_all` injects nothing (commands use the host environment as-is); a
production backend fetches credentials scoped to the approved action:

| Tool | Credentials |
|------|------------|
| aws | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` |
| gh | `GH_TOKEN`, `GITHUB_TOKEN` |
| git | None — uses SSH/HTTPS configured on the host |
| kubectl | `KUBECONFIG` pointing at a temporary kubeconfig file |
| terraform | AWS env vars + `TF_TOKEN_app_terraform_io` |
| psql | `PGPASSWORD`, `PGUSER`, `PGHOST`, `PGDATABASE` |
