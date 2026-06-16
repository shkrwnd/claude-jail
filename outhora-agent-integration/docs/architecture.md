# Outhora Agent Integration — Architecture & Sequence Diagrams

## System Overview

Outhora sits between a coding agent (Claude Code, Copilot, Cursor, etc.) and the infrastructure tools it needs to use. Instead of giving the agent static credentials and unrestricted access, every tool invocation is intercepted at the CLI level, authorized by Outhora's hosted policy engine, and executed only with short-lived credentials scoped to that single action.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Docker Container                             │
│                                                                     │
│  ┌──────────────┐    ┌────────────────────┐    ┌────────────────┐  │
│  │  Claude Code  │───▶│  Outhora Wrappers  │───▶│  Real Binaries │  │
│  │  (or any AI   │    │  /opt/outhora/bin/  │    │  aws, gh, etc  │  │
│  │   agent)      │    │  aws, gh, kubectl,  │    │                │  │
│  └──────────────┘    │  terraform, psql    │    └───────┬────────┘  │
│                      └─────────┬──────────┘            │           │
│                                │                       │           │
└────────────────────────────────┼───────────────────────┼───────────┘
                                 │ HTTPS                 │
                                 ▼                       ▼
                      ┌──────────────────┐      ┌──────────────┐
                      │  api.outhora.com │      │  AWS / GH /  │
                      │                  │      │  K8s / DB /  │
                      │  • Policy Engine │      │  Terraform   │
                      │  • Approval UI   │      │  Cloud       │
                      │  • Credential    │      └──────────────┘
                      │    Vending       │
                      │  • Audit Store   │
                      └──────────────────┘
```

### How the wrapper intercept works

The Outhora wrappers are placed at `/opt/outhora/bin/` and this directory is prepended to `$PATH` before `/usr/local/bin` and `/usr/bin`. When Claude Code runs `aws s3 ls`, the shell resolves `aws` to `/opt/outhora/bin/aws` (the wrapper) instead of `/usr/local/bin/aws` (the real binary). The wrapper calls Outhora's API, and if authorized, locates and invokes the real binary with temporary credentials injected as environment variables.

### Authentication

The container authenticates to Outhora using HTTP Basic auth with an **agent ID** and **agent secret** pair, scoped to a **department** (`dept_id`). These are set as environment variables and never written to disk. The agent identity determines which policies apply and what credentials can be issued.

---

## Sequence Diagrams

### 1. Allowed Action (e.g. `aws s3 ls`)

The most common flow. The agent runs a read-only command, Outhora allows it immediately, issues temporary credentials, and the command executes.

```
┌───────────┐     ┌──────────────┐     ┌──────────────────┐     ┌─────────┐
│ Claude Code│     │ Outhora      │     │ api.outhora.com  │     │  AWS    │
│ (Agent)    │     │ Wrapper      │     │                  │     │         │
└─────┬─────┘     └──────┬───────┘     └────────┬─────────┘     └────┬────┘
      │                  │                      │                    │
      │  aws s3 ls       │                      │                    │
      │─────────────────▶│                      │                    │
      │                  │                      │                    │
      │                  │  POST /api/v1/authorize                   │
      │                  │  {tool: "aws",       │                    │
      │                  │   command: "aws s3 ls",                   │
      │                  │   user_id, dept_id,  │                    │
      │                  │   agent_session_id}  │                    │
      │                  │─────────────────────▶│                    │
      │                  │                      │                    │
      │                  │  {decision: "allow", │                    │
      │                  │   action_id: "act-1"}│                    │
      │                  │◀─────────────────────│                    │
      │                  │                      │                    │
      │                  │  POST /api/v1/credentials                 │
      │                  │  {tool: "aws",       │                    │
      │                  │   action_id: "act-1"}│                    │
      │                  │─────────────────────▶│                    │
      │                  │                      │                    │
      │                  │  {access_key: "...", │                    │
      │                  │   secret_key: "...", │                    │
      │                  │   session_token: "...",                   │
      │                  │   expires_at: "..."}  │                    │
      │                  │◀─────────────────────│                    │
      │                  │                      │                    │
      │                  │  Inject AWS_ACCESS_KEY_ID,                │
      │                  │  AWS_SECRET_ACCESS_KEY,                   │
      │                  │  AWS_SESSION_TOKEN as env vars            │
      │                  │                      │                    │
      │                  │  exec /usr/local/bin/aws s3 ls            │
      │                  │──────────────────────────────────────────▶│
      │                  │                      │                    │
      │                  │  (bucket listing)    │                    │
      │                  │◀──────────────────────────────────────────│
      │                  │                      │                    │
      │  (output)        │                      │                    │
      │◀─────────────────│                      │                    │
      │                  │                      │                    │
      │                  │  POST /api/v1/audit (background)          │
      │                  │  {tool: "aws",       │                    │
      │                  │   command: "aws s3 ls",                   │
      │                  │   decision: "allow", │                    │
      │                  │   exit_code: 0}      │                    │
      │                  │─────────────────────▶│                    │
      │                  │                      │                    │
