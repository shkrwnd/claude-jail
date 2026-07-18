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

## Egress Enforcement
The `deploy/egress-config.json` defines allowed domains but nothing enforces it.
Need to decide and implement one of:
- **iptables** (`init-firewall.sh`) — resolves allowed domains to IPs at startup, blocks all other outbound traffic. Requires `NET_ADMIN` + `NET_RAW` caps. Simpler but IP-based not domain-based.
- **Egress proxy** — all traffic routes through a proxy that checks domain names in HTTP/TLS SNI. Airtight domain-level enforcement but more complex to build.

Clarify first: is there an existing proxy implementation, or does this need to be built from scratch?
