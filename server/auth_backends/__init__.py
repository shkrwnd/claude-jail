"""Authorization backend registry and factory (host-side).

Backend selection:
  AUTH_BACKEND env var (set in deploy/server.env) selects which backend the
  execution server uses. The container has no say in this — it only speaks
  the execution protocol.

Available built-in backends:
    allow_all   — Approve everything, no network calls (dev/CI; default)
    webhook     — Generic HTTP webhook (custom approval service)
    outhora     — Outhora ACP (approval workflows + temp credentials)

Custom backends need no registry edit — set AUTH_BACKEND to a dotted path::

    AUTH_BACKEND=mypackage.mymodule.MyBackend

(Any value containing a "." is treated as a dotted import path; the module
must be importable by the server, e.g. on PYTHONPATH.)
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


def get_backend_class(name: str | None = None) -> Type[AuthBackend]:
    """Resolve the selected backend to its class without instantiating it.

    Selection order:
      1. explicit `name` argument (testing only)
      2. AUTH_BACKEND env var (set in server.env)
      3. "allow_all" default

    The value is either a registry key ("allow_all", "webhook", ...) or a
    dotted import path ("mypackage.mymodule.MyBackend").
    """
    backend_name = name or os.environ.get("AUTH_BACKEND") or _DEFAULT
    dotted_path = REGISTRY.get(backend_name)
    if dotted_path is None and "." in backend_name:
        dotted_path = backend_name  # treat as a direct dotted import path
    if dotted_path is None:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(
            f"Unknown auth backend '{backend_name}'. "
            f"Known backends: {known}. "
            f"For a custom backend, set AUTH_BACKEND to its dotted path, "
            f"e.g. 'mypackage.mymodule.MyBackend'."
        )
    module_path, class_name = dotted_path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(
            f"Cannot import auth backend '{backend_name}': {exc}"
        ) from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(
            f"Auth backend module '{module_path}' has no class '{class_name}'."
        )
    if not (isinstance(cls, type) and issubclass(cls, AuthBackend)):
        raise ValueError(
            f"Auth backend '{backend_name}' resolved to {cls!r}, "
            f"which is not an AuthBackend subclass."
        )
    return cls


def get_auth_backend(name: str | None = None) -> AuthBackend:
    """Instantiate and return the selected authorization backend."""
    return get_backend_class(name)()


__all__ = [
    "AuthBackend", "AuthDecision",
    "REGISTRY", "get_auth_backend", "get_backend_class",
]
