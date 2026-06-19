# Outhora Agent Integration Layer

Authorization, approval workflows, temporary credentials, and audit logging for Claude Code and other coding agents running in Docker containers.

## Why Docker Isolation Alone Is Insufficient

Docker containers provide process and filesystem isolation, but they do not provide:

- **Authorization** — a container can execute any CLI tool without policy checks
- **Approval workflows** — no mechanism to require human review before destructive actions
- **Temporary credentials** — static credentials mounted into containers persist beyond need and can be exfiltrated
- **Audit logging** — no centralized record of what an agent executed, when, and why

Outhora bridges this gap by intercepting tool calls at the CLI level, routing them through a policy engine, and issuing short-lived credentials only when authorized.

## Architecture

```
Claude Code (inside Docker)
       │
       ▼
┌──────────────────────┐
│  Outhora CLI Wrapper  │  ← intercepts aws, gh, kubectl, terraform, psql
│  (bash scripts)       │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  Outhora Python SDK   │  ← HTTP calls to Outhora API
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│  https://app.outhora  │  ← hosted policy engine, approval UI, audit store
│  .com                 │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  Decision: allow / deny / approval_required │
│  + Temporary Credentials (if allowed)        │
└──────────────────────────────────────────┘
       │
       ▼
   Tool Execution
```

## Sequence Diagrams

### Allow Flow

```
Agent          Wrapper        Outhora API
  │               │               │
  │── aws s3 ls ──▶               │
  │               │── authorize ──▶
  │               │◀── allow ─────│
  │               │── get creds ──▶
  │               │◀── temp creds─│
  │               │               │
  │               │── exec aws ───│
  │               │               │
  │               │── audit ──────▶
  │◀── output ────│               │
```

### Deny Flow

```
Agent          Wrapper        Outhora API
  │               │               │
  │── tf destroy ─▶               │
  │               │── authorize ──▶
  │               │◀── deny ──────│
  │◀── DENIED ────│               │
```

### Approval Required Flow

```
Agent          Wrapper        Outhora API        Approver
  │               │               │                  │
  │── tf apply ───▶               │                  │
  │               │── authorize ──▶                  │
  │               │◀── approval ──│                  │
  │               │   required    │                  │
  │◀── URL ───────│               │                  │
  │  (exit 2)     │               │                  │
  │               │               │◀── approve ──────│
  │               │               │                  │
  │ (retry later) │               │                  │
```

## Installation

### 1. Create your `.env` file

Copy the example and fill in your values:

```bash
cp outhora-agent-integration/deploy/.env.example .env
```

```bash
# Required
OUTHORA_API_URL=https://api.outhora.com
OUTHORA_AGENT_ID=your-agent-id
OUTHORA_AGENT_SECRET=your-agent-secret
OUTHORA_DEPT_ID=your-dept-id

# Optional
OUTHORA_USER_ID=developer-id      # defaults to $USER
OUTHORA_SESSION_ID=               # groups tool calls in the audit log

# Claude Code
ANTHROPIC_API_KEY=your-anthropic-key

# Workspace folder mounted into the container as /workspace
# Can be any absolute or relative path on the host
# WORKSPACE_DIR=/Users/you/projects/myproject
```

Keep `.env` at the repo root and never commit it:

```bash
echo ".env" >> .gitignore
```

