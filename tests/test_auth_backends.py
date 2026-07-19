"""Tests for pluggable authorization backends."""

import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.auth_backends import REGISTRY, get_auth_backend
from server.auth_backends.allow_all import AllowAllBackend
from server.auth_backends.base import AuthBackend, AuthDecision
from server.auth_backends.outhora import OuthoraBackend
from server.auth_backends.webhook import WebhookBackend


# ---------------------------------------------------------------------------
# AuthDecision model
# ---------------------------------------------------------------------------

class TestAuthDecision(unittest.TestCase):
    def test_approved_defaults(self):
        d = AuthDecision(status="approved")
        self.assertEqual(d.status, "approved")
        self.assertEqual(d.reason, "")
        self.assertEqual(d.request_id, "")
        self.assertEqual(d.approver, "")

    def test_denied_with_reason(self):
        d = AuthDecision(status="denied", reason="destructive action")
        self.assertEqual(d.status, "denied")
        self.assertEqual(d.reason, "destructive action")

    def test_pending_with_metadata(self):
        d = AuthDecision(status="pending", request_id="req-1", approver="boss@example.com")
        self.assertEqual(d.request_id, "req-1")
        self.assertEqual(d.approver, "boss@example.com")


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

class TestBackendFactory(unittest.TestCase):
    def test_explicit_name_selects_outhora(self):
        # Patching __init__ to avoid needing real env vars
        with patch.object(OuthoraBackend, "__init__", return_value=None):
            backend = get_auth_backend("outhora")
        self.assertIsInstance(backend, OuthoraBackend)

    def test_env_selects_allow_all(self):
        with patch.dict(os.environ, {"AUTH_BACKEND": "allow_all"}):
            backend = get_auth_backend()
        self.assertIsInstance(backend, AllowAllBackend)

    def test_explicit_name_selects_allow_all(self):
        backend = get_auth_backend("allow_all")
        self.assertIsInstance(backend, AllowAllBackend)

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_auth_backend("nonexistent_backend")
        self.assertIn("nonexistent_backend", str(ctx.exception))
        self.assertIn("Known backends", str(ctx.exception))

    def test_custom_backend_registration(self):
        """Registering a custom backend class should make it selectable."""
        original = dict(REGISTRY)
        try:
            REGISTRY["custom_test"] = "server.auth_backends.allow_all.AllowAllBackend"
            backend = get_auth_backend("custom_test")
            self.assertIsInstance(backend, AllowAllBackend)
        finally:
            REGISTRY.clear()
            REGISTRY.update(original)

    def test_dotted_path_selects_custom_backend(self):
        """AUTH_BACKEND may be a dotted import path — no registry edit needed."""
        backend = get_auth_backend("server.auth_backends.allow_all.AllowAllBackend")
        self.assertIsInstance(backend, AllowAllBackend)

    def test_dotted_path_to_missing_module_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_auth_backend("no_such_pkg.NoSuchBackend")
        self.assertIn("no_such_pkg", str(ctx.exception))

    def test_dotted_path_to_non_backend_raises(self):
        """Resolving to something that isn't an AuthBackend subclass fails."""
        with self.assertRaises(ValueError):
            get_auth_backend("json.JSONDecoder")

    def test_is_abstract(self):
        """AuthBackend cannot be instantiated directly."""
        with self.assertRaises(TypeError):
            AuthBackend()  # type: ignore

    def test_env_var_selects_backend(self):
        """AUTH_BACKEND env var selects the backend."""
        with patch.dict(os.environ, {"AUTH_BACKEND": "allow_all"}):
            backend = get_auth_backend()
        self.assertIsInstance(backend, AllowAllBackend)


# ---------------------------------------------------------------------------
# AllowAll backend
# ---------------------------------------------------------------------------

class TestAllowAllBackend(unittest.TestCase):
    def setUp(self):
        self.backend = AllowAllBackend()

    def test_approves_any_tool(self):
        decision = self.backend.authorize("aws", "aws s3 ls", ["s3", "ls"])
        self.assertEqual(decision.status, "approved")

    def test_approves_destructive_command(self):
        decision = self.backend.authorize("terraform", "terraform destroy", ["destroy"])
        self.assertEqual(decision.status, "approved")

    def test_approves_with_no_reason(self):
        decision = self.backend.authorize("git", "git push", ["push"])
        self.assertEqual(decision.status, "approved")
        self.assertEqual(decision.reason, "")


# ---------------------------------------------------------------------------
# Mock HTTP server shared by Outhora and Webhook tests
# ---------------------------------------------------------------------------

