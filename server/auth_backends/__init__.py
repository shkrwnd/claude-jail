"""Authorization backend registry and factory (host-side).

Backend selection:
  AUTH_BACKEND env var (set in deploy/server.env) selects which backend the
  execution server uses. The container has no say in this — it only speaks
  the execution protocol.

Available built-in backends:
    outhora     — Outhora ACP (production default)
    allow_all   — Approve everything, no network calls (dev/CI)
    webhook     — Generic HTTP webhook (custom approval service)

Custom backends: add an entry to REGISTRY pointing to "module.ClassName"::

    from server.auth_backends import REGISTRY
    REGISTRY["my_backend"] = "mypackage.mymodule.MyBackend"
"""

from __future__ import annotations

import importlib
import os
from typing import Type

from server.auth_backends.base import AuthBackend, AuthDecision

# Maps backend name → "module.ClassName" (lazy-imported to avoid loading
# dependencies for backends that are not selected).
REGISTRY: dict[str, str] = {
    "outhora":   "server.auth_backends.outhora.OuthoraBackend",
    "allow_all": "server.auth_backends.allow_all.AllowAllBackend",
    "webhook":   "server.auth_backends.webhook.WebhookBackend",
}

_DEFAULT = "allow_all"


def get_auth_backend(name: str | None = None) -> AuthBackend:
    """Instantiate and return the selected authorization backend.

    Selection order:
      1. explicit `name` argument (testing only)
      2. AUTH_BACKEND env var (set in server.env)
      3. "outhora" default
    """
    backend_name = name or os.environ.get("AUTH_BACKEND", _DEFAULT)
    dotted_path = REGISTRY.get(backend_name)
    if dotted_path is None:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(
            f"Unknown auth backend '{backend_name}'. "
            f"Known backends: {known}. "
            f"Register custom backends via server.auth_backends.REGISTRY."
        )
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls: Type[AuthBackend] = getattr(module, class_name)
    return cls()


__all__ = [
    "AuthBackend", "AuthDecision",
    "REGISTRY", "get_auth_backend",
]
