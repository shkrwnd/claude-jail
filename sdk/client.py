"""Outhora ACP client — mirrors the acp_submit_action / acp_get_action_status contract."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from sdk.auth import exchange_token, get_api_url, get_dept_id, get_session_id, get_user_id
from sdk.models import ActionRequest, ActionResponse, ActionStatus, CredentialResponse

logger = logging.getLogger("outhora.client")


class OuthoraError(Exception):
    """Base exception for Outhora SDK errors."""


class ActionDenied(OuthoraError):
    """Raised when an action is rejected by policy."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(f"Action rejected: {reason}" if reason else "Action rejected by policy")


class ApprovalRequired(OuthoraError):
    """Raised when a human must approve before the action can proceed."""

    def __init__(self, request_id: str, approver: str = "") -> None:
        self.request_id = request_id
        self.approver = approver
        super().__init__(f"Approval required (request_id={request_id}, approver={approver})")


class OuthoraClient:
    """Client for the Outhora AI Control Plane."""

    def __init__(
        self,
        api_url: str | None = None,
        agent_id: str | None = None,
        agent_secret: str | None = None,
        dept_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        # api_key bypasses token exchange — for tests only
        api_key: str | None = None,
    ) -> None:
        self._api_url = (api_url or get_api_url()).rstrip("/")
        self._agent_id = agent_id
        self._agent_secret = agent_secret
        self._dept_id = dept_id or get_dept_id()
        self._user_id = user_id or get_user_id()
        self._session_id = session_id or get_session_id()
        self._api_key = api_key
        self._token: str | None = None

    def _get_token(self) -> str:
        if self._api_key:
            return self._api_key
        if not self._token:
            self._token = exchange_token()
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "User-Agent": "outhora-agent-sdk/1.0",
        }

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._api_url}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise OuthoraError(f"API error {e.code}: {body_text}") from e
        except (urllib.error.URLError, OSError) as e:
            raise OuthoraError(f"Network error: {e}") from e

    def health_check(self) -> bool:
        """Check connectivity to the Outhora API."""
        try:
            url = f"{self._api_url}/v1/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode()).get("status") == "ok"
        except Exception:
            return False

    def submit_action(self, action_type: str, context: dict[str, Any]) -> ActionResponse:
        """Submit an action for authorization.

        Returns ActionResponse with status: approved (+ approval_token) | rejected | pending (+ request_id).
        The approval_token expires in 15 minutes and can only be used once — store it immediately.
        """
        request = ActionRequest(action_type=action_type, context={
            "dept_id": self._dept_id,
            "user_id": self._user_id,
            "session_id": self._session_id,
            **context,
        })
        data = self._request("POST", "/v1/actions", request.to_dict())
        return ActionResponse.from_dict(data)

    def get_action_status(self, request_id: str) -> ActionResponse:
        """Poll the status of a pending action. Call after receiving status=pending."""
        data = self._request("GET", f"/v1/actions/{request_id}")
        return ActionResponse.from_dict(data)

    def get_temporary_credentials(self, tool: str, approval_token: str) -> CredentialResponse:
        """Fetch short-lived credentials using an approval token."""
        data = self._request("POST", "/v1/credentials", {
            "tool": tool,
            "approval_token": approval_token,
        })
        return CredentialResponse.from_dict(data)

    def execute_authorized(
        self,
        action_type: str,
        context: dict[str, Any],
    ) -> ActionResponse:
        """Submit action and handle the decision.

        Returns ActionResponse (with approval_token) if approved.
        Raises ActionDenied if rejected, ApprovalRequired if pending human approval.
        """
        resp = self.submit_action(action_type, context)

        if resp.status == ActionStatus.REJECTED:
            raise ActionDenied(resp.reason)

        if resp.status == ActionStatus.PENDING:
            raise ApprovalRequired(request_id=resp.request_id, approver=resp.approver)

        return resp
