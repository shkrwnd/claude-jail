"""Typed models for Outhora API requests and responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REJECTED = "rejected"


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
    request_id: str
    status: ActionStatus
    reason: str = ""
    approver: str = ""
    approved_by: str = ""
    approval_token: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionResponse:
        return cls(
            request_id=data.get("request_id", ""),
            status=ActionStatus(data.get("status", "pending")),
            reason=data.get("decision_reason", data.get("reason", "")),
            approver=data.get("approver", ""),
            approved_by=data.get("approved_by", ""),
            approval_token=data.get("approval_token", ""),
            receipt=data.get("receipt", {}),
        )
