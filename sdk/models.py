"""Typed models for Outhora ACP requests and responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING  = "pending"


@dataclass
class ActionRequest:
    action_type: str
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "context": self.context,
        }


@dataclass
class ActionResponse:
    status: ActionStatus
    request_id: str = ""
    approval_token: str = ""   # expires in 15 min, single use — store immediately on approval
    reason: str = ""
    approver: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionResponse:
        return cls(
            status=ActionStatus(data.get("status", "rejected")),
            request_id=data.get("request_id", ""),
            approval_token=data.get("approval_token", ""),
            reason=data.get("decision_reason", data.get("reason", "")),
            approver=data.get("approver", ""),
        )


@dataclass
class CredentialResponse:
    access_key: str
    secret_key: str
    session_token: str
    expires_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CredentialResponse:
        known = {"access_key", "secret_key", "session_token", "expires_at"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            access_key=data.get("access_key", ""),
            secret_key=data.get("secret_key", ""),
            session_token=data.get("session_token", ""),
            expires_at=data.get("expires_at", ""),
            extra=extra,
        )
