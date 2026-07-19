"""Generic webhook authorization backend.

Sends a JSON payload to any HTTP endpoint and interprets the response as
an authorization decision. Compatible with any custom approval system,
internal policy service, or home-grown ACP.

Required env vars (when AUTH_BACKEND=webhook):
    AUTH_WEBHOOK_URL — Base URL of the approval service

Optional env vars:
    AUTH_WEBHOOK_TOKEN   — Bearer token for authentication
    AUTH_WEBHOOK_TIMEOUT — Request timeout in seconds (default: 30)
    AUTH_POLL_INTERVAL — Seconds between polls for pending decisions (default: 5)
    AUTH_POLL_TIMEOUT  — Total seconds to wait before giving up (default: 600)

Request format (POST {AUTH_WEBHOOK_URL}/authorize):
    {
        "tool":    "aws",
        "command": "aws s3 ls",
        "args":    ["s3", "ls"],
        "reason":  "listing buckets to find deployment artifacts",
        "repo":    "/workspace/myrepo",
        "branch":  "main"
    }

Expected response:
    { "status": "approved" }
    { "status": "denied",  "reason": "..." }
    { "status": "pending", "request_id": "...", "approver": "..." }

Poll format (GET {AUTH_WEBHOOK_URL}/authorize/{request_id}):
    Same response structure as above.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from server.auth_backends.base import AuthBackend, AuthDecision

_POLL_INTERVAL = int(os.environ.get("AUTH_POLL_INTERVAL", "5"))
_POLL_TIMEOUT = int(os.environ.get("AUTH_POLL_TIMEOUT", "600"))


class WebhookBackend(AuthBackend):
    """Authorization via a generic HTTP webhook.

    Implement any approval service by accepting the standard request format
    and returning {status, reason?, request_id?, approver?}.
    """

    def __init__(self) -> None:
        url = os.environ.get("AUTH_WEBHOOK_URL", "").rstrip("/")
        if not url:
            raise ValueError("AUTH_WEBHOOK_URL must be set when AUTH_BACKEND=webhook")
        self._base_url = url
        self._token = os.environ.get("AUTH_WEBHOOK_TOKEN", "")
        self._timeout = int(os.environ.get("AUTH_WEBHOOK_TIMEOUT", "30"))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "claude-jail/1.0"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Webhook error {e.code}: {body_text}") from e
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Webhook unreachable: {e}") from e

    def _get(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise RuntimeError(f"Webhook error {e.code}: {body_text}") from e
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Webhook unreachable: {e}") from e

    def authorize(
        self,
        tool: str,
        command: str,
        args: list[str],
        reason: str = "",
        repo: str = "",
        branch: str = "",
    ) -> AuthDecision:
        try:
            data = self._post("/authorize", {
                "tool": tool,
                "command": command,
                "args": args,
                "reason": reason,
                "repo": repo or os.getcwd(),
                "branch": branch,
            })
        except Exception as exc:
            return AuthDecision(status="denied", reason=f"Webhook error: {exc}")

        status = data.get("status", "denied")
        reason_out = data.get("reason", "")
        request_id = data.get("request_id", "")
        approver = data.get("approver", "")

        if status == "approved":
            return AuthDecision(status="approved", request_id=request_id)

        if status in ("denied", "rejected"):
            return AuthDecision(status="denied", reason=reason_out or "denied by webhook")

        if status == "pending":
            print("", file=sys.stderr)
            print(f"  Approval required (request_id={request_id}, approver={approver})", file=sys.stderr)
            print(f"  Review at: {self._base_url}/approvals/{request_id}", file=sys.stderr)
            print("  Waiting for approval (Ctrl+C to cancel)...", file=sys.stderr)
            print("", file=sys.stderr)

            deadline = time.time() + _POLL_TIMEOUT
            while time.time() < deadline:
                time.sleep(_POLL_INTERVAL)
                try:
                    poll = self._get(f"/authorize/{request_id}")
                except Exception as exc:
                    print(f"  Poll error: {exc}", file=sys.stderr)
                    continue

                poll_status = poll.get("status", "pending")
                if poll_status == "approved":
                    print("  Approved. Executing...", file=sys.stderr)
                    return AuthDecision(status="approved", request_id=request_id)
                if poll_status in ("denied", "rejected"):
                    return AuthDecision(
                        status="denied",
                        reason=poll.get("reason", "rejected by approver"),
                        request_id=request_id,
                    )
                print("  Still waiting...", file=sys.stderr)

            return AuthDecision(
                status="denied",
                reason=f"Approval timed out after {_POLL_TIMEOUT}s",
                request_id=request_id,
            )

        return AuthDecision(status="denied", reason=f"Unexpected status from webhook: {status}")
