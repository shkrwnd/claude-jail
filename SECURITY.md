# Security Policy

## Reporting a Vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/shkrwnd/claude-jail/security/advisories/new)
rather than opening a public issue. You should get a response within a few
days. Please include reproduction steps and which trust boundary (below) you
believe is broken.

## Threat Model

Claude Jail assumes the **agent in the container is adversarial** and the
**host is trusted**. The security claims are:

1. **No credentials in the container** — the image and container environment
   contain no cloud credentials, SSH keys, or auth backend config. The only
   sensitive value is `EXEC_TOKEN`, which grants exactly one right: asking
   the host to run a command (which the host then authorizes independently).
2. **Policy cannot be influenced from inside** — auth backend selection and
   policy live in `deploy/server.env` on the host. Bypassing the wrappers
   (deleting them, opening the TCP connection directly) reaches the same
   server enforcing the same policy.
3. **The execution server is not reachable from the network** — it binds
   `127.0.0.1` only and rejects requests without the shared token.

A report that breaks any of these claims is a vulnerability.

## Known Gaps (out of scope for reports)

These are documented, accepted limitations with mitigation plans tracked in
[TODO.md](TODO.md):

- **Host-side execution of agent-controlled workspaces** — approved commands
  run on the host with `cwd` inside an agent-writable workspace, so
  config-driven code execution (git hooks, `core.fsmonitor`, terraform
  provider plugins, `.psqlrc`) can run agent code on the host. Planned fix:
  config neutralization, then a disposable executor container.
- **Egress is not enforced** — `deploy/egress-config.json` exists but nothing
  applies it yet.
- **The default `allow_all` backend approves everything** — by design, for
  local development. Production use requires configuring a real backend.
- The `StaticPolicyBackend` example in the README uses prefix matching and is
  explicitly documented as a demonstration, not a security boundary.

## Hardening Checklist for Deployers

- Never mount `~/.aws`, `~/.ssh`, `~/.kube`, `~/.docker`, or `~/.config/gh`
  into the container.
- Switch `AUTH_BACKEND` away from `allow_all` for anything beyond local dev.
- Keep `deploy/exec.token` and `deploy/server.env` out of version control
  (the default `.gitignore` already does this).
- Mount workspace folders read-only (`:ro`) when the agent doesn't need to
  write.
