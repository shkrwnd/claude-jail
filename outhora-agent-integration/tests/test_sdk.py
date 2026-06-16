"""Tests for Outhora SDK."""

import json
import os
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from unittest.mock import patch

# Add parent to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sdk.models import (
    AuthorizationRequest,
    AuthorizationResponse,
    AuditEvent,
    CredentialResponse,
    Decision,
)
from sdk.client import OuthoraClient, AuthorizationDenied, ApprovalRequired
from sdk.credentials import build_execution_env


class TestModels(unittest.TestCase):
    def test_authorization_request_to_dict(self):
        req = AuthorizationRequest(
            tool="aws",
            command="aws s3 ls",
            user_id="dev-1",
            agent_session_id="sess-1",
            repo="/workspace",
            branch="main",
        )
        d = req.to_dict()
        self.assertEqual(d["tool"], "aws")
        self.assertEqual(d["command"], "aws s3 ls")
        self.assertEqual(d["user_id"], "dev-1")

    def test_authorization_request_minimal(self):
        req = AuthorizationRequest(
            tool="gh", command="gh pr view", user_id="dev-1", agent_session_id="sess-1"
        )
        d = req.to_dict()
        self.assertNotIn("repo", d)
        self.assertNotIn("branch", d)

    def test_authorization_response_allow(self):
        resp = AuthorizationResponse.from_dict({
            "decision": "allow",
            "action_id": "act-123",
        })
        self.assertEqual(resp.decision, Decision.ALLOW)
        self.assertEqual(resp.action_id, "act-123")

    def test_authorization_response_deny(self):
        resp = AuthorizationResponse.from_dict({
            "decision": "deny",
            "reason": "policy violation",
        })
        self.assertEqual(resp.decision, Decision.DENY)
        self.assertEqual(resp.reason, "policy violation")

    def test_authorization_response_approval_required(self):
        resp = AuthorizationResponse.from_dict({
            "decision": "approval_required",
            "approval_id": "apr-456",
        })
        self.assertEqual(resp.decision, Decision.APPROVAL_REQUIRED)
        self.assertEqual(resp.approval_id, "apr-456")

    def test_credential_response(self):
        creds = CredentialResponse.from_dict({
            "access_key": "AKIA...",
            "secret_key": "secret",
            "session_token": "token",
            "expires_at": "2026-01-01T00:00:00Z",
            "gh_token": "ghp_xxx",
        })
        self.assertEqual(creds.access_key, "AKIA...")
        self.assertEqual(creds.extra["gh_token"], "ghp_xxx")

    def test_audit_event_auto_timestamp(self):
        event = AuditEvent(
            tool="aws",
            command="aws s3 ls",
            decision="allow",
            agent_session_id="sess-1",
            user_id="dev-1",
        )
        self.assertTrue(event.timestamp.endswith("Z"))

    def test_audit_event_to_dict(self):
        event = AuditEvent(
            tool="kubectl",
            command="kubectl get pods",
            decision="allow",
            agent_session_id="sess-1",
            user_id="dev-1",
            exit_code=0,
        )
        d = event.to_dict()
        self.assertEqual(d["tool"], "kubectl")
        self.assertEqual(d["exit_code"], 0)


class TestCredentialInjection(unittest.TestCase):
    def test_build_aws_env(self):
        creds = CredentialResponse(
            access_key="AKIA_TEST",
            secret_key="SECRET_TEST",
            session_token="TOKEN_TEST",
            expires_at="2026-01-01T00:00:00Z",
        )
        env = build_execution_env("aws", creds)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA_TEST")
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "SECRET_TEST")
        self.assertEqual(env["AWS_SESSION_TOKEN"], "TOKEN_TEST")

    def test_build_gh_env(self):
        creds = CredentialResponse(
            access_key="ghp_token",
            secret_key="",
            session_token="",
            expires_at="2026-01-01T00:00:00Z",
            extra={"gh_token": "ghp_real_token"},
        )
        env = build_execution_env("gh", creds)
        self.assertEqual(env["GH_TOKEN"], "ghp_real_token")

    def test_build_psql_env(self):
        creds = CredentialResponse(
            access_key="",
            secret_key="",
            session_token="",
            expires_at="2026-01-01T00:00:00Z",
            extra={
                "pgpassword": "dbpass",
                "pguser": "dbuser",
                "pghost": "db.example.com",
                "pgdatabase": "mydb",
            },
        )
        env = build_execution_env("psql", creds)
        self.assertEqual(env["PGPASSWORD"], "dbpass")
        self.assertEqual(env["PGUSER"], "dbuser")


class MockOuthoraHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for testing the SDK client."""

    def log_message(self, format, *args):
        pass  # Suppress log output during tests

    def do_GET(self):
        if self.path == "/api/v1/health":
            self._respond(200, {"status": "ok"})
        elif self.path.startswith("/api/v1/approvals/"):
            self._respond(200, {
                "approval_id": self.path.split("/")[-1],
                "status": "pending",
            })
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if self.path == "/api/v1/authorize":
            command = body.get("command", "")
            if "destroy" in command or "delete" in command:
                self._respond(200, {"decision": "deny", "reason": "destructive action blocked"})
            elif "apply" in command or "merge" in command:
                self._respond(200, {"decision": "approval_required", "approval_id": "apr-test-123"})
            else:
                self._respond(200, {"decision": "allow", "action_id": "act-test-456"})
        elif self.path == "/api/v1/credentials":
            self._respond(200, {
                "access_key": "AKIA_TEMP",
                "secret_key": "SECRET_TEMP",
                "session_token": "TOKEN_TEMP",
                "expires_at": "2026-01-01T00:00:00Z",
            })
        elif self.path == "/api/v1/audit":
            self._respond(202, {"accepted": True})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class TestClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockOuthoraHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.api_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _client(self):
        return OuthoraClient(
            api_url=self.api_url,
            api_key="test-key",
            user_id="test-user",
            session_id="test-session",
        )

    def test_health_check(self):
        client = self._client()
        self.assertTrue(client.health_check())

    def test_authorize_allow(self):
        client = self._client()
        resp = client.authorize_action("aws", "aws s3 ls")
        self.assertEqual(resp.decision, Decision.ALLOW)
        self.assertEqual(resp.action_id, "act-test-456")

    def test_authorize_deny(self):
        client = self._client()
        resp = client.authorize_action("terraform", "terraform destroy")
        self.assertEqual(resp.decision, Decision.DENY)
        self.assertIn("destructive", resp.reason)

    def test_authorize_approval_required(self):
        client = self._client()
        resp = client.authorize_action("terraform", "terraform apply")
        self.assertEqual(resp.decision, Decision.APPROVAL_REQUIRED)
        self.assertEqual(resp.approval_id, "apr-test-123")

    def test_get_credentials(self):
        client = self._client()
        creds = client.get_temporary_credentials("aws", "act-test-456")
        self.assertEqual(creds.access_key, "AKIA_TEMP")
        self.assertEqual(creds.session_token, "TOKEN_TEMP")

    def test_execute_authorized_allow(self):
        client = self._client()
        auth_resp, creds = client.execute_authorized("aws", "aws s3 ls")
        self.assertEqual(auth_resp.decision, Decision.ALLOW)
        self.assertIsNotNone(creds)
        self.assertEqual(creds.access_key, "AKIA_TEMP")

    def test_execute_authorized_deny(self):
        client = self._client()
        with self.assertRaises(AuthorizationDenied):
            client.execute_authorized("terraform", "terraform destroy")

    def test_execute_authorized_approval(self):
        client = self._client()
        with self.assertRaises(ApprovalRequired) as ctx:
            client.execute_authorized("gh", "gh pr merge")
        self.assertEqual(ctx.exception.approval_id, "apr-test-123")

    def test_request_approval_status(self):
        client = self._client()
        status = client.request_approval("apr-test-123")
        self.assertEqual(status.status, "pending")

    def test_record_audit(self):
        client = self._client()
        event = AuditEvent(
            tool="aws",
            command="aws s3 ls",
            decision="allow",
            agent_session_id="test-session",
            user_id="test-user",
        )
        with patch.dict(os.environ, {
            "OUTHORA_API_URL": self.api_url,
            "OUTHORA_API_KEY": "test-key",
        }):
            result = client.record_audit_event(event)
            self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
