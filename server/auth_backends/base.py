"""Abstract authorization backend interface (host-side).

An AuthBackend decides whether a tool command may execute, and optionally
provides the environment (e.g. temporary credentials) to execute it with.
Backends run only on the host — the container never sees them.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AuthDecision:
    """Result of an authorization request.

    status values:
        "approved" — action is allowed, proceed with execution
        "denied"   — action is blocked, do not execute
        "pending"  — still waiting for human approval. The handler does not
                     execute, but tells the agent an approval is in flight so
                     it can inform the user and retry (unlike a denial).
    """

    status: str
    reason: str = ""
    request_id: str = ""
    approver: str = ""
    approval_token: str = ""  # single-use token for credential issuance (if the backend supports it)


class AuthBackend(ABC):
    """Authorization middleware — decides whether a tool command may execute.

    Implementations that need human approval should poll toward a terminal
    decision (approved/denied) and return "pending" if the poll window
    expires first. Only "approved" executes.

    Example minimal implementation::

        class MyBackend(AuthBackend):
            def authorize(self, tool, command, args, reason="", repo="", branch="") -> AuthDecision:
                ok = my_policy_check(tool, command)
                return AuthDecision(status="approved" if ok else "denied")
    """

    # Env vars this backend needs (no defaults — typically secrets set in
    # deploy/server.env). Checked at server startup; missing vars abort boot
    # with a clear error instead of failing on the first request.
    required_env: tuple[str, ...] = ()

    @abstractmethod
    def authorize(
        self,
        tool: str,
        command: str,
        args: list[str],
        reason: str = "",
        repo: str = "",
        branch: str = "",
    ) -> AuthDecision:
        """Authorize a tool invocation.

        Args:
            tool:    Tool name (e.g. "aws", "git", "kubectl").
            command: Full command string for context (e.g. "aws s3 ls").
            args:    Raw argument list (sys.argv[1:] from the wrapper).
            reason:  Human-readable intent set by the agent via AGENT_REASON.
            repo:    Container working directory (context for the approver).
            branch:  Git branch in the container (context for the approver).

        Returns:
            AuthDecision. "approved" executes; "denied" returns the reason as
            an error; "pending" (e.g. after a poll window expires without a
            human decision) returns a retryable "approval waiting" message.
        """
        ...

    def execution_env(self, tool: str, decision: AuthDecision) -> dict[str, str]:
        """Return the environment to execute the approved command with.

        Default: the host environment as-is. Backends that issue temporary
        credentials (e.g. Outhora) override this to inject them.
        """
        return dict(os.environ)