Obtain your agent ID, secret, and dept ID from [app.outhora.com](https://app.outhora.com).

### 2. Configure your workspace folders

By default the container mounts one folder as `/workspace`. To give Claude Code access to multiple folders, create an override file:

```bash
cp deploy/docker-compose.override.example.yml deploy/docker-compose.override.yml
# edit docker-compose.override.yml with your actual paths
```

```yaml
services:
  claude:
    volumes:
      - /path/to/project:/workspace/project:rw
      - /path/to/another-repo:/workspace/another-repo:rw
      - /path/to/docs:/workspace/docs:ro   # read-only
```

Inside the container, Claude sees all your folders under `/workspace/`.

### 3. Build and start the container

```bash
cd outhora-agent-integration

# Without override (single workspace folder from .env WORKSPACE_DIR)
docker compose --env-file ../.env -f deploy/docker-compose.example.yml up -d --build

# With override (multiple workspace folders)
docker compose --env-file ../.env \
  -f deploy/docker-compose.example.yml \
  -f deploy/docker-compose.override.yml \
  up -d --build
```

This builds an image with Node.js, Claude Code CLI, Python, and the Outhora wrappers pre-installed.

### 3. Shell in and use Claude Code

```bash
docker compose -f deploy/docker-compose.example.yml exec claude bash

# Inside the container
claude
```

Claude Code works exactly as it does locally. On first run it will prompt you to log in via the browser. After that, any call to `aws`, `gh`, `kubectl`, `terraform`, or `psql` is automatically intercepted by Outhora.

## How It Works

### CLI Wrappers

Each wrapper (`aws`, `gh`, `git`, `kubectl`, `terraform`, `psql`) is a bash script that:

1. Captures the full command
2. Calls `POST /api/v1/authorize` with the command details
3. Handles the decision:
   - **allow** — proceeds to credential fetch and execution
   - **deny** — prints reason and exits with code 1
   - **approval_required** — prints approval URL and exits with code 2
4. Fetches temporary credentials from `POST /api/v1/credentials`
5. Injects credentials into environment variables (never written to disk)
6. Executes the real binary
7. Sends an audit event to `POST /api/v1/audit`

### Temporary Credentials

Outhora never expects static credentials in the container. On each authorized action:

- The wrapper requests short-lived credentials scoped to that specific action
- Credentials are injected as environment variables for the subprocess only
- No credential files are written to disk
- Credentials expire automatically

**Supported credential types:**

| Tool | Credentials |
|------|------------|
| aws | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` |
| gh | `GH_TOKEN`, `GITHUB_TOKEN` |
| git | None — uses SSH/HTTPS configured in the repo |
| kubectl | `--token` flag, `--server` flag |
| terraform | AWS env vars + `TF_TOKEN_app_terraform_io` |
| psql | `PGPASSWORD`, `PGUSER`, `PGHOST`, `PGDATABASE` |

### Audit Logging

Every tool execution generates an audit event sent to Outhora:

```json
{
  "timestamp": "2026-01-15T10:30:00Z",
  "tool": "aws",
  "command": "aws s3 ls",
  "decision": "allow",
  "agent_session_id": "sess-abc",
  "user_id": "dev-1",
  "exit_code": 0
}
```

Audit submission uses fire-and-forget with 3 retries and exponential backoff. Audit failures never block tool execution.

## Security

### Container Hardening

```yaml
cap_drop:
  - ALL
security_opt:
  - no-new-privileges:true
```

### Do NOT Mount

```
~/.aws
~/.ssh
~/.kube
~/.docker
~/.config/gh
```

All credentials come from Outhora's temporary credential API.

### Non-Root Execution

Containers should run as a non-root user. See `deploy/Dockerfile.integration`.

## Example Policies

These are configured in Outhora (not enforced locally):

| Action | Policy |
|--------|--------|
| `aws s3 ls` | Allow |
| `gh pr view` | Allow |
| `git status` | Allow |
| `git log` | Allow |
| `git commit` | Allow |
| `kubectl get pods` | Allow |
| `terraform plan` | Allow |
| `git push` | Approval Required |
| `gh pr merge` | Approval Required |
| `terraform apply` | Approval Required |
| `kubectl rollout restart` | Approval Required |
| `git push --force` | Deny |
| `terraform destroy` | Deny |
| `kubectl delete namespace` | Deny |
| `aws rds delete-db-instance` | Deny |
| `aws iam delete-role` | Deny |

## Python SDK

For programmatic integration:

```python
from sdk.client import OuthoraClient, AuthorizationDenied, ApprovalRequired

# Credentials from environment (OUTHORA_AGENT_ID, OUTHORA_AGENT_SECRET)
client = OuthoraClient()

# Or pass explicitly:
# client = OuthoraClient(agent_id="your-id", agent_secret="your-secret")

# Health check
assert client.health_check()

# Full authorization flow
try:
    auth_resp, creds = client.execute_authorized(
        tool="aws",
        command="aws s3 ls",
    )
    # creds.access_key, creds.secret_key, creds.session_token
except AuthorizationDenied as e:
    print(f"Denied: {e.reason}")
except ApprovalRequired as e:
    print(f"Approve at: {e.approval_url}")
```

## Testing

All tests run inside Docker containers to match the production environment:

```bash
# Run all tests (SDK + wrapper integration) inside Docker
cd outhora-agent-integration
./tests/run-tests.sh
```

This builds a test container, then runs:
1. **Python SDK unit tests** — model serialization, client flows, credential injection (uses an in-process mock HTTP server)
2. **Wrapper integration tests** — authorization decisions, credential fetch, audit submission against a mock Outhora API

## Future Integrations

- **AWS STS** — Outhora can issue STS `AssumeRole` credentials scoped to specific actions
- **GitHub App Tokens** — installation tokens with fine-grained permissions per repository
- **Kubernetes Service Accounts** — short-lived tokens bound to specific namespaces and RBAC roles
- **Database Credentials** — time-limited database users via Vault or native DB mechanisms

## Project Structure

```
outhora-agent-integration/
├── sdk/
│   ├── __init__.py
│   ├── client.py          # Main SDK client
│   ├── auth.py            # Authentication helpers
│   ├── credentials.py     # Credential injection per tool
│   ├── audit.py           # Audit event submission with retries
│   └── models.py          # Typed dataclass models
├── wrappers/
│   ├── outhora-common.sh  # Shared bash functions
│   ├── aws                # AWS CLI wrapper
│   ├── gh                 # GitHub CLI wrapper
│   ├── git                # Git wrapper (no credential injection)
│   ├── kubectl            # kubectl wrapper
│   ├── terraform          # Terraform wrapper
│   └── psql               # PostgreSQL wrapper
├── deploy/
│   ├── docker-compose.example.yml
│   ├── Dockerfile.integration
│   └── .env.example
├── tests/
│   ├── Dockerfile.test    # Test container definition
│   ├── run-tests.sh       # Entrypoint: builds and runs all tests in Docker
│   ├── test_sdk.py        # Python unit tests with mock server
│   └── test_wrappers.sh   # Bash integration tests
└── README.md
```
