"""Container-side client for the host execution protocol.

This is the ONLY thing the container knows about the outside world:

    POST /execute to the host execution server
    request:  { tool, args, reason, repo, branch }
    response: { stdout, stderr, exit_code }

Authorization, credentials, and execution all happen on the host
(see server/). Which auth service the host consults — Outhora, a
webhook, or none — is invisible to the container by design.

Stdlib-only. No imports from server/ or any SDK.

Env vars:
    EXEC_SERVER — "host:port" of the execution server
                  (default: host.docker.internal:8377)
    EXEC_TOKEN  — shared secret sent as X-Exec-Token; injected into the
                  container by docker compose from deploy/exec.token
"""

from __future__ import annotations

import http.client
import json
import os
from dataclasses import dataclass

_DEFAULT_SERVER = "host.docker.internal:8377"


@dataclass
class ExecutionResult:
    """Result of a remote execution request."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def execute(
    tool: str,
    args: list[str],
    reason: str = "",
    repo: str = "",
    branch: str = "",
    server: str | None = None,
) -> ExecutionResult:
    """Send one execution request to the host and return its result."""
    endpoint = server or os.environ.get("EXEC_SERVER", _DEFAULT_SERVER)
    host, _, port = endpoint.partition(":")

    payload = json.dumps({
        "tool": tool,
        "args": args,
        "reason": reason,
        "repo": repo,
        "branch": branch,
    }).encode()

    try:
        conn = http.client.HTTPConnection(host, int(port or 8377))
        conn.request(
            "POST", "/execute",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "X-Exec-Token": os.environ.get("EXEC_TOKEN", ""),
            },
        )
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
    except ConnectionRefusedError:
        return ExecutionResult(
            exit_code=1,
            stderr=(
                f"ERROR: Execution server not listening on {endpoint}.\n"
                "Start it on the host with: python3 server/main.py"
            ),
        )
    except OSError as exc:
        return ExecutionResult(exit_code=1, stderr=f"ERROR: Cannot reach execution server at {endpoint}: {exc}")

    if resp.status == 401:
        return ExecutionResult(
            exit_code=1,
            stderr=(
                "ERROR: Execution server rejected the request (401): EXEC_TOKEN "
                "missing or stale. Restart the stack with ./start.sh to re-sync "
                "deploy/exec.token into the container."
            ),
        )
    if resp.status != 200:
        return ExecutionResult(exit_code=1, stderr=f"ERROR: Server returned HTTP {resp.status}: {body}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ExecutionResult(exit_code=1, stderr=f"ERROR: Invalid response from server: {body}")

    return ExecutionResult(
        exit_code=int(data.get("exit_code", 1)),
        stdout=data.get("stdout", ""),
        stderr=data.get("stderr", ""),
    )
