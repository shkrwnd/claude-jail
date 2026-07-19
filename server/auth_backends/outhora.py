"""Outhora ACP authorization backend.

Routes every tool call through the Outhora AI Control Plane for policy
evaluation and optional human approval. Polls until a terminal decision
is reached before returning. On approval, can exchange the approval_token
for temporary credentials via execution_env().

Required env vars (when AUTH_BACKEND=outhora):
    OUTHORA_API_URL      — Outhora API base URL
    OUTHORA_AGENT_ID     — Agent identifier
    OUTHORA_AGENT_SECRET — Agent secret
    OUTHORA_DEPT_ID      — Department ID
"""

from __future__ import annotations

import os
import sys
import time

from server.auth_backends.base import AuthBackend, AuthDecision
from server.outhora.client import OuthoraClient
from server.outhora.credentials import build_execution_env
from server.outhora.models import ActionStatus

_POLL_INTERVAL = int(os.environ.get("OUTHORA_POLL_INTERVAL", "5"))
_POLL_TIMEOUT = int(os.environ.get("OUTHORA_POLL_TIMEOUT", "600"))


class OuthoraBackend(AuthBackend):
    """Authorization via Outhora ACP.

    Submits each action to /v1/actions and polls if pending, blocking until
    the approver accepts or rejects. On approval, returns AuthDecision(approved)
    carrying the single-use approval_token for credential issuance.
    """

    required_env = ("OUTHORA_AGENT_ID", "OUTHORA_AGENT_SECRET", "OUTHORA_DEPT_ID")

    def __init__(self) -> None:
        self._client = OuthoraClient()

    def authorize(
        self,
        tool: str,
        command: str,
        args: list[str],
        reason: str = "",
        repo: str = "",
        branch: str = "",
    ) -> AuthDecision:
        # Derive action_type: {tool}_{first_non_flag_subcommand}
        subcommand = next((a for a in args if not a.startswith("-")), "")
        action_type = f"{tool}_{subcommand}" if subcommand else tool

        try:
            resp = self._client.submit_action(
                action_type=action_type,
                context={
                    "tool": tool,
                    "command": command,
                    "reason": reason,
                    "repo": repo or os.getcwd(),
                    "branch": branch,
                },
            )
        except Exception as exc:
            return AuthDecision(status="denied", reason=f"Outhora error: {exc}")

        if resp.status == ActionStatus.APPROVED:
            return AuthDecision(
                status="approved",
                request_id=resp.request_id,
                approval_token=resp.approval_token,
            )

        if resp.status == ActionStatus.REJECTED:
            return AuthDecision(
                status="denied",
                reason=resp.reason or "rejected by policy",
                request_id=resp.request_id,
            )

        # PENDING — print approval notice and poll
        if resp.status == ActionStatus.PENDING:
            api_url = os.environ.get("OUTHORA_API_URL", "https://api.outhora.com").rstrip("/")
            print("", file=sys.stderr)
            print("╔══════════════════════════════════════════════════════╗", file=sys.stderr)
            print("║  Approval required in Outhora                        ║", file=sys.stderr)
            print("╚══════════════════════════════════════════════════════╝", file=sys.stderr)
            print("", file=sys.stderr)
            print(f"  Request ID: {resp.request_id}", file=sys.stderr)
            print(f"  Approver:   {resp.approver}", file=sys.stderr)
            print(f"  Review at:  {api_url}/approvals/{resp.request_id}", file=sys.stderr)
            print("", file=sys.stderr)
            print("  Waiting for approval (Ctrl+C to cancel)...", file=sys.stderr)
            print("", file=sys.stderr)

            deadline = time.time() + _POLL_TIMEOUT
            while time.time() < deadline:
                time.sleep(_POLL_INTERVAL)
                try:
                    poll = self._client.get_action_status(resp.request_id)
                except Exception as exc:
                    print(f"  Poll error: {exc}", file=sys.stderr)
                    continue

                if poll.status == ActionStatus.APPROVED:
                    print("  Approved. Executing...", file=sys.stderr)
                    return AuthDecision(
                        status="approved",
                        request_id=poll.request_id,
                        approver=poll.approver,
                        approval_token=poll.approval_token,
                    )

                if poll.status == ActionStatus.REJECTED:
                    return AuthDecision(
                        status="denied",
                        reason=poll.reason or "rejected by approver",
                        request_id=poll.request_id,
                    )

                print("  Still waiting...", file=sys.stderr)

            # Still unapproved after the poll window — report "pending", not
            # "denied": the handler tells the agent an approval is waiting.
            return AuthDecision(
                status="pending",
                reason=f"still awaiting approval after {_POLL_TIMEOUT}s",
                request_id=resp.request_id,
            )

        return AuthDecision(status="denied", reason=f"Unexpected status: {resp.status}")

    def execution_env(self, tool: str, decision: AuthDecision) -> dict[str, str]:
        """Exchange the approval_token for temporary credentials, if available.

        The credentials endpoint is optional — on any failure the tool falls
        back to the host environment (host credentials).
        """
        if decision.approval_token:
            try:
                creds = self._client.get_temporary_credentials(tool, decision.approval_token)
                return build_execution_env(tool, creds)
            except Exception:
                pass
        return dict(os.environ)
