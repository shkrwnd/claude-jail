# TODO

## Host-Side Execution Is a Sandbox Escape Vector
The execution server runs real binaries on the host with `cwd` inside an
agent-writable workspace. Tools execute config from there: git runs
`.git/hooks/` and reads `.git/config` (`core.fsmonitor`, `core.sshCommand` →
arbitrary host command execution), terraform runs provider plugins declared by
workspace files, psql reads `.psqlrc`. An approved `git commit` can therefore
run agent-written code on the host with full user privileges.

Two distinct risks: **R1** host compromise (fixable), **R2** credential theft
by agent code running while creds are in env (only shrinkable).

Layered fix, in order:

1. **Neutralize config-driven code execution** (cheap, partial — blocklist):
   per-tool safe env/flags in `server/handler.py`. Git: `-c
   core.hooksPath=/dev/null -c core.fsmonitor=false -c core.sshCommand=ssh -c
   core.pager=cat -c credential.helper=` + `GIT_CONFIG_GLOBAL=/dev/null`,
   `GIT_CONFIG_SYSTEM=/dev/null`. psql: `PSQLRC=/dev/null`. Terraform with
   agent-controlled `.tf` files is inherently code execution — policy must
   treat it as such. Caveat: git has many more exec-ish keys (`filter.*`,
   `diff.*.textconv`, `merge.*.driver`, aliases...) — raises the bar only.
2. **Executor sandbox** (the real R1 fix): host process stays the broker
   (auth, creds, audit) but runs the tool in a short-lived executor container
   (`docker run --rm -v <workspace>:/w -e <temp creds> <tool> ...`) instead of
   `subprocess.run` on the host. Malicious hooks then fire inside a disposable
   container with no host FS, no `~/.ssh`, no `server.env`. Also removes the
   need for container→host path mapping. Plan: `EXECUTOR=docker|host` setting,
   docker default, host kept for dev. Cost: ~0.5–1s startup + executor image.
3. **Make stolen creds worthless** (shrinks R2): short TTLs, per-action
   scoping (STS policy for the exact call, single-repo deploy keys), and no
   general egress from the executor — enforce `egress-config.json` there
   (ties into the item below).

## Input Validation in handler.py

The broker crashes on malformed requests: `{"tool": [1,2,3]}` or
`{"tool": 123}` causes an unhandled exception (string operations on
non-string), returning an empty reply / broken connection instead of a
proper 400 error. Similarly, `args` is assumed to be a list of strings but
never checked.

Fix: validate at the top of `_execute()` that `tool` is a non-empty string
and `args` is a list of strings; return a 400/error response for anything
else. Also worth adding: reject requests with missing `tool` field entirely
(currently returns an error message but only after string ops have already
run).

Additionally, document in SECURITY.md that `host.docker.internal` exposes
ALL host services — users running other things on localhost (databases,
admin panels, other APIs) should understand the container can reach them
until egress enforcement is implemented.

## Container Hardening (from internal pentest)

Findings from running Claude against its own sandbox. These don't break the
trust boundary (the container is assumed adversarial; security lives on the
host), but they reduce unnecessary attack surface:

1. **Strip SUID bits** — `su`, `passwd`, `mount`, `umount`, `gpasswd`,
   `chfn`, `chsh`, `chage`, `newgrp` are all unnecessary inside the
   container. `no-new-privileges` already neuters them at runtime, but
   removing the bits is defense-in-depth. One Dockerfile `RUN` line:
   `find / -perm /4000 -exec chmod u-s {} +`

2. **Stop mounting `egress-config.json`** until enforcement exists — the
   config is inert (nothing reads it) and just reveals the planned allowlist
   to a compromised agent, letting it tailor exfiltration to the allowed
   channels. Remove the bind mount from `docker-compose.yml` until the
   egress proxy/iptables work below is implemented.

3. **EXEC_TOKEN in process environment** — every child process inherits it
   and it's readable via `/proc/*/environ`. Accepted trade-off: stealing
   the token gains nothing beyond what the wrappers already provide (the
   host still authorizes via the auth backend). Documented in SECURITY.md.
   If a future design eliminates the env var (e.g. per-request short-lived
   tokens or a mounted secret file with restricted read), do it then.

4. **Claude Code's own credential storage** — OAuth tokens in
   `~/.claude/.credentials.json` and conversation history in
   `history.jsonl` are readable by agent. This is Anthropic's app behavior,
   not something we control. The tokens grant Claude API access the agent
   already has by running inside the container. No action unless Anthropic
   ships a keychain-backed storage option.

## Egress Enforcement — Remaining Gaps

Domain-level filtering is now enforced via tinyproxy (`deploy/egress-proxy/`):
the container is on an `internal: true` network, all HTTPS routes through
the proxy, and the allowlist controls which domains are reachable.

Still open:
- **Host port filtering** — `NO_PROXY=host.docker.internal` lets the
  container reach all host ports (needed for the execution server). Other
  host services (dev databases, admin UIs) are reachable too. Fix: iptables
  init container or move the execution server onto the Docker network
  directly (requires rethinking how it runs host binaries).
- **Non-HTTP egress** — raw TCP/UDP that doesn't use the proxy env vars is
  blocked by the internal network, BUT a process that ignores `HTTPS_PROXY`
  and tries a direct connection simply gets "network unreachable." This is
  correct behavior (blocked), but worth noting: it relies on the `internal`
  network flag, not active filtering.
- **Exfiltration via allowed domains** — a compromised agent could encode
  secrets in prompts to api.anthropic.com. No network filter can stop this;
  it requires Anthropic-side monitoring.