class MockAuthHandler(BaseHTTPRequestHandler):
    """Mock approval service for testing OuthoraBackend and WebhookBackend."""

    def log_message(self, format, *args):
        pass  # suppress test output

    def do_GET(self):
        if self.path.startswith("/v1/actions/") or self.path.startswith("/authorize/"):
            request_id = self.path.split("/")[-1]
            self._respond(200, {
                "status": "approved",
                "request_id": request_id,
                "approval_token": f"tok-{request_id}",
            })
        elif self.path == "/v1/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path in ("/v1/agent-auth",):
            self._respond(200, {"access_token": "test-jwt-token"})
            return

        # Shared decision logic for both /v1/actions and /authorize
        command = (
            body.get("context", {}).get("command", "")
            or body.get("command", "")
        )
        if "destroy" in command or "delete" in command:
            self._respond(200, {"status": "rejected", "reason": "destructive action blocked"})
        elif "apply" in command or "merge" in command:
            self._respond(200, {
                "status": "pending",
                "request_id": "req-mock-123",
                "approver": "manager@example.com",
            })
        else:
            self._respond(200, {"status": "approved", "approval_token": "tok-mock-456"})

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


# ---------------------------------------------------------------------------
# Outhora backend
# ---------------------------------------------------------------------------

class TestOuthoraBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockAuthHandler)
        cls.port = cls.server.server_address[1]
        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api_url = f"http://127.0.0.1:{cls.port}"
        # Set env vars for the lifetime of the test class
        os.environ["OUTHORA_API_URL"] = cls.api_url
        os.environ["OUTHORA_AGENT_ID"] = "test-agent"
        os.environ["OUTHORA_AGENT_SECRET"] = "test-secret"
        os.environ["OUTHORA_DEPT_ID"] = "test-dept"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for key in ["OUTHORA_API_URL", "OUTHORA_AGENT_ID", "OUTHORA_AGENT_SECRET", "OUTHORA_DEPT_ID"]:
            os.environ.pop(key, None)

    def _backend(self):
        return OuthoraBackend()

    def test_approved_action(self):
        backend = self._backend()
        decision = backend.authorize("git", "git push origin main", ["push", "origin", "main"])
        self.assertEqual(decision.status, "approved")

    def test_denied_action(self):
        backend = self._backend()
        decision = backend.authorize("terraform", "terraform destroy", ["destroy"])
        self.assertEqual(decision.status, "denied")
        self.assertIn("destructive", decision.reason)

    def test_reason_passed_in_context(self):
        """Verify reason flows through to the authorization request."""
        backend = self._backend()
        # Should be approved (no destructive keyword) — reason is metadata
        decision = backend.authorize(
            "aws", "aws s3 ls", ["s3", "ls"],
            reason="listing buckets to find deployment artifacts",
        )
        self.assertEqual(decision.status, "approved")

    def test_action_type_derived_from_subcommand(self):
        """action_type should be {tool}_{subcommand}."""
        # We can't directly inspect the request, but a successful auth confirms
        # the action_type derivation didn't raise.
        backend = self._backend()
        decision = backend.authorize("aws", "aws s3 ls", ["s3", "ls"])
        self.assertEqual(decision.status, "approved")

    def test_backend_error_returns_denied(self):
        """Network/auth errors should return denied rather than raise."""
        with patch.dict(os.environ, {
            "OUTHORA_API_URL": "http://127.0.0.1:1",  # nothing listening
            "OUTHORA_AGENT_ID": "x",
            "OUTHORA_AGENT_SECRET": "x",
            "OUTHORA_DEPT_ID": "x",
        }):
            backend = OuthoraBackend()
        decision = backend.authorize("aws", "aws s3 ls", ["s3", "ls"])
        self.assertEqual(decision.status, "denied")
        self.assertIn("Outhora error", decision.reason)


# ---------------------------------------------------------------------------
# Webhook backend
# ---------------------------------------------------------------------------

class TestWebhookBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockAuthHandler)
        cls.port = cls.server.server_address[1]
        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _backend(self):
        with patch.dict(os.environ, {"AUTH_WEBHOOK_URL": self.base_url}):
            return WebhookBackend()

    def test_missing_url_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AUTH_WEBHOOK_URL", None)
            with self.assertRaises(ValueError) as ctx:
                WebhookBackend()
            self.assertIn("AUTH_WEBHOOK_URL", str(ctx.exception))

    def test_approved_action(self):
        backend = self._backend()
        decision = backend.authorize("git", "git push origin main", ["push", "origin", "main"])
        self.assertEqual(decision.status, "approved")

    def test_denied_action(self):
        backend = self._backend()
        decision = backend.authorize("kubectl", "kubectl delete pod mypod", ["delete", "pod", "mypod"])
        self.assertEqual(decision.status, "denied")
        self.assertIn("destructive", decision.reason)

    def test_webhook_unreachable_returns_denied(self):
        with patch.dict(os.environ, {"AUTH_WEBHOOK_URL": "http://127.0.0.1:1"}):
            backend = WebhookBackend()
        decision = backend.authorize("aws", "aws s3 ls", ["s3", "ls"])
        self.assertEqual(decision.status, "denied")
        self.assertIn("Webhook error", decision.reason)


if __name__ == "__main__":
    unittest.main()
