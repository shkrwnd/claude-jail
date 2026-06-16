"""Main Outhora SDK client."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from sdk.auth import (
    exchange_token,
    get_agent_id,
    get_agent_secret,
    get_api_url,
    get_dept_id,
    get_session_id,
    get_user_id,
)
from sdk.models import ActionRequest, ActionResponse, ActionStatus

logger = logging.getLogger("outhora.client")


class OuthoraError(Exception):
    """Base exception for Outhora SDK errors."""


class ActionDenied(OuthoraError):
    """Raised when an action is denied by policy."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(f"Action denied: {reason}" if reason else "Action denied by policy")


class ApprovalRequired(OuthoraError):
    """Raised when an action requires human approval."""

    def __init__(self, request_id: str, approver: str = "", reason: str = "") -> None:
        self.request_id = request_id
        self.approver = approver
        self.reason = reason
        super().__init__(f"Approval required from {approver}. Reason: {reason}")


class OuthoraClient:
    """Client for the Outhora authorization API."""

    def __init__(
        self,
        api_url: str | None = None,
        agent_id: str | None = None,
        agent_secret: str | None = None,
        dept_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._api_url = api_url or get_api_url()
        self._agent_id = agent_id or get_agent_id()
        self._agent_secret = agent_secret or get_agent_secret()
        self._dept_id = dept_id or get_dept_id()
        self._user_id = user_id or get_user_id()
        self._session_id = session_id or get_session_id()
        self._token: str | None = None

    def _get_token(self) -> str:
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
        """Check connectivity to Outhora API (no auth required)."""
        try:
            url = f"{self._api_url}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                return result.get("status") == "ok"
        except Exception:
            return False

    def submit_action(
        self,
        tool: str,
        command: str,
        action_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionResponse:
        """Submit an action for authorization.

        action_type defaults to '{tool}_{first_subcommand}', e.g. 'git_push'.
        """
        if not action_type:
            parts = command.split()
            subcommand = next((p for p in parts[1:] if not p.startswith("-")), "")
            action_type = f"{tool}_{subcommand}" if subcommand else tool

        request = ActionRequest(
            action_type=action_type,
            context={
                "tool": tool,
                "command": command,
                "agent_id": self._agent_id,
                "dept_id": self._dept_id,
                "user_id": self._user_id,
                "session_id": self._session_id,
                **(metadata or {}),
            },
        )
        data = self._request("POST", "/v1/actions", request.to_dict())
        return ActionResponse.from_dict(data)

    def get_action_status(self, request_id: str) -> ActionResponse:
        """Poll the status of a pending action."""
        data = self._request("GET", f"/v1/actions/{request_id}")
        return ActionResponse.from_dict(data)

    def execute_authorized(
        self,
        tool: str,
        command: str,
        action_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActionResponse:
        """Submit action and handle the decision.

        Returns the ActionResponse (with approval_token if approved).
        Raises ActionDenied or ApprovalRequired as appropriate.
        """
        response = self.submit_action(tool, command, action_type, metadata)

        if response.status in (ActionStatus.DENIED, ActionStatus.REJECTED):
            raise ActionDenied(response.reason)

        if response.status == ActionStatus.PENDING:
            raise ApprovalRequired(
                request_id=response.request_id,
                approver=response.approver,
                reason=response.reason,
            )

        return response
