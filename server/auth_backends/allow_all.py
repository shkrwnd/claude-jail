"""AllowAll authorization backend — approves every action without any check.

Use this for local development, CI pipelines, or any environment where you
want the wrappers active (for logging/observability) but no approval gate.

Set AUTH_BACKEND=allow_all in server.env to activate.

WARNING: This backend provides zero security. Never use it in production.
"""

from __future__ import annotations

from server.auth_backends.base import AuthBackend, AuthDecision


class AllowAllBackend(AuthBackend):
    """Approves every action immediately. No network calls, no env vars required."""

    def authorize(
        self,
        tool: str,
        command: str,
        args: list[str],
        reason: str = "",
        repo: str = "",
        branch: str = "",
    ) -> AuthDecision:
        return AuthDecision(status="approved")