```

**Key points:**
- Credentials are injected as process-level environment variables, never written to `~/.aws/credentials`
- The real binary is located by scanning `$PATH` and skipping the wrapper directory
- Audit is sent in the background (fire-and-forget) so it never blocks the agent

---

### 2. Denied Action (e.g. `terraform destroy`)

A destructive command is blocked immediately. The real binary is never executed. No credentials are issued.

```
┌───────────┐     ┌──────────────┐     ┌──────────────────┐
│ Claude Code│     │ Outhora      │     │ api.outhora.com  │
│ (Agent)    │     │ Wrapper      │     │                  │
└─────┬─────┘     └──────┬───────┘     └────────┬─────────┘
      │                  │                      │
      │  terraform       │                      │
      │  destroy         │                      │
      │─────────────────▶│                      │
      │                  │                      │
      │                  │  POST /api/v1/authorize
      │                  │  {tool: "terraform", │
      │                  │   command: "terraform destroy",
      │                  │   user_id, dept_id}  │
      │                  │─────────────────────▶│
      │                  │                      │
      │                  │  {decision: "deny",  │
      │                  │   reason: "destructive
      │                  │   action blocked by  │
      │                  │   policy"}           │
      │                  │◀─────────────────────│
      │                  │                      │
      │  DENIED:         │                      │
      │  "destructive    │                      │
      │   action blocked │                      │
      │   by policy"     │                      │
      │◀─────────────────│                      │
      │                  │                      │
      │  (exit code 1)   │                      │
      │                  │                      │
      │           ╔═══════════════════════╗     │
      │           ║ No credentials issued ║     │
      │           ║ No binary executed    ║     │
      │           ║ No side effects       ║     │
      │           ╚═══════════════════════╝     │
```

**Key points:**
- The deny happens before any credentials are fetched — the agent never gets access
- Exit code 1 signals Claude Code that the command failed, so it can adapt
- The deny reason is surfaced to the agent so it can explain to the user why

---

### 3. Approval-Required Action (e.g. `terraform apply`)

A sensitive-but-legitimate command needs human review. The wrapper prints an approval URL and exits immediately without executing. A human reviews and approves (or denies) in the Outhora UI.

```
┌───────────┐     ┌──────────────┐     ┌──────────────────┐     ┌──────────┐
│ Claude Code│     │ Outhora      │     │ api.outhora.com  │     │ Approver │
│ (Agent)    │     │ Wrapper      │     │                  │     │ (Human)  │
└─────┬─────┘     └──────┬───────┘     └────────┬─────────┘     └────┬─────┘
      │                  │                      │                    │
      │  terraform       │                      │                    │
      │  apply           │                      │                    │
      │─────────────────▶│                      │                    │
      │                  │                      │                    │
      │                  │  POST /api/v1/authorize                   │
      │                  │  {tool: "terraform", │                    │
      │                  │   command: "terraform apply",             │
      │                  │   user_id, dept_id}  │                    │
      │                  │─────────────────────▶│                    │
      │                  │                      │                    │
      │                  │  {decision:          │                    │
      │                  │   "approval_required",                    │
      │                  │   approval_id:       │                    │
      │                  │   "apr-789"}         │                    │
      │                  │◀─────────────────────│                    │
      │                  │                      │                    │
      │  ╔═══════════════════════════════════╗  │                    │
      │  ║ Approval required in Outhora     ║  │                    │
      │  ║                                   ║  │                    │
      │  ║ URL: https://app.outhora.com/    ║  │                    │
      │  ║      approvals/apr-789           ║  │                    │
      │  ║                                   ║  │                    │
      │  ║ Command will not execute until   ║  │                    │
      │  ║ approved.                        ║  │                    │
      │  ╚═══════════════════════════════════╝  │                    │
      │◀─────────────────│                      │                    │
      │                  │                      │                    │
      │  (exit code 2)   │                      │                    │
      │                  │                      │                    │
      │                  │                      │                    │
      │                  │              ┌───────┴────────┐           │
      │                  │              │ Outhora shows: │           │
      │                  │              │ • who requested│           │
      │                  │              │ • what command │──────────▶│
      │                  │              │ • repo/branch  │  reviews  │
      │                  │              │ • dept context │           │
      │                  │              └───────┬────────┘           │
      │                  │                      │                    │
      │                  │                      │◀── approve/deny ───│
      │                  │                      │                    │
      │                  │                      │                    │
      │  (agent retries  │                      │                    │
      │   later or user  │                      │                    │
      │   re-runs cmd)   │                      │                    │
      │─────────────────▶│  POST /api/v1/authorize                   │
      │                  │─────────────────────▶│                    │
      │                  │  {decision: "allow"} │                    │
      │                  │◀─────────────────────│                    │
      │                  │                      │                    │
      │                  │  (continues with credential               │
      │                  │   fetch + execution, │                    │
      │                  │   same as Allow flow)│                    │
```

**Key points:**
- Exit code 2 distinguishes "needs approval" from "denied" (exit 1) and "success" (exit 0)
- The agent can poll `GET /api/v1/approvals/{id}` to check if it was approved (future)
- Outhora's UI shows the approver full context: command, repo, branch, who requested it, department

---

### 4. Temporary Credential Lifecycle

This diagram focuses on how credentials flow — they are never stored, never shared across actions, and expire automatically.

```
┌───────────────────────────────────────────────────────────────────┐
│                     Credential Lifecycle                          │
│                                                                   │
│  ┌─────────┐        ┌─────────────┐        ┌──────────────────┐ │
│  │ Wrapper  │        │ Outhora API │        │ Cloud Provider   │ │
│  │ Process  │        │             │        │ (AWS STS / GH    │ │
│  │          │        │             │        │  App / K8s SA)   │ │
│  └────┬─────┘        └──────┬──────┘        └───────┬──────────┘ │
│       │                     │                       │            │
│       │  1. Request creds   │                       │            │
│       │  (tool + action_id) │                       │            │
│       │────────────────────▶│                       │            │
│       │                     │                       │            │
│       │                     │  2. AssumeRole /      │            │
│       │                     │     generate token    │            │
│       │                     │──────────────────────▶│            │
│       │                     │                       │            │
│       │                     │  3. Temp creds        │            │
│       │                     │  (TTL: minutes)       │            │
│       │                     │◀──────────────────────│            │
│       │                     │                       │            │
│       │  4. Temp creds      │                       │            │
│       │  (in HTTP response) │                       │            │
│       │◀────────────────────│                       │            │
│       │                     │                       │            │
│       │  5. Inject as env   │                       │            │
│       │     vars in subprocess                      │            │
│       │  ┌────────────────────────────────┐         │            │
│       │  │ AWS_ACCESS_KEY_ID=AKIA...     │         │            │
│       │  │ AWS_SECRET_ACCESS_KEY=...     │         │            │
│       │  │ AWS_SESSION_TOKEN=...         │         │            │
│       │  │                               │         │            │
│       │  │  > aws s3 ls                  │─────────────────────▶│
│       │  │  (uses temp creds)            │         │            │
│       │  └────────────────────────────────┘         │            │
│       │                     │                       │            │
│       │  6. Process exits   │                       │            │
│       │     — env vars gone │                       │            │
│       │     — nothing on    │                       │            │
│       │       disk          │                       │            │
│       │                     │                       │            │
│       │                     │  7. Creds auto-expire │            │
│       │                     │     after TTL         │            │
│       │                     │                       │            │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ✗ No ~/.aws/credentials file                              │  │
│  │ ✗ No credential files on disk at any point                │  │
│  │ ✗ No credential reuse across actions                      │  │
│  │ ✗ No long-lived tokens                                    │  │
│  │ ✓ Each action gets unique, scoped, short-lived creds      │  │
│  └────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

---

### 5. Audit Trail

Every tool invocation — whether allowed, denied, or pending approval — produces an audit record. This diagram shows what is captured and when.

```
┌──────────────┐     ┌──────────────────┐
│ Outhora      │     │ api.outhora.com  │
│ Wrapper      │     │ /api/v1/audit    │
└──────┬───────┘     └────────┬─────────┘
       │                      │
       │  ┌──────────────────────────────────────────┐
       │  │ Audit Event Payload                      │
       │  │                                          │
       │  │ {                                        │
       │  │   "timestamp": "2026-06-14T15:30:00Z",  │
       │  │   "tool":      "aws",                   │
       │  │   "command":   "aws s3 ls s3://bucket",  │
       │  │   "decision":  "allow",                  │
       │  │   "user_id":   "dev-shikhartiwari",     │
       │  │   "agent_session_id": "sess-abc123",    │
       │  │   "action_id": "act-456",               │
       │  │   "exit_code": 0                        │
       │  │ }                                        │
       │  └──────────────────────────────────────────┘
       │                      │
       │  POST (background)   │
       │─────────────────────▶│
       │                      │
       │  If fails:           │
       │  retry up to 3×      │
       │  with backoff        │
       │  (1s, 2s, 4s)        │
       │                      │
       │  202 Accepted        │
       │◀─────────────────────│
       │                      │

  ┌──────────────────────────────────────────────────────────┐
  │ What gets logged:                                        │
  │                                                          │
  │  • Every "allow"  — what ran, who ran it, exit code     │
  │  • Every "deny"   — what was blocked and why            │
  │  • Every "approval_required" — what awaits review       │
  │                                                          │
  │ What is NEVER logged:                                    │
  │                                                          │
  │  • Credentials (access keys, tokens, passwords)         │
  │  • Command output / stdout / stderr                     │
  │  • File contents                                        │
  └──────────────────────────────────────────────────────────┘
```

---

### 6. End-to-End: Complete Wrapper Execution Flow

This is the full lifecycle of a single wrapper invocation, showing every step and decision point.

```
                    ┌─────────────────┐
                    │ Agent runs      │
                    │ "aws s3 ls"     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Shell resolves  │
                    │ aws → wrapper   │
                    │ (/opt/outhora/  │
                    │  bin/aws)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Wrapper sources │
                    │ outhora-common  │
                    │ .sh             │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Validate env:   │
                    │ OUTHORA_AGENT_ID│
                    │ OUTHORA_AGENT_  │
                    │   SECRET        │
                    │ OUTHORA_DEPT_ID │
                    └────────┬────────┘
                             │
                             ▼
                ┌────────────────────────┐
                │ POST /api/v1/authorize │
                │                        │
                │ Auth: Basic            │
                │   {agent_id:secret}    │
                │                        │
                │ Body: {tool, command,  │
                │   user_id, dept_id,    │
                │   agent_session_id,    │
                │   repo, branch}        │
                └────────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────┐
                  │ Decision?        │
                  └──┬───────┬───┬───┘
                     │       │   │
              ┌──────┘       │   └──────┐
              ▼              ▼          ▼
        ┌──────────┐  ┌───────────┐  ┌──────────────┐
        │  ALLOW   │  │   DENY    │  │  APPROVAL    │
        │          │  │           │  │  REQUIRED    │
        └────┬─────┘  └─────┬─────┘  └──────┬───────┘
             │              │               │
             │              ▼               ▼
             │     ┌──────────────┐  ┌──────────────┐
             │     │ Print reason │  │ Print URL:   │
             │     │ to stderr    │  │ app.outhora  │
             │     │              │  │ .com/approve │
             │     │ Exit code: 1 │  │ /{id}        │
             │     └──────────────┘  │              │
             │                       │ Exit code: 2 │
             ▼                       └──────────────┘
    ┌─────────────────┐
    │ POST /api/v1/   │
    │ credentials     │
    │                 │
    │ {tool,          │
    │  action_id}     │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ Inject creds    │
    │ as env vars:    │
    │                 │
    │ AWS_ACCESS_     │
    │   KEY_ID        │
    │ AWS_SECRET_     │
    │   ACCESS_KEY    │
    │ AWS_SESSION_    │
    │   TOKEN         │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ Find real binary│
    │ by scanning     │
    │ PATH, skipping  │
    │ wrapper dir     │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ exec real binary│
    │ with injected   │
    │ env vars        │
    │                 │
    │ Capture exit    │
    │ code            │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ POST /api/v1/   │
    │ audit           │
    │ (background,    │
    │  3× retry)      │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │ Exit with       │
    │ original exit   │
    │ code            │
    └─────────────────┘
```

---

## Component Descriptions

### Outhora CLI Wrappers (`/opt/outhora/bin/`)

Bash scripts that shadow real tool binaries in `$PATH`. Each wrapper follows an identical pattern:

1. **Capture** — record the full command (`tool + arguments`)
2. **Authorize** — `POST /api/v1/authorize` with command details, agent identity, and context (repo, branch, department)
3. **Decide** — handle `allow` / `deny` / `approval_required`
4. **Credential fetch** — if allowed, `POST /api/v1/credentials` to get short-lived secrets
5. **Inject** — set credentials as environment variables (tool-specific: `AWS_*`, `GH_TOKEN`, `PGPASSWORD`, etc.)
6. **Execute** — run the real binary with the injected environment
7. **Audit** — `POST /api/v1/audit` in the background with the outcome and exit code

### Outhora Python SDK (`/opt/outhora/sdk/`)

A zero-dependency (stdlib-only) Python SDK for programmatic access to the same API the wrappers use. Useful for:

- Custom tools or scripts that need authorization
- Building higher-level orchestration on top of Outhora
- Testing and validation

### Outhora Hosted Platform (`api.outhora.com`)

The server-side components — **not** part of this integration package:

| Component | Responsibility |
|-----------|---------------|
| **Policy Engine** | Evaluates authorization requests against configured policies per department. Returns allow/deny/approval_required. |
| **Approval UI** | Web interface at `app.outhora.com/approvals/{id}` where designated approvers review and decide on pending actions. |
| **Credential Vending** | Issues short-lived, scoped credentials by calling cloud provider APIs (AWS STS, GitHub App installations, K8s token requests). |
| **Audit Store** | Immutable log of every authorization decision, who made it, what was executed, and the outcome. |

### Security Boundaries

| Boundary | Enforced By |
|----------|------------|
| Container isolation (filesystem, process) | Docker |
| No static credentials in container | Docker Compose config (no host mounts for `~/.aws`, `~/.ssh`, etc.) |
| Authorization before execution | Outhora wrappers (PATH precedence) |
| Short-lived credentials only | Outhora credential vending + cloud provider TTLs |
| Audit trail | Outhora audit API (fire-and-forget with retry) |
| Human approval for sensitive ops | Outhora approval workflow |
| Non-root execution | Dockerfile `USER outhora` |
| No privilege escalation | `cap_drop: ALL` + `no-new-privileges:true` |
