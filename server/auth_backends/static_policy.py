"""Static allow/deny-list backend — the worked example from the README.

Demonstrates the minimal custom backend: prefix-match the command string
against a deny list first, then an allow list, and fail closed on anything
unlisted. Select it in deploy/server.env:

    AUTH_BACKEND=server.auth_backends.static_policy.StaticPolicyBackend

Prefix matching is a demonstration, not a security boundary — e.g.
"git -c core.sshCommand=... push" doesn't start with "git push", and
"kubectl get" also matches "kubectl get secrets". Robust policies must
inspect `args`, not just the command string.
"""

from __future__ import annotations

from server.auth_backends.base import AuthBackend, AuthDecision

ALLOWED = (
    "git status", "git log", "git diff", "git commit",
    "aws s3 ls",
    "kubectl get",
)
DENIED = (
    "git push --force",
    "terraform destroy",
    "kubectl delete",
)


class StaticPolicyBackend(AuthBackend):
    def authorize(self, tool, command, args, reason="", repo="", branch="") -> AuthDecision:
        if any(command.startswith(prefix) for prefix in DENIED):
            return AuthDecision(status="denied", reason=f"{command!r} is on the deny list")
        if any(command.startswith(prefix) for prefix in ALLOWED):
            return AuthDecision(status="approved")
        return AuthDecision(status="denied", reason=f"{command!r} is not on the allow list")
