"""Authentication helpers for Outhora API.

Reads credentials from environment variables. The execution server
(server/main.py) loads these from server.env before handling requests.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class AuthError(Exception):
    """Raised when authentication configuration is missing or invalid."""


def _get_env(key: str, required: bool = True) -> str:
    """Read a value from an environment variable."""
    value = os.environ.get(key, "")
    if required and not value:
        raise AuthError(f"{key} environment variable is not set")
    return value


def get_api_url() -> str:
    return _get_env("OUTHORA_API_URL").rstrip("/")


def get_agent_id() -> str:
    return _get_env("OUTHORA_AGENT_ID")


def get_agent_secret() -> str:
    return _get_env("OUTHORA_AGENT_SECRET")


def get_dept_id() -> str:
    return _get_env("OUTHORA_DEPT_ID")


def get_user_id() -> str:
    return _get_env("OUTHORA_USER_ID", required=False) \
        or os.environ.get("USER", "unknown")


def get_session_id() -> str:
    return _get_env("OUTHORA_SESSION_ID", required=False)


def exchange_token() -> str:
    """Exchange agent credentials for a JWT via POST /v1/agent-auth."""
    url = f"{get_api_url()}/v1/agent-auth"
    payload = json.dumps({
        "agent_identifier": get_agent_id(),
        "agent_secret": get_agent_secret(),
        "dept_id": get_dept_id(),
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "outhora-agent-sdk/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("access_token", "")
            if not token:
                raise AuthError("No access_token returned from agent-auth endpoint")
            return token
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise AuthError(f"Agent auth failed (HTTP {e.code}): {body}") from e
    except urllib.error.URLError as e:
        raise AuthError(f"Agent auth network error: {e}") from e


def get_headers() -> dict[str, str]:
    """Exchange credentials for a JWT and return auth headers."""
    token = exchange_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "outhora-agent-sdk/1.0",
    }
