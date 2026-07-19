"""Execution handler — runs on the host, handles one /execute request.

Flow:
  1. Receive { tool, args, reason, repo, branch } from the container
  2. Authorize via the configured auth backend (AUTH_BACKEND in server.env;
     polls until a terminal decision if approval is pending)
  3. Ask the backend for the execution environment (may inject temporary creds)
  4. Find and run the real binary via subprocess
  5. Return { stdout, stderr, exit_code }

The handler is backend-agnostic: which auth service is consulted (Outhora,
webhook, allow_all, ...) is decided here on the host. The container never
knows — it only speaks the execution protocol.

All sensitive data (approval tokens, credentials) stays on the host.
The container only ever sees the text output.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# repo root is one level up from server/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.auth_backends import get_auth_backend  # noqa: E402

# Hard cap on how long an executed command may run (seconds). Prevents a hung
# command (e.g. an interactive prompt) from holding a server thread forever.
_DEFAULT_TIMEOUT = 300


def _exec_timeout() -> int:
    try:
        return int(os.environ.get("EXEC_TIMEOUT") or _DEFAULT_TIMEOUT)
    except ValueError:
        return _DEFAULT_TIMEOUT


def _log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[server] {timestamp} {message}", flush=True)


def _error(message: str) -> dict:
    """Log an error and return it as the response."""
    _log(message)
    return {"stdout": "", "stderr": message, "exit_code": 1}


def _parse_override_volumes(path: str) -> dict[str, str]:
    """Extract container→host mappings from docker-compose.override.yml.

    Parses volume lines of the form "- /host/path:/workspace/name[:rw|ro]".
    Stdlib-only (no PyYAML), so it only understands this simple list format —
    which is all the override file uses.
    """
    mapping: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                match = re.match(
                    r"^\s*-\s*(/[^:]+):(/workspace(?:/[^:\s]*)?)(?::r[wo])?\s*$", line
                )
                if match:
                    host_path, container_path = match.group(1), match.group(2)
                    mapping[container_path.rstrip("/")] = host_path
    except FileNotFoundError:
        pass
    return mapping


def _workspace_map() -> dict[str, str]:
    """Container path prefix → host path mappings.

    /workspace maps to WORKSPACE_DIR (default: <repo root>/workspace, matching
    the docker-compose default). Extra mounts are read automatically from
    deploy/docker-compose.override.yml, so declaring a volume there is enough.
    WORKSPACE_MAP in server.env can add or override entries:
        WORKSPACE_MAP=/workspace/project=/Users/me/project,/workspace/docs=/Users/me/docs
    """
    mapping = {"/workspace": os.environ.get("WORKSPACE_DIR") or os.path.join(_ROOT, "workspace")}
    mapping.update(_parse_override_volumes(os.path.join(_ROOT, "deploy", "docker-compose.override.yml")))
    for pair in os.environ.get("WORKSPACE_MAP", "").split(","):
        container_path, _, host_path = pair.strip().partition("=")
        if container_path and host_path:
            mapping[container_path.rstrip("/")] = host_path
    return mapping


def _map_repo_to_host(repo: str) -> tuple[str | None, dict | None]:
    """Translate a container repo path to a host path.

    Returns (host_path, None) on success, (None, error_response) on failure.
    An empty repo means "no working directory" and is allowed.
    """
    if not repo:
        return None, None

    mapping = _workspace_map()
    # Longest prefix wins so /workspace/project beats /workspace
    for prefix in sorted(mapping, key=len, reverse=True):
        if repo == prefix or repo.startswith(prefix + "/"):
            host_path = mapping[prefix] + repo[len(prefix):]
            if not os.path.isdir(host_path):
                return None, _error(
                    f"ERROR: Container path {repo!r} maps to {host_path!r}, "
                    "which does not exist on the host. Check WORKSPACE_DIR / "
                    "WORKSPACE_MAP in deploy/server.env."
                )
            return host_path, None

    return None, _error(
        f"ERROR: Container path {repo!r} is outside the mapped workspace. "
        "Add a mapping via WORKSPACE_MAP in deploy/server.env."
    )


def execute(request: dict) -> dict:
    """Handle one execution request. Returns { stdout, stderr, exit_code }."""
    started = time.monotonic()
    result = _execute(request)

    command = " ".join([request.get("tool", "")] + request.get("args", []))
    _log(f"request complete: {command!r} -> exit {result['exit_code']} "
         f"({time.monotonic() - started:.2f}s)")
    return result


def _execute(request: dict) -> dict:
    tool: str = request.get("tool", "")
    args: list[str] = request.get("args", [])
    reason: str = request.get("reason", "")
    repo: str = request.get("repo", "")
    branch: str = request.get("branch", "")

    if not tool:
        return _error("ERROR: 'tool' field is required")

    command = " ".join([tool] + args)
    _log(f"request start: {command!r} (repo={repo}, branch={branch})")

    # ── 0. Map the container repo path to its host equivalent ─────────────
    workdir, path_error = _map_repo_to_host(repo)
    if path_error:
        return path_error
    if workdir:
        _log(f"mapped {repo!r} -> {workdir!r}")

    # ── 1. Authorize via the configured backend ──────────────────────────
    try:
        backend = get_auth_backend()
    except Exception as exc:
        return _error(f"ERROR: Failed to load auth backend: {exc}")

    try:
        decision = backend.authorize(
            tool=tool,
            command=command,
            args=args,
            reason=reason,
            repo=repo,
            branch=branch,
        )
    except Exception as exc:
        return _error(f"ERROR: Authorization failed for {command!r}: {exc}")

    # Teach the AGENT_REASON convention at the point of need: approvers see
    # the reason, so a missing one is worth flagging on any non-approval.
    reason_hint = (
        "" if reason else
        " (Tip: set AGENT_REASON=\"why you are running this\" before the "
        "command — approvers see it.)"
    )

    if decision.status == "pending":
        # Not a denial: a human simply hasn't approved yet. Tell the agent
        # explicitly so it can inform the user and retry instead of giving up.
        detail = f" ({decision.reason})" if decision.reason else ""
        ref = f" [request id: {decision.request_id}]" if decision.request_id else ""
        _log(f"PENDING: {command!r}{detail}{ref}")
        return {
            "stdout": "",
            "stderr": (
                f"PENDING APPROVAL: {command!r} requires human approval and has "
                f"not been approved yet{detail}{ref}. "
                "Tell the user an approval is waiting, then retry the command "
                f"once it has been granted.{reason_hint}"
            ),
            "exit_code": 1,
        }

    if decision.status != "approved":
        _log(f"DENIED: {command!r} ({decision.reason or 'policy violation'})")
        return {
            "stdout": "",
            "stderr": f"DENIED: {decision.reason or 'policy violation'}{reason_hint}",
            "exit_code": 1,
        }

    # ── 2. Build execution environment (backend may inject temp creds) ───
    env = backend.execution_env(tool, decision)

    # ── 3. Find real binary on the host ──────────────────────────────────
    real_binary = shutil.which(tool)
    if not real_binary:
        return _error(f"ERROR: '{tool}' not found on host PATH")

    # ── 4. Execute ────────────────────────────────────────────────────────
    # stdin=DEVNULL: commands that try to prompt (passwords, confirmations)
    # fail immediately instead of hanging until the timeout.
    timeout = _exec_timeout()
    try:
        result = subprocess.run(
            [real_binary] + args,
            env=env,
            cwd=workdir,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return _error(
            f"ERROR: {command!r} timed out after {timeout}s and was killed. "
            "If it legitimately needs longer, raise EXEC_TIMEOUT in "
            "deploy/server.env."
        )
    except Exception as exc:
        return _error(f"ERROR: Execution failed for {command!r}: {exc}")
